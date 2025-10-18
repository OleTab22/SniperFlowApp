#!/usr/bin/env python3
"""Create a minimal dummy ONNX model for testing the ML integration."""
import json
import numpy as np

try:
    import lightgbm as lgb
    import onnxmltools
    from onnxmltools.convert import convert_lightgbm
    from skl2onnx.common.data_types import FloatTensorType
except ImportError:
    print("Installing required packages...")
    import subprocess
    subprocess.check_call(["pip", "install", "lightgbm", "onnxmltools", "skl2onnx", "scikit-learn"])
    import lightgbm as lgb
    import onnxmltools
    from onnxmltools.convert import convert_lightgbm
    from skl2onnx.common.data_types import FloatTensorType

# Load feature order
with open("models/feature_order.json", "r") as f:
    features = json.load(f)

print(f"Creating dummy model with {len(features)} features...")

# Create dummy training data (random)
np.random.seed(42)
n_samples = 500
X = np.random.randn(n_samples, len(features)).astype(np.float32)
# Simple target: bull if dxy_z (negative for gold) + real_z (negative) > 0
y = ((X[:, 0] * -0.5 + X[:, 1] * -0.3 + X[:, 6] * 0.4) > 0).astype(int)

# Train a tiny LightGBM model
model = lgb.LGBMClassifier(
    n_estimators=10,
    max_depth=3,
    learning_rate=0.1,
    random_state=42,
    verbose=-1
)
model.fit(X, y)

# Convert to ONNX
initial_type = [('float_input', FloatTensorType([None, len(features)]))]
onnx_model = convert_lightgbm(
    model,
    initial_types=initial_type,
    target_opset=12
)

# Save
with open("models/xau_nowcast_lgb.onnx", "wb") as f:
    f.write(onnx_model.SerializeToString())

print("✓ Dummy model saved to models/xau_nowcast_lgb.onnx")
print("✓ This model will produce random predictions - replace with a real trained model later")

