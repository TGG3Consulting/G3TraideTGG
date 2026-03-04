# -*- coding: utf-8 -*-
"""
ML Models module - prediction models.

Components:
- BaseModel: Abstract base class for all models
- DirectionClassifier: Predicts trade direction (long/short/neutral)
- SLRegressor, TPRegressor: Predict stop-loss and take-profit levels
- ConfidenceCalibrator: Calibrates prediction confidence
- ModelEnsemble: Combines all models into unified interface
- ModelExplainer: SHAP-based model explainability
"""

from .base import BaseModel, ClassifierMixin, RegressorMixin
from .direction import DirectionClassifier
from .levels import LevelRegressor, SLRegressor, TPRegressor, MultiTargetLevelRegressor
from .lifetime import LifetimeRegressor
from .confidence import ConfidenceCalibrator, DirectionalConfidenceCalibrator
from .ensemble import ModelEnsemble
from .explainer import ModelExplainer, Explanation, FeatureContribution

__all__ = [
    # Base
    "BaseModel",
    "ClassifierMixin",
    "RegressorMixin",
    # Direction
    "DirectionClassifier",
    # Levels
    "LevelRegressor",
    "SLRegressor",
    "TPRegressor",
    "MultiTargetLevelRegressor",
    # Lifetime
    "LifetimeRegressor",
    # Confidence
    "ConfidenceCalibrator",
    "DirectionalConfidenceCalibrator",
    # Ensemble
    "ModelEnsemble",
    # Explainability
    "ModelExplainer",
    "Explanation",
    "FeatureContribution",
]
