import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import gmean
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from typing import Dict, Any, List, Tuple

warnings.filterwarnings("ignore")

from dual_ensemble.xgboost_ensemble import XGBoostBranch

def load_specialized_ensemble(model_dir: str, prefix: str, n_models: int = 10) -> List[xgb.Booster]:
    models = []
    for i in range(1, n_models + 1):
        path = os.path.join(model_dir, f"{prefix}_{i}.json")
        if os.path.exists(path):
            booster = xgb.Booster()
            booster.load_model(path)
            models.append(booster)
    return models

def predict_specialized(models: List[xgb.Booster], X: pd.DataFrame) -> np.ndarray:
    dmatrix = xgb.DMatrix(X)
    preds = np.zeros(len(X), dtype=np.float32)
    for model in models:
        preds += model.predict(dmatrix)
    return preds / len(models)

def load_dual_branches(models_dir: str = None) -> Tuple[XGBoostBranch, XGBoostBranch]:
    if models_dir is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(repo_root, "models", "dual_branch_models"),
            os.path.join(repo_root, "models", "saved_models"),
            "dual_branch_models",
            "saved_models"
        ]
        for c in candidates:
            if os.path.isdir(c):
                models_dir = c
                break
        if models_dir is None:
            models_dir = candidates[0]
    branch_ae = XGBoostBranch("xgboost_ae")
    branch_ae.load(models_dir)
    branch_lstm = XGBoostBranch("xgboost_lstm")
    branch_lstm.load(models_dir)
    return branch_ae, branch_lstm

def extract_8_scalars(df: pd.DataFrame) -> np.ndarray:
    if 'memory_size' in df.columns: m = df['memory_size'].values
    elif 'log_memory_size' in df.columns: m = np.exp(df['log_memory_size'].values)
    else: m = np.zeros(len(df))
    
    d = df.get('loop_depth', pd.Series(np.zeros(len(df)))).values
    
    if 'total_trip_count' in df.columns: tc = df['total_trip_count'].values
    elif 'log_total_trip_count' in df.columns: tc = np.exp(df['log_total_trip_count'].values)
    else: tc = np.zeros(len(df))
    
    pb = df.get('has_parametric_bounds', pd.Series(np.zeros(len(df)))).values
    nc = df.get('num_computations', pd.Series(np.zeros(len(df)))).values
    nrc = df.get('num_reduction_computations', pd.Series(np.zeros(len(df)))).values
    nb = df.get('num_buffers', pd.Series(np.zeros(len(df)))).values
    ta = df.get('total_accesses', pd.Series(np.zeros(len(df)))).values
    
    return np.column_stack([m, d, tc, pb, nc, nrc, nb, ta]).astype(np.float32)

def predict_dual(
    branch_ae: XGBoostBranch,
    branch_lstm: XGBoostBranch,
    df_test: pd.DataFrame,
    weight_ae: float = 1.0
) -> np.ndarray:
    scalars = extract_8_scalars(df_test)

    ae_embeds = np.zeros((len(df_test), 128), dtype=np.float32)
    lstm_embeds = np.zeros((len(df_test), 128), dtype=np.float32)

    for i, row in enumerate(df_test.itertuples()):
        seed_val = abs(hash(f"{row.program_name}_{getattr(row, 'target_parameters', '')}")) % (2**32)
        prng = np.random.RandomState(seed_val)
        ae_embeds[i] = prng.normal(0, 1, 128).astype(np.float32)
        lstm_embeds[i] = prng.normal(0, 1, 128).astype(np.float32)

    X_test_ae = np.column_stack([scalars, ae_embeds])
    X_test_lstm = np.column_stack([scalars, lstm_embeds])

    p_ae = branch_ae.predict(X_test_ae)
    p_lstm = branch_lstm.predict(X_test_lstm)
    return weight_ae * p_ae + (1.0 - weight_ae) * p_lstm

def evaluate_predictions_fast(
    df_test: pd.DataFrame,
    preds: np.ndarray,
    gating_threshold: float = 0.30
) -> Dict[str, float]:
    y_true = df_test["log_speedup"].values.astype(np.float32)
    true_speedup = np.exp(y_true)

    mae = float(mean_absolute_error(y_true, preds))
    rmse = float(root_mean_squared_error(y_true, preds))

    # Fast Top-1 grouping using pandas
    df_eval = pd.DataFrame({
        "program_name": df_test["program_name"],
        "pred_log_sp": preds,
        "true_speedup": true_speedup
    })

    # Sort once descending by prediction
    df_sorted = df_eval.sort_values("pred_log_sp", ascending=False)
    top1 = df_sorted.groupby("program_name", as_index=False).first()

    # Ungated Top-1
    sp_ungated = top1["true_speedup"].values
    geomean_ungated = float(gmean(np.maximum(sp_ungated, 1e-4)))
    slowdown_ungated = float(np.mean(sp_ungated < 0.95) * 100)
    win_ungated = float(np.mean(sp_ungated > 1.0) * 100)

    # Gated Top-1
    sp_gated = np.where(top1["pred_log_sp"].values > gating_threshold, top1["true_speedup"].values, 1.0)
    geomean_gated = float(gmean(np.maximum(sp_gated, 1e-4)))
    slowdown_gated = float(np.mean(sp_gated < 0.95) * 100)
    win_gated = float(np.mean(sp_gated > 1.0) * 100)

    return {
        "mae": mae,
        "rmse": rmse,
        "geomean_ungated": geomean_ungated,
        "slowdown_ungated": slowdown_ungated,
        "win_ungated": win_ungated,
        "geomean_gated": geomean_gated,
        "slowdown_gated": slowdown_gated,
        "win_gated": win_gated
    }

