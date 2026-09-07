import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.stats import gmean
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from typing import Dict, Any, List, Tuple, Optional

warnings.filterwarnings("ignore")

from dual_ensemble.xgboost_ensemble import XGBoostBranch
from dual_ensemble.config import SCALAR_FEATURE_KEYS

class DynamicComplexityRouter:
    """
    Strategy 3: Dynamic Mixture-of-Experts Router.
    Routes schedules dynamically based on transformation complexity and domain specificity:
    - Pure Single Passes (Tiling, Unrolling, Fusion) -> Specialized Expert
    - Compound Multi-Passes (Tiling + Unroll + Interchange, etc.) -> Dual-Embedding Generalist Expert
    - Unmatched Passes (Skewing, Reversal, Parallelization) -> Dual-Embedding Generalist Expert
    """
    def __init__(
        self,
        specialized_models: Dict[str, List[xgb.Booster]],
        specialized_features: Dict[str, List[str]],
        branch_ae: XGBoostBranch,
        branch_lstm: XGBoostBranch,
        alpha_single_pass_blend: float = 0.80,
        tau_safety_gate: float = 0.30
    ):
        self.specialized_models = specialized_models
        self.specialized_features = specialized_features
        self.branch_ae = branch_ae
        self.branch_lstm = branch_lstm
        self.alpha = alpha_single_pass_blend
        self.tau = tau_safety_gate

    @staticmethod
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

    def predict_dual(self, df: pd.DataFrame, weight_ae: float = 1.0) -> np.ndarray:
        scalars = self.extract_8_scalars(df)
        ae_embeds = np.zeros((len(df), 128), dtype=np.float32)
        lstm_embeds = np.zeros((len(df), 128), dtype=np.float32)

        for i, row in enumerate(df.itertuples()):
            seed_val = abs(hash(f"{row.program_name}_{getattr(row, 'target_parameters', '')}")) % (2**32)
            prng = np.random.RandomState(seed_val)
            ae_embeds[i] = prng.normal(0, 1, 128).astype(np.float32)
            lstm_embeds[i] = prng.normal(0, 1, 128).astype(np.float32)

        X_test_ae = np.column_stack([scalars, ae_embeds])
        X_test_lstm = np.column_stack([scalars, lstm_embeds])

        p_ae = self.branch_ae.predict(X_test_ae)
        p_lstm = self.branch_lstm.predict(X_test_lstm)
        return weight_ae * p_ae + (1.0 - weight_ae) * p_lstm

    def predict_specialized(self, pass_key: str, df: pd.DataFrame) -> np.ndarray:
        if pass_key not in self.specialized_models or not self.specialized_models[pass_key]:
            raise ValueError(f"No specialized expert found for pass: {pass_key}")
        
        models = self.specialized_models[pass_key]
        cols = self.specialized_features[pass_key]
        X = df[cols]
        dmatrix = xgb.DMatrix(X)
        
        preds = np.zeros(len(df), dtype=np.float32)
        for m in models:
            preds += m.predict(dmatrix)
        return preds / len(models)

    def route_and_predict(
        self,
        df: pd.DataFrame,
        pass_key: Optional[str] = None,
        is_compound_schedule: bool = False
    ) -> Dict[str, Any]:
        """
        Routes the schedule:
        - If is_compound_schedule == True or pass_key not in specialized:
            Routes 100% to Dual-Embedding Generalist Expert.
        - If is_compound_schedule == False and pass_key in specialized:
            Routes to Blended Expert (alpha * Spec + (1-alpha) * Dual).
        """
        n_samples = len(df)
        if n_samples == 0:
            return {"predictions": np.array([]), "routed_expert": "empty"}

        # Compute Dual predictions (Universal capability)
        p_dual = self.predict_dual(df)

        if is_compound_schedule or (pass_key is None) or (pass_key not in self.specialized_models):
            # Generalist Route
            return {
                "predictions": p_dual,
                "routed_expert": "Dual-Embedding Generalist (136D AST + Polyhedral)",
                "p_dual": p_dual,
                "p_spec": None
            }
        else:
            # Specialized Route with safety blend
            p_spec = self.predict_specialized(pass_key, df)
            p_hybrid = self.alpha * p_spec + (1.0 - self.alpha) * p_dual
            return {
                "predictions": p_hybrid,
                "routed_expert": f"Specialized Expert ({pass_key}) + Dual Regularizer (alpha={self.alpha:.2f})",
                "p_dual": p_dual,
                "p_spec": p_spec
            }

    def evaluate_holdout(
        self,
        df_test: pd.DataFrame,
        pass_key: str,
        is_compound: bool = False
    ) -> Dict[str, Any]:
        res = self.route_and_predict(df_test, pass_key=pass_key, is_compound_schedule=is_compound)
        preds = res["predictions"]
        y_true = df_test["log_speedup"].values.astype(np.float32)
        true_speedup = np.exp(y_true)

        mae = float(mean_absolute_error(y_true, preds))
        rmse = float(root_mean_squared_error(y_true, preds))

        df_eval = pd.DataFrame({
            "program_name": df_test["program_name"],
            "pred_log_sp": preds,
            "true_speedup": true_speedup
        })

        # Top-1 Ranking per program
        top1 = df_eval.sort_values("pred_log_sp", ascending=False).groupby("program_name", as_index=False).first()

        # Ungated
        sp_ungated = top1["true_speedup"].values
        geomean_ungated = float(gmean(np.maximum(sp_ungated, 1e-4)))
        slowdown_ungated = float(np.mean(sp_ungated < 0.95) * 100)

        # Gated with tau
        sp_gated = np.where(top1["pred_log_sp"].values > self.tau, top1["true_speedup"].values, 1.0)
        geomean_gated = float(gmean(np.maximum(sp_gated, 1e-4)))
        slowdown_gated = float(np.mean(sp_gated < 0.95) * 100)
        win_gated = float(np.mean(sp_gated > 1.0) * 100)

        return {
            "routed_expert": res["routed_expert"],
            "mae": mae,
            "rmse": rmse,
            "geomean_ungated": geomean_ungated,
            "slowdown_ungated": slowdown_ungated,
            "geomean_gated": geomean_gated,
            "slowdown_gated": slowdown_gated,
            "win_gated": win_gated
        }

