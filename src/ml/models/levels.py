# -*- coding: utf-8 -*-
"""
Level Regressors for ML System.

Predicts optimal SL and TP levels using LightGBM regression.

Usage:
    sl_regressor = SLRegressor()
    sl_regressor.fit(X_train, y_sl_train)
    sl_predictions = sl_regressor.predict(X_test)

    tp_regressor = TPRegressor()
    tp_regressor.fit(X_train, y_tp_train)
    tp_predictions = tp_regressor.predict(X_test)
"""

from datetime import datetime, timezone
from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from .base import BaseModel, RegressorMixin


logger = structlog.get_logger(__name__)


class LevelRegressor(BaseModel, RegressorMixin):
    """
    Base regressor for price levels (SL/TP).

    Predicts percentage distance from entry price.
    """

    def __init__(self, name: str, min_value: float = 0.0, max_value: float = 100.0):
        """
        Initialize level regressor.

        Args:
            name: Model name
            min_value: Minimum allowed prediction
            max_value: Maximum allowed prediction
        """
        super().__init__(name)
        self._min_value = min_value
        self._max_value = max_value

    def fit(
        self,
        X: Union[np.ndarray, pd.DataFrame],
        y: Union[np.ndarray, pd.Series],
        X_val: Optional[Union[np.ndarray, pd.DataFrame]] = None,
        y_val: Optional[Union[np.ndarray, pd.Series]] = None,
        **kwargs,
    ) -> "LevelRegressor":
        """
        Fit regressor to training data.

        Args:
            X: Training features
            y: Training targets (percentage values)
            X_val: Optional validation features
            y_val: Optional validation targets
            **kwargs: Additional LightGBM parameters

        Returns:
            Self
        """
        try:
            import lightgbm as lgb
        except ImportError:
            logger.error("lightgbm_not_installed")
            raise ImportError("LightGBM required: pip install lightgbm")

        X_arr = self._validate_features(X)
        y_arr = np.asarray(y)

        logger.info(
            "fitting_level_regressor",
            model_name=self._name,
            n_samples=len(X_arr),
            n_features=X_arr.shape[1],
            y_mean=float(np.mean(y_arr)),
            y_std=float(np.std(y_arr)),
        )

        # Build LightGBM parameters
        params = {
            "objective": "regression",
            "boosting_type": "gbdt",
            "n_estimators": self._config.n_estimators,
            "max_depth": self._config.max_depth,
            "learning_rate": self._config.learning_rate,
            "min_child_samples": self._config.min_samples_leaf,
            "random_state": 42,
            "verbose": -1,
            "n_jobs": -1,
        }
        params.update(kwargs)

        # Create and fit model
        self._model = lgb.LGBMRegressor(**params)

        # Prepare callbacks for early stopping
        callbacks = []
        eval_set = None

        if X_val is not None and y_val is not None:
            X_val_arr = self._validate_features(X_val)
            y_val_arr = np.asarray(y_val)
            eval_set = [(X_val_arr, y_val_arr)]

            early_stopping = settings.ml.training.early_stopping_rounds
            callbacks.append(
                lgb.early_stopping(stopping_rounds=early_stopping, verbose=False)
            )

        self._model.fit(
            X_arr,
            y_arr,
            eval_set=eval_set,
            callbacks=callbacks if callbacks else None,
        )

        self._is_fitted = True
        self._fit_timestamp = datetime.now(timezone.utc)

        logger.info(
            "level_regressor_fitted",
            model_name=self._name,
            n_iterations=self._model.n_estimators_,
        )

        return self

    def predict(self, X: Union[np.ndarray, pd.DataFrame]) -> np.ndarray:
        """
        Predict level values.

        Args:
            X: Features

        Returns:
            Array of level predictions (clipped to valid range)
        """
        if not self._is_fitted:
            raise ValueError("Model not fitted. Call fit() first.")

        X_arr = self._validate_features(X)
        predictions = self._model.predict(X_arr)

        # Clip to valid range
        predictions = np.clip(predictions, self._min_value, self._max_value)

        return predictions

    def predict_with_uncertainty(
        self,
        X: Union[np.ndarray, pd.DataFrame],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Predict with uncertainty using tree variance.

        Args:
            X: Features

        Returns:
            Tuple of (predictions, uncertainties)
        """
        if not self._is_fitted:
            raise ValueError("Model not fitted. Call fit() first.")

        X_arr = self._validate_features(X)

        # Get predictions from all trees
        booster = self._model.booster_
        n_trees = booster.num_trees()

        # Collect predictions from each tree
        tree_predictions = []
        for i in range(n_trees):
            pred = booster.predict(X_arr, start_iteration=i, num_iteration=1)
            tree_predictions.append(pred)

        tree_predictions = np.array(tree_predictions)

        # Mean prediction
        predictions = np.mean(tree_predictions, axis=0)
        predictions = np.clip(predictions, self._min_value, self._max_value)

        # Uncertainty as standard deviation across trees
        uncertainties = np.std(tree_predictions, axis=0)

        return predictions, uncertainties


class SLRegressor(LevelRegressor):
    """
    Stop-Loss level regressor.

    Predicts optimal SL distance (% from entry).
    """

    def __init__(self, name: str = "sl_regressor"):
        """Initialize SL regressor."""
        # SL typically 0.1% to 10%
        super().__init__(name, min_value=0.1, max_value=10.0)


class TPRegressor(LevelRegressor):
    """
    Take-Profit level regressor.

    Predicts optimal TP distance (% from entry).
    """

    def __init__(self, name: str = "tp_regressor"):
        """Initialize TP regressor."""
        # TP typically 0.2% to 20%
        super().__init__(name, min_value=0.2, max_value=20.0)


class MultiTargetLevelRegressor(BaseModel, RegressorMixin):
    """
    Multi-target regressor for predicting SL, TP1, TP2, TP3 together.

    Can capture correlations between different targets.
    """

    def __init__(self, name: str = "multi_level_regressor"):
        """Initialize multi-target regressor."""
        super().__init__(name)
        self._target_names = ["sl_pct", "tp1_pct", "tp2_pct", "tp3_pct"]
        self._models = {}

    def fit(
        self,
        X: Union[np.ndarray, pd.DataFrame],
        y: Union[np.ndarray, pd.DataFrame],
        X_val: Optional[Union[np.ndarray, pd.DataFrame]] = None,
        y_val: Optional[Union[np.ndarray, pd.DataFrame]] = None,
        **kwargs,
    ) -> "MultiTargetLevelRegressor":
        """
        Fit regressors for all targets.

        Args:
            X: Training features
            y: Training targets (DataFrame with sl_pct, tp1_pct, tp2_pct, tp3_pct)
            X_val: Optional validation features
            y_val: Optional validation targets
            **kwargs: Additional parameters

        Returns:
            Self
        """
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("LightGBM required: pip install lightgbm")

        X_arr = self._validate_features(X)

        if isinstance(y, pd.DataFrame):
            y_dict = {col: y[col].values for col in y.columns}
        else:
            y_arr = np.asarray(y)
            y_dict = {
                self._target_names[i]: y_arr[:, i]
                for i in range(min(y_arr.shape[1], len(self._target_names)))
            }

        logger.info(
            "fitting_multi_target_regressor",
            n_samples=len(X_arr),
            targets=list(y_dict.keys()),
        )

        # Fit separate model for each target
        for target_name, y_target in y_dict.items():
            params = {
                "objective": "regression",
                "boosting_type": "gbdt",
                "n_estimators": self._config.n_estimators,
                "max_depth": self._config.max_depth,
                "learning_rate": self._config.learning_rate,
                "random_state": 42,
                "verbose": -1,
                "n_jobs": -1,
            }
            params.update(kwargs)

            model = lgb.LGBMRegressor(**params)

            eval_set = None
            callbacks = []

            if X_val is not None and y_val is not None:
                X_val_arr = self._validate_features(X_val)
                if isinstance(y_val, pd.DataFrame):
                    y_val_target = y_val[target_name].values
                else:
                    idx = self._target_names.index(target_name)
                    y_val_target = np.asarray(y_val)[:, idx]
                eval_set = [(X_val_arr, y_val_target)]
                callbacks.append(
                    lgb.early_stopping(
                        stopping_rounds=settings.ml.training.early_stopping_rounds,
                        verbose=False,
                    )
                )

            model.fit(
                X_arr,
                y_target,
                eval_set=eval_set,
                callbacks=callbacks if callbacks else None,
            )

            self._models[target_name] = model

            logger.debug(
                "target_model_fitted",
                target=target_name,
                n_iterations=model.n_estimators_,
            )

        self._is_fitted = True
        self._fit_timestamp = datetime.now(timezone.utc)

        return self

    def predict(self, X: Union[np.ndarray, pd.DataFrame]) -> np.ndarray:
        """
        Predict all level targets.

        Args:
            X: Features

        Returns:
            Array of shape (n_samples, n_targets)
        """
        if not self._is_fitted:
            raise ValueError("Model not fitted. Call fit() first.")

        X_arr = self._validate_features(X)

        predictions = []
        for target_name in self._target_names:
            if target_name in self._models:
                pred = self._models[target_name].predict(X_arr)
                predictions.append(pred)

        return np.column_stack(predictions) if predictions else np.array([])

    def predict_target(
        self,
        X: Union[np.ndarray, pd.DataFrame],
        target: str,
    ) -> np.ndarray:
        """
        Predict specific target.

        Args:
            X: Features
            target: Target name (sl_pct, tp1_pct, etc.)

        Returns:
            Predictions for that target
        """
        if not self._is_fitted:
            raise ValueError("Model not fitted. Call fit() first.")

        if target not in self._models:
            raise ValueError(f"Unknown target: {target}")

        X_arr = self._validate_features(X)
        return self._models[target].predict(X_arr)

    def save(self, path: Optional[str] = None) -> str:
        """Save all models."""
        import pickle
        from pathlib import Path

        if path is None:
            save_dir = Path(self._config.save_dir)
            save_dir.mkdir(parents=True, exist_ok=True)
            path = str(save_dir / f"{self._name}.pkl")

        data = {
            "name": self._name,
            "models": self._models,
            "target_names": self._target_names,
            "feature_names": self._feature_names,
            "is_fitted": self._is_fitted,
            "fit_timestamp": self._fit_timestamp,
        }

        with open(path, "wb") as f:
            pickle.dump(data, f)

        logger.info("multi_target_model_saved", path=path)
        return path

    def load(self, path: str) -> "MultiTargetLevelRegressor":
        """Load all models."""
        import pickle

        with open(path, "rb") as f:
            data = pickle.load(f)

        self._name = data["name"]
        self._models = data["models"]
        self._target_names = data["target_names"]
        self._feature_names = data["feature_names"]
        self._is_fitted = data["is_fitted"]
        self._fit_timestamp = data.get("fit_timestamp")

        logger.info("multi_target_model_loaded", path=path)
        return self
