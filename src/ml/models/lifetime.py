# -*- coding: utf-8 -*-
"""
Lifetime Regressor for ML System.

Predicts how long a signal will remain valid (hours until TP/SL hit).

Usage:
    regressor = LifetimeRegressor()
    regressor.fit(X_train, y_lifetime_hours)
    predicted_hours = regressor.predict(X_test)
"""

from datetime import datetime, timezone
from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from .base import BaseModel, RegressorMixin


logger = structlog.get_logger(__name__)


class LifetimeRegressor(BaseModel, RegressorMixin):
    """
    Predicts signal lifetime (time until resolution).

    Useful for:
    - Setting appropriate valid_hours
    - Opportunity cost calculation
    - Position management
    """

    def __init__(self, name: str = "lifetime_regressor"):
        """Initialize lifetime regressor."""
        super().__init__(name)
        self._min_hours = 0.5  # Minimum 30 minutes
        self._max_hours = 168  # Maximum 1 week

    def fit(
        self,
        X: Union[np.ndarray, pd.DataFrame],
        y: Union[np.ndarray, pd.Series],
        X_val: Optional[Union[np.ndarray, pd.DataFrame]] = None,
        y_val: Optional[Union[np.ndarray, pd.Series]] = None,
        **kwargs,
    ) -> "LifetimeRegressor":
        """
        Fit regressor to training data.

        Args:
            X: Training features
            y: Training targets (hours until resolution)
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

        # Log-transform target for better regression
        y_log = np.log1p(y_arr)

        logger.info(
            "fitting_lifetime_regressor",
            n_samples=len(X_arr),
            n_features=X_arr.shape[1],
            y_mean_hours=float(np.mean(y_arr)),
            y_median_hours=float(np.median(y_arr)),
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

        self._model = lgb.LGBMRegressor(**params)

        callbacks = []
        eval_set = None

        if X_val is not None and y_val is not None:
            X_val_arr = self._validate_features(X_val)
            y_val_log = np.log1p(np.asarray(y_val))
            eval_set = [(X_val_arr, y_val_log)]
            callbacks.append(
                lgb.early_stopping(
                    stopping_rounds=settings.ml.training.early_stopping_rounds,
                    verbose=False,
                )
            )

        self._model.fit(
            X_arr,
            y_log,
            eval_set=eval_set,
            callbacks=callbacks if callbacks else None,
        )

        self._is_fitted = True
        self._fit_timestamp = datetime.now(timezone.utc)

        logger.info(
            "lifetime_regressor_fitted",
            n_iterations=self._model.n_estimators_,
        )

        return self

    def predict(self, X: Union[np.ndarray, pd.DataFrame]) -> np.ndarray:
        """
        Predict lifetime in hours.

        Args:
            X: Features

        Returns:
            Array of predicted hours (clipped to valid range)
        """
        if not self._is_fitted:
            raise ValueError("Model not fitted. Call fit() first.")

        X_arr = self._validate_features(X)

        # Predict log-transformed, then inverse
        y_log_pred = self._model.predict(X_arr)
        predictions = np.expm1(y_log_pred)

        # Clip to valid range
        predictions = np.clip(predictions, self._min_hours, self._max_hours)

        return predictions

    def predict_with_confidence(
        self,
        X: Union[np.ndarray, pd.DataFrame],
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Predict lifetime with confidence intervals.

        Args:
            X: Features

        Returns:
            Tuple of (predictions, lower_bound, upper_bound)
        """
        predictions = self.predict(X)

        # Estimate uncertainty based on prediction magnitude
        # Higher predicted lifetime = higher uncertainty
        uncertainty = predictions * 0.3  # 30% relative uncertainty

        lower = np.maximum(self._min_hours, predictions - uncertainty)
        upper = np.minimum(self._max_hours, predictions + uncertainty)

        return predictions, lower, upper

    def categorize_lifetime(
        self,
        X: Union[np.ndarray, pd.DataFrame],
    ) -> np.ndarray:
        """
        Categorize predicted lifetime into bins.

        Categories:
        - 0: Very short (< 2h)
        - 1: Short (2-8h)
        - 2: Medium (8-24h)
        - 3: Long (24-72h)
        - 4: Very long (> 72h)

        Args:
            X: Features

        Returns:
            Array of category indices
        """
        hours = self.predict(X)

        categories = np.zeros(len(hours), dtype=int)
        categories[hours >= 2] = 1
        categories[hours >= 8] = 2
        categories[hours >= 24] = 3
        categories[hours >= 72] = 4

        return categories
