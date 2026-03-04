# -*- coding: utf-8 -*-
"""
Cross-Exchange Detectors.

Individual detectors for specific manipulation patterns:
- CX-001: Price divergence (arbitrage opportunities)
- CX-002: Volume correlation (wash trading)
- CX-003: Funding arbitrage
- CX-004: OI migration (whale accumulation)
- CX-005: Liquidity hunting
- CX-006: Cross-exchange spoofing

Each detector implements a common interface and can be
enabled/disabled independently.
"""

from src.cross_exchange.detectors.base import (
    BaseCrossDetector,
    Detection,
    DetectionType,
    DetectorConfig,
    Severity,
)
from src.cross_exchange.detectors.price_divergence import (
    PriceDivergenceDetector,
    PriceDivergenceConfig,
)
from src.cross_exchange.detectors.volume_correlation import (
    VolumeCorrelationDetector,
    VolumeCorrelationConfig,
)
from src.cross_exchange.detectors.funding_arbitrage import (
    FundingArbitrageDetector,
    FundingArbitrageConfig,
)
from src.cross_exchange.detectors.oi_migration import (
    OIMigrationDetector,
    OIMigrationConfig,
)
from src.cross_exchange.detectors.liquidity_hunt import (
    LiquidityHuntDetector,
    LiquidityHuntConfig,
)
from src.cross_exchange.detectors.spoofing_cross import (
    SpoofingCrossDetector,
    SpoofingCrossConfig,
)

__all__ = [
    # Base
    "BaseCrossDetector",
    "Detection",
    "DetectionType",
    "DetectorConfig",
    "Severity",
    # CX-001: Price Divergence
    "PriceDivergenceDetector",
    "PriceDivergenceConfig",
    # CX-002: Volume Correlation
    "VolumeCorrelationDetector",
    "VolumeCorrelationConfig",
    # CX-003: Funding Arbitrage
    "FundingArbitrageDetector",
    "FundingArbitrageConfig",
    # CX-004: OI Migration
    "OIMigrationDetector",
    "OIMigrationConfig",
    # CX-005: Liquidity Hunt
    "LiquidityHuntDetector",
    "LiquidityHuntConfig",
    # CX-006: Spoofing Cross
    "SpoofingCrossDetector",
    "SpoofingCrossConfig",
]
