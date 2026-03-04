# -*- coding: utf-8 -*-
"""
CX-001: Price Divergence Detector.

Detects abnormal price divergence between exchanges that may indicate:
- Manipulation on low-liquidity exchange (pump before arbitrage)
- Front-running arbitrage bots
- Technical issues on specific exchange
- Coordinated pump/dump starting on small exchange

Pattern:
1. Price spikes on smaller exchange (MEXC, BingX)
2. Larger exchanges (Binance, Bybit) lag behind
3. Arbitrageurs profit from the gap
4. Possible coordinated manipulation if this repeats

Thresholds (configurable):
- WARNING: spread > 0.5%
- ALERT: spread > 1.0%
- CRITICAL: spread > 2.0%
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from src.cross_exchange.detectors.base import (
    BaseCrossDetector,
    Detection,
    DetectionType,
    DetectorConfig,
    Severity,
)

if TYPE_CHECKING:
    from src.cross_exchange.state_store import StateStore


@dataclass
class PriceDivergenceConfig(DetectorConfig):
    """Configuration for price divergence detector."""
    enabled: bool = True
    dedup_seconds: int = 30

    # Spread thresholds (as decimal, e.g., 0.005 = 0.5%)
    warning_threshold: float = 0.005   # 0.5%
    alert_threshold: float = 0.01      # 1.0%
    critical_threshold: float = 0.02   # 2.0%

    # Minimum exchanges required
    min_exchanges: int = 2

    # Price must be fresh (seconds)
    max_price_age_sec: int = 10


class PriceDivergenceDetector(BaseCrossDetector):
    """
    Detects abnormal price divergence between exchanges.

    This detector compares prices across all exchanges and identifies
    when the spread exceeds configurable thresholds.
    """

    DETECTION_TYPE = DetectionType.PRICE_DIVERGENCE
    NAME = "price_divergence"

    def __init__(self, config: PriceDivergenceConfig = None):
        super().__init__(config or PriceDivergenceConfig())
        self.config: PriceDivergenceConfig = self.config

    async def analyze(
        self,
        symbol: str,
        state: "StateStore"
    ) -> Optional[Detection]:
        """
        Analyze price divergence for a symbol.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            state: Cross-exchange state store

        Returns:
            Detection if significant divergence found
        """
        # Get price spread data
        spreads = state.get_price_spread(symbol)

        if not spreads or "_max" not in spreads:
            return None

        max_spread = float(spreads.get("_max", 0))

        # Check if spread exceeds warning threshold
        if max_spread < self.config.warning_threshold * 100:  # spreads are in %
            return None

        # Find the divergent pair
        divergent_pair, spread_value = self._find_max_divergent_pair(spreads)
        if not divergent_pair:
            return None

        # Determine severity
        spread_decimal = spread_value / 100  # Convert % to decimal
        severity = self._get_severity(spread_decimal)

        # Get additional context
        cross_price = state.get_cross_price(symbol)
        exchanges = list(cross_price.prices.keys())
        prices_str = {ex: str(p) for ex, p in cross_price.prices.items()}

        # Calculate confidence
        confidence = self._calculate_confidence(
            spread_decimal,
            self.config.warning_threshold,
            self.config.critical_threshold * 2
        )

        # Determine likely cause
        cause = self._analyze_cause(cross_price.prices, cross_price.volumes)

        # Create detection
        low_ex, high_ex = divergent_pair.split("_")
        description = (
            f"Price divergence of {spread_value:.3f}% between {low_ex} and {high_ex}. "
            f"{cause}"
        )

        detection = self._create_detection(
            symbol=symbol,
            severity=severity,
            exchanges=exchanges,
            description=description,
            details={
                "max_spread_pct": spread_value,
                "divergent_pair": divergent_pair,
                "all_spreads": {k: float(v) for k, v in spreads.items() if not k.startswith("_")},
                "prices": prices_str,
                "low_exchange": low_ex,
                "high_exchange": high_ex,
                "likely_cause": cause,
            },
            confidence=confidence,
            recommended_action=self._get_recommendation(severity, low_ex, high_ex)
        )

        # Smart deduplication
        if not self._should_alert(detection):
            return None

        self._record_alert(detection)
        return detection

    def _find_max_divergent_pair(
        self,
        spreads: Dict[str, Any]
    ) -> Tuple[Optional[str], float]:
        """Find the pair with maximum spread."""
        max_pair = None
        max_spread = 0.0

        for key, value in spreads.items():
            if key.startswith("_"):
                continue
            try:
                spread = float(value)
                if spread > max_spread:
                    max_spread = spread
                    max_pair = key
            except (TypeError, ValueError):
                continue

        return max_pair, max_spread

    def _analyze_cause(
        self,
        prices: Dict[str, Decimal],
        volumes: Dict[str, Decimal]
    ) -> str:
        """Analyze likely cause of divergence."""
        if not prices or len(prices) < 2:
            return "Insufficient data"

        # Find highest and lowest price exchanges
        sorted_prices = sorted(prices.items(), key=lambda x: x[1])
        lowest_ex, lowest_price = sorted_prices[0]
        highest_ex, highest_price = sorted_prices[-1]

        # Check volume context
        low_volume_ex = volumes.get(lowest_ex, Decimal(0))
        high_volume_ex = volumes.get(highest_ex, Decimal(0))

        # Heuristics
        if high_volume_ex > 0 and low_volume_ex > 0:
            volume_ratio = high_volume_ex / low_volume_ex if low_volume_ex > 0 else float('inf')

            if volume_ratio > 10:
                return f"Low liquidity on {lowest_ex} may cause price lag"
            elif volume_ratio < 0.1:
                return f"Low liquidity on {highest_ex} may cause price spike"

        # Check for pump pattern (one exchange significantly higher)
        avg_price = sum(prices.values()) / len(prices)
        deviation = (highest_price - avg_price) / avg_price * 100

        if deviation > 1:
            return f"Possible pump on {highest_ex} - price {deviation:.2f}% above average"

        return "Arbitrage opportunity detected"

    def _get_recommendation(
        self,
        severity: Severity,
        low_ex: str,
        high_ex: str
    ) -> str:
        """Get action recommendation based on severity."""
        if severity == Severity.CRITICAL:
            return (
                f"CRITICAL: Investigate immediately. Potential manipulation detected. "
                f"Check {high_ex} for suspicious activity. Consider pausing trading."
            )
        elif severity == Severity.ALERT:
            return (
                f"Monitor closely. Large spread may indicate manipulation or "
                f"technical issues on {low_ex} or {high_ex}."
            )
        else:
            return "Normal arbitrage opportunity. Monitor for escalation."
