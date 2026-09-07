import json
import numpy as np
from typing import Dict, Any, List
from scipy.stats import spearmanr
from sklearn.metrics import precision_score, recall_score, f1_score, accuracy_score

from dual_ensemble.config import COMPARISON_WEIGHTS, PREDICTIONS_SAVE_PATH
from dual_ensemble.xgboost_ensemble import XGBoostBranch

def compute_classification_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    """
    Computes classification metrics for binary decision: positive log_speedup = apply (1), else 0.
    """
    true_binary = (y_true > 0).astype(int)
    pred_binary = (y_pred > 0).astype(int)

    return {
        "accuracy": float(accuracy_score(true_binary, pred_binary)),
        "precision": float(precision_score(true_binary, pred_binary, zero_division=0)),
        "recall": float(recall_score(true_binary, pred_binary, zero_division=0)),
        "f1": float(f1_score(true_binary, pred_binary, zero_division=0))
    }

def evaluate_predictions(
    y_true: np.ndarray,
    p_ae: np.ndarray,
    p_lstm: np.ndarray,
    weights: List[float] = COMPARISON_WEIGHTS,
    save_path: str = PREDICTIONS_SAVE_PATH
) -> Dict[str, Any]:
    """
    Computes ensemble predictions across multiple weight configurations,
    calculates MSE, MAE, Spearman correlation, and Classification metrics for each.
    """
    results = {
        "metrics": {},
        "predictions": {
            "p_ae": p_ae.tolist(),
            "p_lstm": p_lstm.tolist(),
            "y_true": y_true.tolist(),
            "weighted_ensembles": {}
        }
    }

    best_weight = None
    best_mse = float("inf")

    for w in weights:
        w_ae = float(w)
        w_lstm = round(1.0 - w_ae, 2)
        p_ensemble = w_ae * p_ae + w_lstm * p_lstm

        mse = float(np.mean((p_ensemble - y_true) ** 2))
        mae = float(np.mean(np.abs(p_ensemble - y_true)))
        
        spearman_corr, _ = spearmanr(p_ensemble, y_true)
        spearman_val = float(spearman_corr) if not np.isnan(spearman_corr) else 0.0

        clf_metrics = compute_classification_metrics(y_true, p_ensemble)

        key = f"AE_{w_ae:.2f}_LSTM_{w_lstm:.2f}"
        results["metrics"][key] = {
            "weight_ae": w_ae,
            "weight_lstm": w_lstm,
            "mse": mse,
            "mae": mae,
            "spearman_rho": spearman_val,
            "decision_gate": clf_metrics
        }
        results["predictions"]["weighted_ensembles"][key] = p_ensemble.tolist()

        if mse < best_mse:
            best_mse = mse
            best_weight = key

    results["best_configuration"] = {
        "configuration": best_weight,
        **results["metrics"][best_weight]
    }

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return results

def evaluate_branches(
    branch_ae: XGBoostBranch,
    branch_lstm: XGBoostBranch,
    X_val_ae: np.ndarray,
    X_val_lstm: np.ndarray,
    y_val: np.ndarray,
    weights: List[float] = COMPARISON_WEIGHTS,
    save_path: str = PREDICTIONS_SAVE_PATH
) -> Dict[str, Any]:
    """
    Evaluates both 10x XGBoost branches and weighted ensembles on validation data.
    """
    p_ae = branch_ae.predict(X_val_ae)
    p_lstm = branch_lstm.predict(X_val_lstm)
    return evaluate_predictions(y_val, p_ae, p_lstm, weights, save_path)
