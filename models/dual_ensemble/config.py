import os
import torch

# Device Settings
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
XGB_DEVICE = "cuda"
XGB_TREE_METHOD = "hist"

# Dataset Configuration (Compact PACT25 splits in workspace)
LOCAL_TRAIN_PATH = os.getenv("TRAIN_DATA_PATH", os.path.join("LooperSet", "data", "pact25", "looperset_v2_pact_train_compact.jsonl.gz"))
LOCAL_VAL_PATH = os.getenv("VAL_DATA_PATH", os.path.join("LooperSet", "data", "pact25", "looperset_v2_pact_validation_compact.jsonl.gz"))

# AutoEncoder API Configuration
AE_API_URL = os.getenv("AE_API_URL", "http://localhost:8000/embed_ae")
AE_API_KEY = os.getenv("AE_API_KEY", "")
AE_CACHE_FILE = "ae_embeddings_cache.json"

# Expression LSTM Configuration
VOCAB_PATH = os.getenv("VOCAB_PATH", "vocab.json")
LOOPER_WEIGHTS_PATH = os.getenv("LOOPER_WEIGHTS_PATH", "looper_weights.pth")
LSTM_INPUT_DIM = 64
LSTM_HIDDEN_DIM = 128
LSTM_NUM_LAYERS = 2
LSTM_EMBED_DIM = 128

# 8 Scalar Features (Extracted directly from program_annotation / looperset_features.py)
SCALAR_FEATURE_KEYS = [
    "memory_size",
    "loop_depth",
    "total_trip_count",
    "has_parametric_bounds",
    "num_computations",
    "num_reduction_computations",
    "num_buffers",
    "total_accesses"
]

# Model Ensemble Settings (Rule of Thumb: log2(136) ~ 7)
NUM_ENSEMBLE_MODELS = 10
XGB_PARAMS = {
    "n_estimators": 100,
    "max_depth": 7,
    "max_leaves": 128,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.7,
    "tree_method": XGB_TREE_METHOD,
    "device": XGB_DEVICE,
    "random_state": 42
}

# Weighted Ensemble Comparison Weights (w_AE, w_LSTM = 1 - w_AE)
COMPARISON_WEIGHTS = [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0]
PREDICTIONS_SAVE_PATH = "ensemble_predictions_comparison.json"
