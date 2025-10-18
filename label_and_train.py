#!/usr/bin/env python3
"""
Label collected data and train a real LightGBM model.
Usage:
  1) Run collect_training_data.py for several days
  2) Run this script to label + train
  3) Outputs models/xau_nowcast_lgb.onnx
"""
import pandas as pd
import numpy as np
import json
from pathlib import Path
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, accuracy_score, classification_report

try:
    import lightgbm as lgb
    import onnxmltools
    from onnxmltools.convert import convert_lightgbm
    from skl2onnx.common.data_types import FloatTensorType
except ImportError:
    print("Installing required packages...")
    import subprocess
    subprocess.check_call(["pip", "install", "lightgbm", "onnxmltools", "skl2onnx", "scikit-learn", "pandas", "numpy"])
    import lightgbm as lgb
    import onnxmltools
    from onnxmltools.convert import convert_lightgbm
    from skl2onnx.common.data_types import FloatTensorType

def label_data(df, horizon_rows=12):
    """
    Create target labels: did price move up in next N rows (1 hour @ 5min intervals)?
    horizon_rows: how many rows ahead to check (12 rows = 1 hour @ 5min)
    """
    df = df.sort_values("_timestamp").reset_index(drop=True)
    
    targets = []
    for i in range(len(df)):
        if i + horizon_rows >= len(df):
            # Not enough future data → drop these rows
            targets.append(None)
        else:
            current_price = df.loc[i, "_price_now"]
            future_price = df.loc[i + horizon_rows, "_price_now"]
            
            # Label: 1 if price went UP, 0 if DOWN
            targets.append(1 if future_price > current_price else 0)
    
    df["target"] = targets
    # Drop rows without labels
    df = df.dropna(subset=["target"])
    return df

def train_model(csv_path="training_data_raw.csv", horizon_rows=12):
    """Train LightGBM model and export to ONNX."""
    
    # Load data
    print(f"Loading data from {csv_path}...")
    df = pd.read_csv(csv_path)
    print(f"  Loaded {len(df)} raw samples")
    
    # Label
    print(f"Labeling data (horizon={horizon_rows} rows = {horizon_rows*5} minutes)...")
    df = label_data(df, horizon_rows=horizon_rows)
    print(f"  {len(df)} labeled samples after filtering")
    
    if len(df) < 100:
        print("ERROR: Need at least 100 labeled samples. Run collect_training_data.py longer!")
        return
    
    # Load feature order
    feature_path = Path("models/feature_order.json")
    if not feature_path.exists():
        print(f"ERROR: {feature_path} not found!")
        return
    
    with open(feature_path) as f:
        features = json.load(f)
    
    # Remove internal columns
    features = [f for f in features if not f.startswith("_")]
    
    # Ensure all features exist (fill missing with 0)
    for feat in features:
        if feat not in df.columns:
            df[feat] = 0.0
    
    X = df[features].fillna(0.0)
    y = df["target"].astype(int)
    
    print(f"\nFeature matrix: {X.shape}")
    print(f"Target distribution: {y.value_counts().to_dict()}")
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    # Train LightGBM
    print("\nTraining LightGBM...")
    model = lgb.LGBMClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbose=0
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
    
    # Evaluate
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    
    print("\n" + "="*60)
    print("MODEL PERFORMANCE")
    print("="*60)
    print(f"Accuracy: {accuracy_score(y_test, y_pred):.3f}")
    print(f"ROC AUC:  {roc_auc_score(y_test, y_proba):.3f}")
    print("\nClassification Report:")
    print(classification_report(y_test, y_pred, target_names=["DOWN", "UP"]))
    
    # Feature importance
    print("\nTop 10 Features:")
    importances = sorted(
        zip(features, model.feature_importances_),
        key=lambda x: x[1],
        reverse=True
    )
    for feat, imp in importances[:10]:
        print(f"  {feat:20s} {imp:8.1f}")
    
    # Convert to ONNX
    print("\nConverting to ONNX...")
    initial_type = [('float_input', FloatTensorType([None, len(features)]))]
    onnx_model = convert_lightgbm(
        model,
        initial_types=initial_type,
        target_opset=12
    )
    
    # Save
    output_path = Path("models/xau_nowcast_lgb.onnx")
    output_path.parent.mkdir(exist_ok=True, parents=True)
    with open(output_path, "wb") as f:
        f.write(onnx_model.SerializeToString())
    
    print(f"✓ Model saved to {output_path}")
    print(f"✓ Features: {len(features)}")
    print(f"✓ Training samples: {len(X_train)}")
    print(f"✓ Test samples: {len(X_test)}")
    print("\nNext steps:")
    print("  1) Review performance above")
    print("  2) (Optional) Calibrate with train_platt.py")
    print("  3) Restart your backend → it will use the new model")

if __name__ == "__main__":
    import sys
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "training_data_raw.csv"
    horizon = int(sys.argv[2]) if len(sys.argv) > 2 else 12  # 12 rows = 1 hour @ 5min
    
    if not Path(csv_file).exists():
        print(f"ERROR: {csv_file} not found!")
        print("\nFirst run: python collect_training_data.py")
        print("Let it collect for at least a few hours, then run this script again.")
        sys.exit(1)
    
    train_model(csv_file, horizon)

