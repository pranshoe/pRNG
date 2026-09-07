import numpy as np
import xgboost as xgb
import os
from typing import List, Dict, Any, Optional
from dual_ensemble.config import NUM_ENSEMBLE_MODELS, XGB_PARAMS

class XGBoostBranch:
    def __init__(self, name: str, num_models: int = NUM_ENSEMBLE_MODELS, params: Optional[Dict[str, Any]] = None):
        self.name = name
        self.num_models = num_models
        self.params = params or XGB_PARAMS.copy()
        self.models: List[xgb.XGBRegressor] = []

    def fit(self, X: np.ndarray, y: np.ndarray, save_dir: str = "saved_models") -> None:
        """
        Trains 10 distinct XGBoost regressors with varying random states / bootstrap samples.
        Saves each model checkpoint immediately upon completion.
        """
        os.makedirs(save_dir, exist_ok=True)
        self.models = []
        n_samples = X.shape[0]
        for i in range(self.num_models):
            model_params = self.params.copy()
            model_params["random_state"] = self.params.get("random_state", 42) + i * 10
            
            # Bootstrap sample for bagging
            indices = np.random.RandomState(model_params["random_state"]).choice(n_samples, size=n_samples, replace=True)
            X_boot, y_boot = X[indices], y[indices]

            reg = xgb.XGBRegressor(**model_params)
            reg.fit(X_boot, y_boot)
            self.models.append(reg)
            
            # Save checkpoint immediately
            reg.save_model(os.path.join(save_dir, f"{self.name}_model_{i}.json"))
            print(f"  [{self.name}] Trained & checkpointed model {i+1}/{self.num_models}", flush=True)

    def predict_individual(self, X: np.ndarray) -> np.ndarray:
        """
        Returns predictions from all individual models in the branch. Shape: (num_models, N)
        """
        return np.array([model.predict(X) for model in self.models])

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Returns the mean prediction across all 10 models in this branch. Shape: (N,)
        """
        preds = self.predict_individual(X)
        return np.mean(preds, axis=0)

    def save(self, directory: str) -> None:
        os.makedirs(directory, exist_ok=True)
        for i, model in enumerate(self.models):
            model.save_model(os.path.join(directory, f"{self.name}_model_{i}.json"))

    def load(self, directory: str) -> None:
        self.models = []
        for i in range(self.num_models):
            model_path = os.path.join(directory, f"{self.name}_model_{i}.json")
            if os.path.exists(model_path):
                model = xgb.XGBRegressor()
                model.load_model(model_path)
                self.models.append(model)
