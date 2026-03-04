# -*- coding: utf-8 -*-
"""
CX-003: Funding Arbitrage Detector.

Detects abnormal funding rate divergence between exchanges that creates
arbitrage opportunities and may indicate:
- Impending price movement as rates converge
- Manipulation of funding rates on low-volume exchanges
- Whale position building (pushing rates negative)
- Liquidation hunting setup

Pattern:
1. Funding rate +0.1% on Binance (longs pay shorts)
2. Funding rate -0.05% on Bybit (shorts pay longs)
3. Arbitrageurs can long on Bybit, short on Binance
4. Rates will converge - anticipate price movement

Thresholds (configurable):
- WARNING: funding spread > 0.03%
- ALERT: funding spread > 0.05%
- CRITICAL: funding spread > 0.1%
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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
class FundingArbitrageConfig(DetectorConfig):
    """Configuration for funding arbitrage detector."""
    enabled: bool = True
    dedup_seconds: int = 30

    # Spread thresholds (as decimal, e.g., 0.0003 = 0.03%)
    warning_threshold: float = 0.0003   # 0.03%
    alert_threshold: float = 0.0005     # 0.05%
    critical_threshold: float = 0.001   # 0.1%

    # Minimum exchanges required
    min_exchanges: int = 2


class FundingArbitrageDetector(BaseCrossDetector):
    """
    Detects funding rate divergence creating arbitrage opportunities.

    Large funding spreads between exchanges can indicate:
    - Arbitrage opportunities
    - Directional bets building on specific exchanges
    - Potential price movements as rates converge
    """

    DETECTION_TYPE = DetectionType.FUNDING_ARBITRAGE
    NAME = "funding_arbitrage"

    def __init__(self, config: FundingArbitrageConfig = None):
        super().__init__(config or FundingArbitrageConfig())
        self.config: FundingArbitrageConfig = self.config

    async def analyze(
        self,
        symbol: str,
        state: "StateStore"
    ) -> Optional[Detection]:
        """
        Analyze funding rate divergence.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            state: Cross-exchange state store

        Returns:
            Detection if significant divergence found
        """
        # Get funding divergence data
        divergence = state.get_funding_divergence(symbol)

        if not divergence or "_spread" not in divergence:
            return None

        # Get spread (already calculated by state store)
        spread = float(divergence.get("_spread", 0))
        max_rate = float(divergence.get("_max", 0))
        min_rate = float(divergence.get("_min", 0))

        # Check if spread exceeds threshold
        if abs(spread) < self.config.warning_threshold:
            return None

        # Determine severity
        severity = self._get_severity(abs(spread))

        # Find the exchanges with highest/lowest rates
        long_exchange, short_exchange = self._find_arb_pair(divergence)

        # Calculate annualized yield
        annualized = abs(spread) * 3 * 365 * 100  # 3 funding/day, as percentage

        # Calculate confidence
        confidence = self._calculate_confidence(
            abs(spread),
            self.config.warning_threshold,
            self.config.critical_threshold * 2
        )

        # Analyze market implication
        implication = self._analyze_implication(max_rate, min_rate, spread)

        # Create description
        description = (
            f"Funding arbitrage: Long on {long_exchange} ({min_rate*100:.4f}%), "
            f"Short on {short_exchange} ({max_rate*100:.4f}%). "
            f"Spread: {spread*100:.4f}% ({annualized:.1f}% APY). {implication}"
        )

        exchanges = [ex for ex in divergence.keys() if not ex.startswith("_")]

        detection = self._create_detection(
            symbol=symbol,
            severity=severity,
            exchanges=exchanges,
            description=description,
            details={
                "funding_spread": spread,
                "funding_spread_pct": spread * 100,
                "annualized_yield_pct": annualized,
                "long_exchange": long_exchange,
                "short_exchange": short_exchange,
                "long_rate": min_rate,
                "short_rate": max_rate,
                "all_rates": {
                    k: float(v) for k, v in divergence.items()
                    if not k.startswith("_")
                },
                "implication": implication,
            },
            confidence=confidence,
            recommended_action=self._get_recommendation(
                severity, long_exchange, short_exchange, implication
            )
        )

        # Smart deduplication
        if not self._should_alert(detection):
            return None

        self._record_alert(detection)
        return detection

    def _find_arb_pair(self, divergence: Dict[str, Any]) -> tuple[str, str]:
        """Find exchanges with lowest and highest funding rates."""
        rates = {
            k: float(v) for k, v in divergence.items()
            if not k.startswith("_") and v is not None
        }

        if not rates:
            return "unknown", "unknown"

        long_exchange = min(rates.items(), key=lambda x: x[1])[0]  # Lowest rate (go long)
        short_exchange = max(rates.items(), key=lambda x: x[1])[0]  # Highest rate (go short)

        return long_exchange, short_exchange

    def _analyze_implication(
        self,
        max_rate: float,
        min_rate: float,
        spread: float
    ) -> str:
        """Analyze market implication of funding divergence."""
        # Both positive = market bullish
        if max_rate > 0 and min_rate > 0:
            if spread > 0.0005:  # Large spread despite both positive
                return "Market bullish but divergence suggests whale positioning"
            return "Market bullish across exchanges"

        # Both negative = market bearish
        if max_rate < 0 and min_rate < 0:
            if abs(spread) > 0.0005:
                return "Market bearish but divergence suggests whale positioning"
            return "Market bearish across exchanges"

        # Mixed = significant divergence
        if max_rate > 0 and min_rate < 0:
            return "Mixed sentiment - significant arbitrage opportunity, expect convergence"

        return "Neutral market conditions"

    def _get_recommendation(
        self,
        severity: Severity,
        long_ex: str,
        short_ex: str,
        implication: str
    ) -> str:
        """Get action recommendation."""
        if severity == Severity.CRITICAL:
            return (
                f"CRITICAL: Large funding divergence. Arbitrage: Long {long_ex}, Short {short_ex}. "
                "Expect rapid convergence - monitor for price movement. "
                "Check for manipulation on lower-volume exchange."
            )
        elif severity == Severity.ALERT:
            return (
                f"Profitable funding arbitrage available. {implication}. "
                "Consider delta-neutral position if risk/reward favorable."
            )
        else:
            return (
                "Minor funding divergence. Monitor for escalation. "
                "May indicate early directional positioning."
            )
