# -*- coding: utf-8 -*-
"""
ML System for BinanceFriend.

Provides machine learning-based signal optimization including:
- Direction prediction
- SL/TP level optimization
- Confidence calibration
- Risk management
- Production monitoring

Usage:
    from src.ml import MLIntegration

    integration = MLIntegration(futures_monitor, state_store)
    await integration.initialize()
    optimized = await integration.optimize_signal(signal)

Modules:
- data: Data collection and preprocessing
- features: Feature engineering
- models: ML models (classifier, regressors, ensemble)
- training: Training pipeline with validation
- optimization: Signal optimization
- risk: Risk management
- utils: Market utilities, monitoring, validation
- integration: Main entry point
"""

from config.settings import settings

# Re-export config for convenience
ml_config = settings.ml

# Main entry point
from .integration import MLIntegration, MLService

# Core components
from .data import (
    HistoricalDataCollector,
    DataPreprocessor,
    MarketSnapshot,
    FeatureVector,
    PredictionResult,
    OptimizedSignal,
)

from .features import (
    FeatureEngineer,
    TechnicalIndicators,
)

from .models import (
    DirectionClassifier,
    SLRegressor,
    TPRegressor,
    LifetimeRegressor,
    ConfidenceCalibrator,
    ModelEnsemble,
)

from .training import (
    Labeler,
    Trainer,
    Evaluator,
    ModelValidator,
    EvaluationMetrics,
    TrainingPipeline,
)

from .optimization import (
    SignalOptimizer,
    OptimalParamCalculator,
)

from .risk import (
    PositionSizer,
    RiskManager,
    LimitChecker,
    CorrelationFilter,
    CorrelationCheckResult,
    OpportunityCostCalculator,
    OpportunityCostMetrics,
)

from .config import MLConfig

from .utils import (
    DataQualityChecker,
    SlippageModel,
    MarketRegimeDetector,
    MarketRegime,
    TailRiskManager,
    ModelMonitor,
)

__all__ = [
    # Config
    "ml_config",
    # Integration
    "MLIntegration",
    "MLService",
    # Data
    "HistoricalDataCollector",
    "DataPreprocessor",
    "MarketSnapshot",
    "FeatureVector",
    "PredictionResult",
    "OptimizedSignal",
    # Features
    "FeatureEngineer",
    "TechnicalIndicators",
    # Models
    "DirectionClassifier",
    "SLRegressor",
    "TPRegressor",
    "LifetimeRegressor",
    "ConfidenceCalibrator",
    "ModelEnsemble",
    # Training
    "Labeler",
    "Trainer",
    "Evaluator",
    "ModelValidator",
    "EvaluationMetrics",
    "TrainingPipeline",
    # Optimization
    "SignalOptimizer",
    "OptimalParamCalculator",
    # Risk
    "PositionSizer",
    "RiskManager",
    "LimitChecker",
    "CorrelationFilter",
    "CorrelationCheckResult",
    "OpportunityCostCalculator",
    "OpportunityCostMetrics",
    # Config
    "MLConfig",
    # Utils
    "DataQualityChecker",
    "SlippageModel",
    "MarketRegimeDetector",
    "MarketRegime",
    "TailRiskManager",
    "ModelMonitor",
]