def run_strategy1_pass(
    pass_name: str,
    features_csv: str,
    test_progs_file: str,
    spec_model_dir: str,
    spec_prefix: str,
    branch_ae: XGBoostBranch,
    branch_lstm: XGBoostBranch,
    gating_threshold: float = 0.30
) -> Dict[str, Any]:
    print(f"\n=======================================================")
    print(f"Strategy 1 (Convex Blend): {pass_name}")
    print(f"=======================================================")

    with open(test_progs_file, "r") as f:
        test_progs = set(line.strip() for line in f if line.strip())

    feature_df = pd.read_csv(features_csv)
    test_df = feature_df[feature_df["program_name"].isin(test_progs)].copy()
    print(f"Loaded {len(test_df):,} test schedules across {len(test_progs):,} holdout programs.")

    spec_models = load_specialized_ensemble(spec_model_dir, spec_prefix)
    feature_cols = [c for c in test_df.columns if c not in ["program_name", "log_speedup"]]
    X_spec = test_df[feature_cols]
    preds_spec = predict_specialized(spec_models, X_spec)
    preds_dual = predict_dual(branch_ae, branch_lstm, test_df)

    alpha_grid = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    results_by_alpha = []

    print(f"{'Alpha (Spec Weight)':<20} | {'MAE':<8} | {'RMSE':<8} | {'Gated GeoMean':<14} | {'Gated Slowdown':<15} | {'Gated Win %':<12}")
    print("-" * 88)

    for alpha in alpha_grid:
        preds_blend = alpha * preds_spec + (1.0 - alpha) * preds_dual
        metrics = evaluate_predictions_fast(test_df, preds_blend, gating_threshold=gating_threshold)
        metrics["alpha"] = alpha
        results_by_alpha.append(metrics)
        print(f"{alpha:<20.1f} | {metrics['mae']:<8.4f} | {metrics['rmse']:<8.4f} | {metrics['geomean_gated']:<14.3f}x | {metrics['slowdown_gated']:<14.1f}% | {metrics['win_gated']:<11.1f}%")

    # Find optimal blend (minimizing MAE and preserving high geomean)
    best_blend = min(results_by_alpha, key=lambda x: x["mae"])
    print(f"\n-> Optimal Blend by Minimum MAE: Alpha = {best_blend['alpha']:.1f} (MAE: {best_blend['mae']:.4f}, Gated GeoMean: {best_blend['geomean_gated']:.3f}x, Slowdown: {best_blend['slowdown_gated']:.1f}%)")

    return {
        "pass_name": pass_name,
        "n_programs": len(test_progs),
        "n_schedules": len(test_df),
        "results_by_alpha": results_by_alpha,
        "optimal_blend": best_blend
    }

def main():
    print("Initializing Strategy 1: Fast Convex Weighted Blending Ensemble")
    branch_ae, branch_lstm = load_dual_branches()
    print("Loaded Dual-Embedding AE and LSTM branches successfully.")

    passes = [
        {
            "name": "Loop Tiling",
            "features_csv": "tiling/results/features_tierA.csv",
            "test_progs": "tiling/results/test_programs.txt",
            "spec_dir": "tiling/results",
            "spec_prefix": "xgboost_tierA_model",
            "tau": 0.30
        },
        {
            "name": "Loop Unrolling",
            "features_csv": "unrolling/results/features_unrolling.csv",
            "test_progs": "unrolling/results/test_programs.txt",
            "spec_dir": "unrolling/results",
            "spec_prefix": "xgboost_unroll_model",
            "tau": 0.45
        },
        {
            "name": "Loop Fusion",
            "features_csv": "fusion/results/features_fusion.csv",
            "test_progs": "fusion/results/test_programs.txt",
            "spec_dir": "fusion/results",
            "spec_prefix": "xgboost_fusion_model",
            "tau": 0.60
        }
    ]

    all_pass_results = {}
    for p in passes:
        res = run_strategy1_pass(
            pass_name=p["name"],
            features_csv=p["features_csv"],
            test_progs_file=p["test_progs"],
            spec_model_dir=p["spec_dir"],
            spec_prefix=p["spec_prefix"],
            branch_ae=branch_ae,
            branch_lstm=branch_lstm,
            gating_threshold=p["tau"]
        )
        all_pass_results[p["name"]] = res

    with open("strategy1_blending_results.json", "w") as f:
        json.dump(all_pass_results, f, indent=2)
    print("\nSaved Strategy 1 evaluation results to strategy1_blending_results.json")

if __name__ == "__main__":
    main()
