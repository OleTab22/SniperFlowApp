#!/usr/bin/env python3
"""Simple training script with verbose output."""
import pandas as pd
import numpy as np
import json
from pathlib import Path

print("Loading CSV...")
df = pd.read_csv("training_data_backfill.csv")
print(f"Loaded {len(df)} rows")
print(f"Columns: {list(df.columns)[:5]}...")

# Load features
print("\nLoading feature order...")
with open("models/feature_order.json") as f:
    features = json.load(f)
print(f"Features: {len(features)}")

# Filter to training features only
features = [f for f in features if not f.startswith("_")]
print(f"Training features: {len(features)}")

# Prepare data
print("\nPreparing features...")
for feat in features:
    if feat not in df.columns:
        df[feat] = 0.0

X = df[features].fillna(0.0).values.astype(np.float32)
y = df["target"].astype(int).values

print(f"X shape: {X.shape}")
print(f"y shape: {y.shape}")
print(f"Target dist: {np.bincount(y)}")

# Import training libs
print("\nImporting LightGBM...")
import lightgbm as lgb
print("Importing ONNX converters...")
import onnxmltools
from onnxmltools.convert import convert_lightgbm
from skl2onnx.common.data_types import FloatTensorType

# Split
from sklearn.model_selection import train_test_split
print("\nSplitting data...")
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)
print(f"Train: {len(X_train)}, Test: {len(X_test)}")

# Train
print("\nTraining LightGBM...")
model = lgb.LGBMClassifier(
    n_estimators=100,
    max_depth=5,
    learning_rate=0.1,
    num_leaves=15,
    random_state=42,
    verbose=-1
)
print("Fitting model...")
model.fit(X_train, y_train)

# Evaluate
print("\nEvaluating...")
from sklearn.metrics import accuracy_score, roc_auc_score
y_pred = model.predict(X_test)
y_proba = model.predict_proba(X_test)[:, 1]

acc = accuracy_score(y_test, y_pred)
auc = roc_auc_score(y_test, y_proba)

print(f"\n{'='*50}")
print(f"ACCURACY: {acc:.3f}")
print(f"ROC AUC:  {auc:.3f}")
print(f"{'='*50}")

# Convert to ONNX
print("\nConverting to ONNX...")
initial_type = [('float_input', FloatTensorType([None, len(features)]))]
onnx_model = convert_lightgbm(model, initial_types=initial_type, target_opset=12)

# Save
print("Saving model...")
Path("models").mkdir(exist_ok=True, parents=True)
with open("models/xau_nowcast_lgb.onnx", "wb") as f:
    f.write(onnx_model.SerializeToString())

print(f"\n✅ SUCCESS!")
print(f"   Model: models/xau_nowcast_lgb.onnx")
print(f"   Accuracy: {acc:.1%}")
print(f"   ROC AUC: {auc:.3f}")
print(f"\nNext: git add models/ && git commit && git push")

