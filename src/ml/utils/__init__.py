# -*- coding: utf-8 -*-
"""
ML Utils module - utilities.

Components:
- DataQualityChecker: Validates data quality
- SlippageModel: Realistic slippage estimation
- MarketRegimeDetector: Bull/bear/sideways detection
- TailRiskManager: Black swan protection
- ModelMonitor: Production drift detection
- ModelSerializer: Model persistence
"""

from .validation import DataQualityChecker, DataQualityReport
from .market import (
    SlippageModel,
    SlippageEstimate,
    MarketRegimeDetector,
    MarketRegime,
    MarketImpactModel,
    FullTransactionCosts,
    Liquidity,
)
from .monitoring import (
    TailRiskManager,
    ModelMonitor,
    DrawdownAnalyzer,
    DrawdownMetrics,
)
from .serialization import (
    ModelSerializer,
    ModelMetadata,
    save_ensemble,
    load_ensemble,
)

__all__ = [
    # Validation
    "DataQualityChecker",
    "DataQualityReport",
    # Market
    "SlippageModel",
    "SlippageEstimate",
    "MarketRegimeDetector",
    "MarketRegime",
    "MarketImpactModel",
    "FullTransactionCosts",
    "Liquidity",
    # Monitoring
    "TailRiskManager",
    "ModelMonitor",
    "DrawdownAnalyzer",
    "DrawdownMetrics",
    # Serialization
    "ModelSerializer",
    "ModelMetadata",
    "save_ensemble",
    "load_ensemble",
]
