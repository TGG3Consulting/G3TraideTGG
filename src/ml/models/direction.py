# -*- coding: utf-8 -*-
"""
Direction Classifier for ML System.

Predicts market direction (long/short/neutral) using LightGBM.

Usage:
    classifier = DirectionClassifier()
    classifier.fit(X_train, y_train)
    predictions = classifier.predict(X_test)
    probabilities = classifier.predict_proba(X_test)
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import structlog

from config.settings import settings
from .base import BaseModel, ClassifierMixin


logger = structlog.get_logger(__name__)


class DirectionClassifier(BaseModel, ClassifierMixin):
    """
    Classifier for predicting trade direction.

    Classes:
    - 0: Neutral (no trade)
    - 1: Long
    - -1: Short

    Uses LightGBM with class weighting for imbalanced data.
    """

    def __init__(self, name: str = "direction_classifier"):
        """Initialize direction classifier."""
        super().__init__(name)
        self._classes: List[int] = [-1, 0, 1]  # short, neutral, long
        self._class_to_idx: Dict[int, int] = {-1: 0, 0: 1, 1: 2}

    def fit(
        self,
        X: Union[np.ndarray, pd.DataFrame],
        y: Union[np.ndarray, pd.Series],
        X_val: Optional[Union[np.ndarray, pd.DataFrame]] = None,
        y_val: Optional[Union[np.ndarray, pd.Series]] = None,
        **kwargs,
    ) -> "DirectionClassifier":
        """
        Fit classifier to training data.

        Args:
            X: Training features
            y: Training labels (-1, 0, 1)
            X_val: Optional validation features
            y_val: Optional validation labels
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

        # Map labels to 0, 1, 2 for LightGBM
        y_mapped = np.array([self._class_to_idx[label] for label in y_arr])

        logger.info(
            "fitting_direction_classifier",
            n_samples=len(X_arr),
            n_features=X_arr.shape[1],
            class_distribution={
                "short": int((y_arr == -1).sum()),
                "neutral": int((y_arr == 0).sum()),
                "long": int((y_arr == 1).sum()),
            },
        )

        # Build LightGBM parameters
        params = {
            "objective": "multiclass",
            "num_class": 3,
            "boosting_type": "gbdt",
            "n_estimators": self._config.n_estimators,
            "max_depth": self._config.max_depth,
            "learning_rate": self._config.learning_rate,
            "min_child_samples": self._config.min_samples_leaf,
            "class_weight": "balanced",
            "random_state": 42,
            "verbose": -1,
            "n_jobs": -1,
        }
        params.update(kwargs)

        # Create and fit model
        self._model = lgb.LGBMClassifier(**params)

        # Prepare callbacks for early stopping
        callbacks = []
        eval_set = None

        if X_val is not None and y_val is not None:
            X_val_arr = self._validate_features(X_val)
            y_val_mapped = np.array([self._class_to_idx[label] for label in y_val])
            eval_set = [(X_val_arr, y_val_mapped)]

            early_stopping = self._config.cv_folds if hasattr(self._config, "cv_folds") else 50
            callbacks.append(
                lgb.early_stopping(stopping_rounds=early_stopping, verbose=False)
            )

        self._model.fit(
            X_arr,
            y_mapped,
            eval_set=eval_set,
            callbacks=callbacks if callbacks else None,
        )

        self._is_fitted = True
        self._fit_timestamp = datetime.now(timezone.utc)

        logger.info(
            "direction_classifier_fitted",
            n_iterations=self._model.n_estimators_,
            best_iteration=getattr(self._model, "best_iteration_", None),
        )

        return self

    def predict(self, X: Union[np.ndarray, pd.DataFrame]) -> np.ndarray:
        """
        Predict direction classes.

        Args:
            X: Features

        Returns:
            Array of direction labels (-1, 0, 1)
        """
        if not self._is_fitted:
            raise ValueError("Model not fitted. Call fit() first.")

        X_arr = self._validate_features(X)
        proba = self._model.predict_proba(X_arr)

        # Get predicted class indices and map back to labels
        pred_indices = np.argmax(proba, axis=1)
        idx_to_class = {0: -1, 1: 0, 2: 1}
        predictions = np.array([idx_to_class[idx] for idx in pred_indices])

        return predictions

    def predict_proba(self, X: Union[np.ndarray, pd.DataFrame]) -> np.ndarray:
        """
        Predict class probabilities.

        Args:
            X: Features

        Returns:
            Probability array (n_samples, 3) for [short, neutral, long]
        """
        if not self._is_fitted:
            raise ValueError("Model not fitted. Call fit() first.")

        X_arr = self._validate_features(X)
        return self._model.predict_proba(X_arr)

    def predict_direction_with_confidence(
        self,
        X: Union[np.ndarray, pd.DataFrame],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Predict direction with confidence scores.

        Confidence is the probability of the predicted class.

        Args:
            X: Features

        Returns:
            Tuple of (directions, confidences)
        """
        proba = self.predict_proba(X)

        # Get max probability and corresponding class
        confidences = np.max(proba, axis=1)
        pred_indices = np.argmax(proba, axis=1)

        idx_to_class = {0: -1, 1: 0, 2: 1}
        directions = np.array([idx_to_class[idx] for idx in pred_indices])

        return directions, confidences

    def get_directional_probabilities(
        self,
        X: Union[np.ndarray, pd.DataFrame],
    ) -> Dict[str, np.ndarray]:
        """
        Get probabilities for each direction.

        Args:
            X: Features

        Returns:
            Dict with 'short', 'neutral', 'long' probability arrays
        """
        proba = self.predict_proba(X)

        return {
            "short": proba[:, 0],
            "neutral": proba[:, 1],
            "long": proba[:, 2],
        }
