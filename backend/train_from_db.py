#!/usr/bin/env python3
"""
Train ML model from PostgreSQL data collected by ml_collector.
Run this locally or via Render shell after enough data is collected.
"""
import os
import sys
import json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, accuracy_score, classification_report

try:
    import psycopg2
    import psycopg2.extras
    import lightgbm as lgb
    import onnxmltools
    from onnxmltools.convert import convert_lightgbm
    from skl2onnx.common.data_types import FloatTensorType
except ImportError:
    print("Installing required packages...")
    import subprocess
    subprocess.check_call([
        "pip", "install", "psycopg2-binary", "pandas", "numpy",
        "lightgbm", "onnxmltools", "skl2onnx", "scikit-learn"
    ])
    import psycopg2
    import psycopg2.extras
    import lightgbm as lgb
    import onnxmltools
    from onnxmltools.convert import convert_lightgbm
    from skl2onnx.common.data_types import FloatTensorType

DATABASE_URL = os.getenv("DATABASE_URL")

def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    try:
        return psycopg2.connect(DATABASE_URL, sslmode="require")
    except Exception:
        return psycopg2.connect(DATABASE_URL)

def load_training_data(min_samples=200):
    """Load all ml_features from DB."""
    with connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT COUNT(*) FROM ml_features")
            count = cur.fetchone()[0]
            print(f"Found {count} samples in database")
            
            if count < min_samples:
                print(f"ERROR: Need at least {min_samples} samples, only have {count}")
                print("Let ml_collector run longer (needs ~1-2 days minimum)")
                return None
            
            cur.execute("""
                SELECT 
                    ts, price_now, dxy_z, real_z, vix_z, risk_z, nom_z, do_ctx, mom,
                    range_to_atr20, activity, vol_pct, spread_pts, news_lock,
                    gap_pct, pct24h,
                    sess_asia, sess_london, sess_newyork, sess_off,
                    q_ok, q_degraded, q_poor,
                    dxyz_fresh, realz_fresh, vixz_fresh, risk_on_fresh,
                    nominalz_fresh, do_ctx_fresh, mom_fresh
                FROM ml_features
                ORDER BY ts ASC
            """)
            rows = cur.fetchall()
            return pd.DataFrame([dict(r) for r in rows])

def label_data(df, horizon_rows=12):
    """Create labels: did price go up in next N rows?"""
    df = df.sort_values("ts").reset_index(drop=True)
    
    targets = []
    for i in range(len(df)):
        if i + horizon_rows >= len(df):
            targets.append(None)
        else:
            current = df.loc[i, "price_now"]
            future = df.loc[i + horizon_rows, "price_now"]
            targets.append(1 if future > current else 0)
    
    df["target"] = targets
    df = df.dropna(subset=["target"])
    return df

def train_model(horizon_rows=12):
    """Train and export ONNX model from database."""
    
    print("=" * 70)
    print("SNIPERFLOW ML TRAINING FROM RENDER DATABASE")
    print("=" * 70)
    
    # Load
    df = load_training_data(min_samples=200)
    if df is None:
        return
    
    # Label
    print(f"\nLabeling data (horizon={horizon_rows} rows = {horizon_rows * 5}min)...")
    df = label_data(df, horizon_rows)
    print(f"  {len(df)} labeled samples")
    
    if len(df) < 100:
        print("ERROR: After labeling, need at least 100 samples!")
        return
    
    # Features (exclude metadata)
    feature_cols = [
        "dxy_z", "real_z", "vix_z", "risk_z", "nom_z", "do_ctx", "mom",
        "range_to_atr20", "activity", "vol_pct", "spread_pts", "news_lock",
        "gap_pct", "pct24h",
        "sess_asia", "sess_london", "sess_newyork", "sess_off",
        "q_ok", "q_degraded", "q_poor",
        "dxyz_fresh", "realz_fresh", "vixz_fresh", "risk_on_fresh",
        "nominalz_fresh", "do_ctx_fresh", "mom_fresh"
    ]
    
    # Ensure all exist
    for col in feature_cols:
        if col not in df.columns:
            df[col] = 0.0
    
    X = df[feature_cols].fillna(0.0)
    y = df["target"].astype(int)
    
    print(f"\nFeature matrix: {X.shape}")
    print(f"Target distribution: {dict(y.value_counts())}")
    
    # Check balance
    balance = y.mean()
    if balance < 0.3 or balance > 0.7:
        print(f"⚠️  WARNING: Imbalanced dataset (bull%={balance*100:.1f}%)")
        print("   Consider collecting more diverse market conditions")
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    # Train
    print("\n🚀 Training LightGBM...")
    model = lgb.LGBMClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbose=-1
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)])
    
    # Evaluate
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    
    print("\n" + "=" * 70)
    print("MODEL PERFORMANCE")
    print("=" * 70)
    print(f"Accuracy:  {accuracy_score(y_test, y_pred):.3f}")
    print(f"ROC AUC:   {roc_auc_score(y_test, y_proba):.3f}")
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=["DOWN", "UP"]))
    
    # Feature importance
    print("\n📊 Top 15 Features:")
    importances = sorted(
        zip(feature_cols, model.feature_importances_),
        key=lambda x: x[1],
        reverse=True
    )
    for feat, imp in importances[:15]:
        print(f"  {feat:25s} {imp:8.1f}")
    
    # Convert to ONNX
    print("\n🔄 Converting to ONNX...")
    initial_type = [('float_input', FloatTensorType([None, len(feature_cols)]))]
    onnx_model = convert_lightgbm(model, initial_types=initial_type, target_opset=12)
    
    # Save
    output_dir = Path("models")
    output_dir.mkdir(exist_ok=True, parents=True)
    
    model_path = output_dir / "xau_nowcast_lgb.onnx"
    with open(model_path, "wb") as f:
        f.write(onnx_model.SerializeToString())
    
    # Save feature order
    feature_order_path = output_dir / "feature_order.json"
    with open(feature_order_path, "w") as f:
        json.dump(feature_cols, f, indent=2)
    
    print(f"\n✅ SUCCESS!")
    print(f"   Model:    {model_path}")
    print(f"   Features: {feature_order_path}")
    print(f"   Samples:  {len(X_train)} train / {len(X_test)} test")
    print(f"\n📦 Next steps:")
    print(f"   1. Review performance above")
    print(f"   2. Optional: python train_platt_from_db.py")
    print(f"   3. Commit models/ to git (or upload to Render)")
    print(f"   4. Redeploy → backend will auto-use new model")

if __name__ == "__main__":
    horizon = int(sys.argv[1]) if len(sys.argv) > 1 else 12  # 12 rows = 1 hour
    train_model(horizon)

