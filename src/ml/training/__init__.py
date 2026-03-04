# -*- coding: utf-8 -*-
"""
ML Training module - model training pipeline.

Components:
- OptimalMLPipeline: FULL pipeline with data loading + training (USE THIS!)
- OptimalParamsCalculator: Calculates OPTIMAL SL/TP/Lifetime from price history
- MLDataLoader: Loads klines, OI, funding with caching
- Labeler: Creates training labels from historical data (legacy)
- SignalLabeler: Creates labels from REAL backtest results
- Trainer: Trains models with cross-validation
- Evaluator: Evaluates model performance
"""

from .labeler import Labeler
from .signal_labeler import SignalLabeler, SignalLabels
from .optimal_params_calculator import OptimalParamsCalculator, OptimalParams
from .optimal_params_labeler import OptimalParamsLabeler
from .optimal_params_pipeline import OptimalParamsPipeline
from .optimal_ml_pipeline import OptimalMLPipeline, TrainingConfig
from .trainer import Trainer, TimeSeriesSplit
from .evaluator import Evaluator
from .validator import ModelValidator, ValidationReport
from .metrics import EvaluationMetrics, ClassificationMetrics, RegressionMetrics, TradingMetrics, CalibrationMetrics
from .pipeline import TrainingPipeline
from .signal_pipeline import SignalTrainingPipeline

__all__ = [
    # RECOMMENDED - Full Pipeline
    "OptimalMLPipeline",
    "TrainingConfig",
    # Optimal Parameters Calculator
    "OptimalParamsCalculator",
    "OptimalParams",
    "OptimalParamsLabeler",
    "OptimalParamsPipeline",
    # Labelers
    "Labeler",  # Legacy - from raw prices
    "SignalLabeler",  # From backtest results
    "SignalLabels",
    # Training
    "Trainer",
    "TimeSeriesSplit",
    "Evaluator",
    "ModelValidator",
    "ValidationReport",
    "EvaluationMetrics",
    "ClassificationMetrics",
    "RegressionMetrics",
    "TradingMetrics",
    "CalibrationMetrics",
    "TrainingPipeline",
]
