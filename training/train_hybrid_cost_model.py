#!/usr/bin/env python3
"""
train_hybrid_cost_model.py

Trains 10 Bagging XGBoost Models with 180D Recursive Structural AST Embeddings (Tree-LSTM)
+ Global Hardware/IR Features on GPU for the LLVM Loop Optimization Pipeline.
"""

import os
import sys
import time
import json
import numpy as np
import xgboost as xgb
from tqdm import tqdm
from sklearn.metrics import mean_squared_error, mean_absolute_error
from scipy.stats import spearmanr

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
looper_model_pkg = os.path.join(current_dir, 'looper_cost_model')
if looper_model_pkg not in sys.path:
    sys.path.insert(0, looper_model_pkg)

from looper_dataset import get_dataloader
from dual_ensemble.tree_ast_encoder import TreeASTEncoder

def train_upgraded_ensemble():
    print("=" * 75)
    print("PHASE 2: Training Upgraded XGBoost Ensemble with 180D Tree-LSTM Embeddings")
    print("=" * 75)

    repo_root = os.path.dirname(current_dir)
    saved_models_dir = os.environ.get("SAVED_MODELS_DIR", os.path.join(repo_root, "models", "tree_ast_branch"))
    os.makedirs(saved_models_dir, exist_ok=True)

    weights_path = os.environ.get(
        "WEIGHTS_PATH",
        os.path.join(current_dir, "runs", "pact25", "weights", "best_looper_cost_model.pt")
    )
    if not os.path.exists(weights_path):
        # Fallback search in models/
        alt_weights = os.path.join(repo_root, "models", "runs", "pact25", "weights", "best_looper_cost_model.pt")
        if os.path.exists(alt_weights):
            weights_path = alt_weights
        else:
            print(f"Waiting for weights at {weights_path}...")
            return

    encoder = TreeASTEncoder(weights_path=weights_path)

    default_train = os.path.join(repo_root, "data", "LooperSet", "data", "pact25", "looperset_v2_pact_train_compact.jsonl.gz")
    default_val = os.path.join(repo_root, "data", "LooperSet", "data", "pact25", "looperset_v2_pact_validation_compact.jsonl.gz")
    train_path = os.environ.get("TRAIN_DATA_PATH", default_train if os.path.exists(default_train) else "LooperSet/data/pact25/looperset_v2_pact_train_compact.jsonl.gz")
    val_path = os.environ.get("VAL_DATA_PATH", default_val if os.path.exists(default_val) else "LooperSet/data/pact25/looperset_v2_pact_validation_compact.jsonl.gz")

    print("1. Loading cached feature batches...")
    train_loader, scaler = get_dataloader(train_path, batch_size=1, is_train=True, max_programs=3000)
    val_loader, _ = get_dataloader(val_path, scaler=scaler, batch_size=1, is_train=False, max_programs=600)

    print("2. Extracting 180D Tree-LSTM Structural Embeddings...")
    X_train_list, y_train_list = [], []
    for X_batch, Y_batch in tqdm(train_loader, desc="Extracting Train Embeddings"):
        embs = encoder.get_embedding_from_tensors(X_batch)
        y = Y_batch.numpy().flatten()
        X_train_list.append(embs)
        y_train_list.append(y)

    X_train = np.vstack(X_train_list)
    y_train = np.concatenate(y_train_list)

    X_val_list, y_val_list = [], []
    for X_batch, Y_batch in tqdm(val_loader, desc="Extracting Val Embeddings"):
        embs = encoder.get_embedding_from_tensors(X_batch)
        y = Y_batch.numpy().flatten()
        X_val_list.append(embs)
        y_val_list.append(y)

    X_val = np.vstack(X_val_list)
    y_val = np.concatenate(y_val_list)

    print(f"-> Extracted Features: X_train shape={X_train.shape}, X_val shape={X_val.shape}")

    # 3. Train 10 Bagging Folds on CUDA GPU
    num_folds = 10
    models = []
    print(f"\n3. Training {num_folds} XGBoost Bagging Folds on NVIDIA RTX 4070 GPU...")

    params = {
        "tree_method": "hist",
        "device": "cuda",
        "max_depth": 8,
        "learning_rate": 0.05,
        "n_estimators": 400,
        "subsample": 0.85,
        "colsample_bytree": 0.85,
        "random_state": 42
    }

    n_samples = len(X_train)
    val_preds_folds = np.zeros((num_folds, len(y_val)), dtype=np.float32)

    for fold in range(num_folds):
        fold_seed = 42 + fold * 17
        np.random.seed(fold_seed)
        indices = np.random.choice(n_samples, size=int(n_samples * 0.85), replace=True)

        reg = xgb.XGBRegressor(**{**params, "random_state": fold_seed})
        reg.fit(
            X_train[indices], y_train[indices],
            eval_set=[(X_val, y_val)],
            verbose=False
        )

        model_path = os.path.join(saved_models_dir, f"tree_xgboost_fold_{fold}.json")
        reg.save_model(model_path)
        models.append(reg)

        fold_preds = reg.predict(X_val)
        val_preds_folds[fold] = fold_preds
        fold_mae = mean_absolute_error(y_val, fold_preds)
        print(f"   Fold {fold+1}/{num_folds} Complete | Val MAE: {fold_mae:.4f}")

    # Ensemble evaluation
    ensemble_preds = np.mean(val_preds_folds, axis=0)
    ens_mae = mean_absolute_error(y_val, ensemble_preds)
    ens_rmse = np.sqrt(mean_squared_error(y_val, ensemble_preds))
    rho = spearmanr(y_val, ensemble_preds).statistic

    print("=" * 75)
    print(f"Phase 2 Complete! 10 Bagging Models Saved to {saved_models_dir}")
    print(f"Ensemble Val MAE: {ens_mae:.4f} | RMSE: {ens_rmse:.4f} | Spearman rho: {rho:.4f}")
    print("=" * 75)

    metrics = {
        "num_folds": num_folds,
        "feature_dim": int(X_train.shape[1]),
        "val_mae": float(ens_mae),
        "val_rmse": float(ens_rmse),
        "spearman_rho": float(rho)
    }
    with open(os.path.join(saved_models_dir, "tree_xgboost_metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)

if __name__ == "__main__":
    train_upgraded_ensemble()
