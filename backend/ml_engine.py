import os, json
from pathlib import Path
import numpy as np

# Safe optional import (keeps backend alive if ORT unavailable)
try:
    import onnxruntime as ort
except Exception:  # pragma: no cover
    ort = None

# --- Model discovery ---
MODEL_DIR = Path(os.getenv("MODEL_DIR", "models"))
MODEL_PATH = MODEL_DIR / os.getenv("MODEL_FILE", "xau_nowcast_lgb.onnx")
FEAT_PATH  = MODEL_DIR / os.getenv("FEATURE_ORDER_FILE", "feature_order.json")
PLATT_PATH = os.getenv("PLATT_FILE", "platt.json")  # optional

# Lazy singletons
_SESSION = None
_FEATURE_ORDER = None
_PLATT = None

def _load_once():
    global _SESSION, _FEATURE_ORDER, _PLATT
    if _SESSION is None and ort is not None and MODEL_PATH.exists():
        _SESSION = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
    if _FEATURE_ORDER is None and FEAT_PATH.exists():
        _FEATURE_ORDER = json.loads(FEAT_PATH.read_text())
    if _PLATT is None and PLATT_PATH and Path(PLATT_PATH).exists():
        _PLATT = json.loads(Path(PLATT_PATH).read_text())

def available() -> bool:
    _load_once()
    return _SESSION is not None and _FEATURE_ORDER is not None

def feature_order() -> list[str] | None:
    _load_once()
    return _FEATURE_ORDER

def _as_row(feats: dict, order: list[str]) -> np.ndarray:
    row = [float(feats.get(k, 0.0) or 0.0) for k in order]
    return np.asarray([row], dtype=np.float32)

def _platt(p_raw: float) -> float:
    """Optional calibration from platt.json: p = sigmoid(w * p_raw + b)."""
    try:
        if not _PLATT:
            return float(p_raw)
        import math
        z = _PLATT.get("w", 1.0) * float(p_raw) + _PLATT.get("b", 0.0)
        return 1.0 / (1.0 + math.exp(-z))
    except Exception:
        return float(p_raw)

def predict_proba(features: dict) -> float | None:
    """
    Returns calibrated P(up) in [0,1], or None if model not available.
    """
    if not available():
        return None
    order = feature_order()
    x = _as_row(features, order)
    try:
        out = _SESSION.run(None, { _SESSION.get_inputs()[0].name: x })
        p_raw = float(out[0].ravel()[0])
    except Exception:
        return None
    return max(0.0, min(1.0, _platt(p_raw)))
