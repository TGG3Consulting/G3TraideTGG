# -*- coding: utf-8 -*-
"""
Base Model Classes for ML System.

Provides abstract interfaces and common functionality
for all ML models in the system.
"""

import pickle
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


class BaseModel(ABC):
    """
    Abstract base class for all ML models.

    Provides common functionality:
    - Save/load model
    - Feature importance
    - Logging
    """

    def __init__(self, name: str):
        """
        Initialize base model.

        Args:
            name: Model identifier
        """
        self._name = name
        self._config = settings.ml.models
        self._model: Optional[Any] = None
        self._feature_names: List[str] = []
        self._is_fitted: bool = False
        self._fit_timestamp: Optional[datetime] = None

        logger.info(
            "model_init",
            model_name=name,
            model_type=self._config.model_type,
        )

    @property
    def name(self) -> str:
        """Model name."""
        return self._name

    @property
    def is_fitted(self) -> bool:
        """Whether model has been fitted."""
        return self._is_fitted

    @property
    def feature_names(self) -> List[str]:
        """Feature names used for training."""
        return self._feature_names

    @abstractmethod
    def fit(
        self,
        X: Union[np.ndarray, pd.DataFrame],
        y: Union[np.ndarray, pd.Series],
        **kwargs,
    ) -> "BaseModel":
        """
        Fit model to training data.

        Args:
            X: Features
            y: Target
            **kwargs: Additional arguments

        Returns:
            Self
        """
        pass

    @abstractmethod
    def predict(self, X: Union[np.ndarray, pd.DataFrame]) -> np.ndarray:
        """
        Make predictions.

        Args:
            X: Features

        Returns:
            Predictions array
        """
        pass

    def get_feature_importance(self) -> Dict[str, float]:
        """
        Get feature importance scores.

        Returns:
            Dict mapping feature names to importance scores
        """
        if not self._is_fitted or self._model is None:
            return {}

        try:
            if hasattr(self._model, "feature_importances_"):
                importances = self._model.feature_importances_
                return dict(zip(self._feature_names, importances))
        except Exception as e:
            logger.warning("feature_importance_error", error=str(e))

        return {}

    def save(self, path: Optional[str] = None) -> str:
        """
        Save model to file.

        Args:
            path: Optional custom path

        Returns:
            Path where model was saved
        """
        if path is None:
            save_dir = Path(self._config.save_dir)
            save_dir.mkdir(parents=True, exist_ok=True)
            path = str(save_dir / f"{self._name}.pkl")

        data = {
            "name": self._name,
            "model": self._model,
            "feature_names": self._feature_names,
            "is_fitted": self._is_fitted,
            "fit_timestamp": self._fit_timestamp,
            "config": {
                "model_type": self._config.model_type,
                "n_estimators": self._config.n_estimators,
                "max_depth": self._config.max_depth,
            },
        }

        with open(path, "wb") as f:
            pickle.dump(data, f)

        logger.info("model_saved", path=path, model_name=self._name)
        return path

    def load(self, path: str) -> "BaseModel":
        """
        Load model from file.

        Args:
            path: Path to model file

        Returns:
            Self
        """
        with open(path, "rb") as f:
            data = pickle.load(f)

        self._name = data["name"]
        self._model = data["model"]
        self._feature_names = data["feature_names"]
        self._is_fitted = data["is_fitted"]
        self._fit_timestamp = data.get("fit_timestamp")

        logger.info(
            "model_loaded",
            path=path,
            model_name=self._name,
            is_fitted=self._is_fitted,
        )
        return self

    def _validate_features(
        self,
        X: Union[np.ndarray, pd.DataFrame],
    ) -> np.ndarray:
        """
        Validate and convert input features.

        Args:
            X: Input features

        Returns:
            Numpy array of features
        """
        if isinstance(X, pd.DataFrame):
            # Store feature names on first fit
            if not self._feature_names:
                self._feature_names = list(X.columns)
            return X.values
        return np.asarray(X)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self._name}', fitted={self._is_fitted})"


class ClassifierMixin:
    """Mixin for classifier models."""

    def predict_proba(self, X: Union[np.ndarray, pd.DataFrame]) -> np.ndarray:
        """
        Predict class probabilities.

        Args:
            X: Features

        Returns:
            Probability array (n_samples, n_classes)
        """
        raise NotImplementedError("Subclass must implement predict_proba")


class RegressorMixin:
    """Mixin for regressor models."""

    def predict_with_uncertainty(
        self,
        X: Union[np.ndarray, pd.DataFrame],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Predict with uncertainty estimates.

        Args:
            X: Features

        Returns:
            Tuple of (predictions, uncertainties)
        """
        # Default: no uncertainty estimation
        predictions = self.predict(X)
        uncertainties = np.zeros_like(predictions)
        return predictions, uncertainties