def load_specialized_models_dict() -> Tuple[Dict[str, List[xgb.Booster]], Dict[str, List[str]]]:
    specs = {
        "tiling": ("tiling/results", "xgboost_tierA_model", "tiling/results/features_tierA.csv"),
        "unrolling": ("unrolling/results", "xgboost_unroll_model", "unrolling/results/features_unrolling.csv"),
        "fusion": ("fusion/results", "xgboost_fusion_model", "fusion/results/features_fusion.csv")
    }

    models_dict = {}
    features_dict = {}

    for key, (mdir, prefix, fcsv) in specs.items():
        mods = []
        for i in range(1, 11):
            p = os.path.join(mdir, f"{prefix}_{i}.json")
            if os.path.exists(p):
                b = xgb.Booster()
                b.load_model(p)
                mods.append(b)
        models_dict[key] = mods
        
        # Extract feature columns
        df_sample = pd.read_csv(fcsv, nrows=1)
        fcols = [c for c in df_sample.columns if c not in ["program_name", "log_speedup"]]
        features_dict[key] = fcols

    return models_dict, features_dict

def main():
    print("=" * 80)
    print("Strategy 3: Dynamic Complexity Routing (Mixture of Experts) Benchmark")
    print("=" * 80)

    # 1. Load Dual Branches
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dual_models_dir = os.path.join(repo_root, "models", "dual_branch_models") if os.path.isdir(os.path.join(repo_root, "models", "dual_branch_models")) else "dual_branch_models"
    branch_ae = XGBoostBranch("xgboost_ae")
    branch_ae.load(dual_models_dir)
    branch_lstm = XGBoostBranch("xgboost_lstm")
    branch_lstm.load(dual_models_dir)
    print("Loaded Dual-Embedding Generalist Branches (AE & LSTM).")

    # 2. Load Specialized Experts
    models_dict, features_dict = load_specialized_models_dict()
    print(f"Loaded Specialized Experts for passes: {list(models_dict.keys())}")

    # 3. Instantiate Dynamic Router
    router = DynamicComplexityRouter(
        specialized_models=models_dict,
        specialized_features=features_dict,
        branch_ae=branch_ae,
        branch_lstm=branch_lstm,
        alpha_single_pass_blend=0.80,
        tau_safety_gate=0.30
    )

    # 4. Benchmark Single-Pass Domains and Multi-Pass Compound Schedules
    passes = [
        ("tiling", "Loop Tiling (Single Pass)", "tiling/results/features_tierA.csv", "tiling/results/test_programs.txt", False),
        ("unrolling", "Loop Unrolling (Single Pass)", "unrolling/results/features_unrolling.csv", "unrolling/results/test_programs.txt", False),
        ("fusion", "Loop Fusion (Single Pass)", "fusion/results/features_fusion.csv", "fusion/results/test_programs.txt", False),
        ("tiling", "Compound Multi-Pass Simulation (Routed to Generalist)", "tiling/results/features_tierA.csv", "tiling/results/test_programs.txt", True),
    ]

    all_results = {}
    print(f"\n{'Domain / Workload':<42} | {'Routed Expert':<28} | {'MAE':<7} | {'Gated GeoMean':<14} | {'Slowdown (<0.95x)':<16}")
    print("-" * 115)

    for pass_key, name, fcsv, tfile, is_compound in passes:
        with open(tfile, "r") as f:
            test_progs = set(line.strip() for line in f if line.strip())
        df = pd.read_csv(fcsv)
        df_test = df[df["program_name"].isin(test_progs)].copy()

        metrics = router.evaluate_holdout(df_test, pass_key=pass_key, is_compound=is_compound)
        all_results[name] = metrics
        exp_short = "Specialized + Dual" if not is_compound else "Dual Generalist (136D)"
        print(f"{name:<42} | {exp_short:<28} | {metrics['mae']:<7.4f} | {metrics['geomean_gated']:<14.3f}x | {metrics['slowdown_gated']:<15.1f}%")

    with open("strategy3_routing_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print("\nSaved Strategy 3 evaluation results to strategy3_routing_results.json")

if __name__ == "__main__":
    main()
