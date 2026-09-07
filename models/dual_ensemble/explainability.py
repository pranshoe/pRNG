import json
import numpy as np
from typing import Dict, Any, List
from dual_ensemble.xgboost_ensemble import XGBoostBranch
from dual_ensemble.config import SCALAR_FEATURE_KEYS

def compute_branch_feature_importance(
    branch: XGBoostBranch,
    feature_names: List[str]
) -> Dict[str, float]:
    """
    Computes averaged feature importances across all 10 models in an XGBoost branch.
    """
    if not branch.models:
        return {}

    importance_list = []
    for model in branch.models:
        booster = model.get_booster()
        score_dict = booster.get_score(importance_type="gain")
        scores = [score_dict.get(f"f{i}", 0.0) for i in range(len(feature_names))]
        importance_list.append(scores)

    avg_importances = np.mean(importance_list, axis=0)
    total = np.sum(avg_importances)
    if total > 0:
        avg_importances = avg_importances / total

    return {name: float(imp) for name, imp in zip(feature_names, avg_importances)}

def explain_ensemble(
    branch_ae: XGBoostBranch,
    branch_lstm: XGBoostBranch,
    ae_embed_dim: int = 128,
    lstm_embed_dim: int = 128,
    save_path: str = "feature_importances.json"
) -> Dict[str, Any]:
    """
    Computes and saves feature importances for both AE and LSTM branches.
    """
    ae_feature_names = SCALAR_FEATURE_KEYS + [f"ae_dim_{i}" for i in range(ae_embed_dim)]
    lstm_feature_names = SCALAR_FEATURE_KEYS + [f"lstm_dim_{i}" for i in range(lstm_embed_dim)]

    ae_importances = compute_branch_feature_importance(branch_ae, ae_feature_names)
    lstm_importances = compute_branch_feature_importance(branch_lstm, lstm_feature_names)

    ae_scalar_imp = sum(ae_importances.get(k, 0.0) for k in SCALAR_FEATURE_KEYS)
    lstm_scalar_imp = sum(lstm_importances.get(k, 0.0) for k in SCALAR_FEATURE_KEYS)

    explanation = {
        "branch_ae": {
            "scalar_total_importance": ae_scalar_imp,
            "embedding_total_importance": 1.0 - ae_scalar_imp if ae_importances else 0.0,
            "feature_importances": ae_importances
        },
        "branch_lstm": {
            "scalar_total_importance": lstm_scalar_imp,
            "embedding_total_importance": 1.0 - lstm_scalar_imp if lstm_importances else 0.0,
            "feature_importances": lstm_importances
        }
    }

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(explanation, f, indent=2)

    return explanation
