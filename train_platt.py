#!/usr/bin/env python3
"""
Optional: Calibrate your model's raw probabilities using Platt scaling.
Run AFTER label_and_train.py has created the ONNX model.
"""
import pandas as pd
import numpy as np
import json
from pathlib import Path
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression

try:
    import onnxruntime as ort
except ImportError:
    import subprocess
    subprocess.check_call(["pip", "install", "onnxruntime"])
    import onnxruntime as ort

def calibrate_platt(csv_path="training_data_raw.csv", horizon_rows=12):
    """
    Fit Platt scaling on model's raw outputs using a holdout calibration set.
    Saves coefficients to models/platt.json
    """
    # Load model
    model_path = Path("models/xau_nowcast_lgb.onnx")
    if not model_path.exists():
        print(f"ERROR: {model_path} not found! Run label_and_train.py first.")
        return
    
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    
    # Load features
    with open("models/feature_order.json") as f:
        features = json.load(f)
    features = [f for f in features if not f.startswith("_")]
    
    # Load and label data (same as training)
    print(f"Loading {csv_path}...")
    df = pd.read_csv(csv_path)
    
    # Label
    df = df.sort_values("_timestamp").reset_index(drop=True)
    targets = []
    for i in range(len(df)):
        if i + horizon_rows >= len(df):
            targets.append(None)
        else:
            current = df.loc[i, "_price_now"]
            future = df.loc[i + horizon_rows, "_price_now"]
            targets.append(1 if future > current else 0)
    df["target"] = targets
    df = df.dropna(subset=["target"])
    
    for feat in features:
        if feat not in df.columns:
            df[feat] = 0.0
    
    X = df[features].fillna(0.0).values.astype(np.float32)
    y = df["target"].astype(int).values
    
    # Split: use different split than training to avoid overfitting calibration
    _, X_cal, _, y_cal = train_test_split(X, y, test_size=0.3, random_state=999, stratify=y)
    
    # Get raw model predictions
    print("Getting raw model predictions...")
    input_name = session.get_inputs()[0].name
    raw_preds = []
    for row in X_cal:
        out = session.run(None, {input_name: row.reshape(1, -1)})
        raw_preds.append(float(out[0].ravel()[0]))
    raw_preds = np.array(raw_preds).reshape(-1, 1)
    
    # Fit Platt: logit(p_calib) = w * p_raw + b
    print("Fitting Platt scaling...")
    platt = LogisticRegression(random_state=42, max_iter=1000)
    platt.fit(raw_preds, y_cal)
    
    w = float(platt.coef_[0][0])
    b = float(platt.intercept_[0])
    
    print(f"\nPlatt coefficients:")
    print(f"  w = {w:.4f}")
    print(f"  b = {b:.4f}")
    
    # Save
    platt_data = {"w": w, "b": b}
    with open("models/platt.json", "w") as f:
        json.dump(platt_data, f, indent=2)
    
    print(f"\n✓ Platt calibration saved to models/platt.json")
    print("✓ Your backend will now use calibrated probabilities")
    
    # Quick validation
    from sklearn.calibration import calibration_curve
    y_calib = 1.0 / (1.0 + np.exp(-(w * raw_preds.ravel() + b)))
    
    print("\nCalibration quality check:")
    prob_true, prob_pred = calibration_curve(y_cal, y_calib, n_bins=5)
    for pt, pp in zip(prob_true, prob_pred):
        print(f"  Predicted {pp:.2f} → Actual {pt:.2f}")

if __name__ == "__main__":
    import sys
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "training_data_raw.csv"
    horizon = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    
    if not Path(csv_file).exists():
        print(f"ERROR: {csv_file} not found!")
        sys.exit(1)
    
    calibrate_platt(csv_file, horizon)

