import numpy as np
from typing import Dict, Any, List
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from looperset_features import extract_program_features
except ImportError:
    extract_program_features = None

from dual_ensemble.config import SCALAR_FEATURE_KEYS

def extract_scalar_features(item: Dict[str, Any], keys: List[str] = SCALAR_FEATURE_KEYS) -> np.ndarray:
    """
    Extracts the 8 scalar/heuristic features from a LOOPerSet JSON object.
    Returns a 1D float32 numpy array of shape (8,).
    """
    if "program_annotation" in item and extract_program_features is not None:
        try:
            feat_dict = extract_program_features(item["program_annotation"])
            return np.array([float(feat_dict.get(k, 0.0)) for k in keys], dtype=np.float32)
        except Exception:
            pass

    scalar_dict = item.get("scalar_features", item)
    features = []
    for key in keys:
        val = scalar_dict.get(key, item.get(key, 0.0))
        if val is None:
            val = 0.0
        try:
            features.append(float(val))
        except (ValueError, TypeError):
            features.append(0.0)

    return np.array(features, dtype=np.float32)
