# -*- coding: utf-8 -*-
"""
CX-002: Volume Correlation Detector.

Detects suspiciously synchronized volume across multiple exchanges,
which may indicate:
- Coordinated manipulation (pump groups)
- Wash trading across exchanges
- Bot networks executing synchronized trades
- Front-running with information advantage

Pattern:
1. Same volume pattern appears on 3+ exchanges simultaneously
2. Buy/sell ratio is nearly identical
3. Trade timing is suspiciously synchronized
4. Often precedes large price movements

Thresholds (configurable):
- WARNING: correlation > 0.8 on 3+ exchanges
- ALERT: correlation > 0.9 on 3+ exchanges
- CRITICAL: correlation > 0.95 on 5+ exchanges
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
class VolumeCorrelationConfig(DetectorConfig):
    """Configuration for volume correlation detector."""
    enabled: bool = True
    dedup_seconds: int = 30

    # Correlation thresholds
    warning_threshold: float = 0.80
    alert_threshold: float = 0.90
    critical_threshold: float = 0.95

    # Minimum exchanges for detection
    min_exchanges_warning: int = 3
    min_exchanges_alert: int = 3
    min_exchanges_critical: int = 5

    # Analysis window
    window_seconds: int = 60

    # Minimum trades to consider
    min_trades: int = 10


class VolumeCorrelationDetector(BaseCrossDetector):
    """
    Detects synchronized volume patterns across exchanges.

    Looks for suspiciously similar trading patterns that may indicate
    coordinated manipulation or wash trading networks.
    """

    DETECTION_TYPE = DetectionType.VOLUME_CORRELATION
    NAME = "volume_correlation"

    def __init__(self, config: VolumeCorrelationConfig = None):
        super().__init__(config or VolumeCorrelationConfig())
        self.config: VolumeCorrelationConfig = self.config

    async def analyze(
        self,
        symbol: str,
        state: "StateStore"
    ) -> Optional[Detection]:
        """
        Analyze volume correlation across exchanges.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            state: Cross-exchange state store

        Returns:
            Detection if suspicious correlation found
        """
        # Get volume correlation data
        vol_data = state.get_volume_correlation(symbol)

        if not vol_data:
            return None

        # Get buy/sell ratios
        buy_sell_ratios = vol_data.get("buy_sell_ratio", {})
        volume_shares = vol_data.get("volume_share", {})
        trade_counts = vol_data.get("trade_count", {})

        # Need at least min_exchanges for analysis
        active_exchanges = [
            ex for ex, count in trade_counts.items()
            if count >= self.config.min_trades
        ]

        if len(active_exchanges) < self.config.min_exchanges_warning:
            return None

        # Analyze correlation of buy/sell ratios
        ratio_correlation, ratio_details = self._analyze_ratio_correlation(
            buy_sell_ratios,
            active_exchanges
        )

        # Analyze volume distribution similarity
        volume_similarity = self._analyze_volume_similarity(
            volume_shares,
            active_exchanges
        )

        # Combined score
        correlation_score = (ratio_correlation + volume_similarity) / 2

        # Determine severity based on score and exchange count
        severity = self._determine_severity(correlation_score, len(active_exchanges))

        if severity == Severity.INFO:
            return None

        # Calculate confidence
        confidence = self._calculate_confidence(
            correlation_score,
            self.config.warning_threshold,
            1.0
        )

        # Analyze pattern
        pattern = self._identify_pattern(vol_data, active_exchanges)

        # Create description
        description = (
            f"Synchronized volume detected across {len(active_exchanges)} exchanges. "
            f"Correlation score: {correlation_score:.2%}. {pattern}"
        )

        detection = self._create_detection(
            symbol=symbol,
            severity=severity,
            exchanges=active_exchanges,
            description=description,
            details={
                "correlation_score": correlation_score,
                "ratio_correlation": ratio_correlation,
                "volume_similarity": volume_similarity,
                "buy_sell_ratios": buy_sell_ratios,
                "volume_shares": volume_shares,
                "trade_counts": trade_counts,
                "exchange_count": len(active_exchanges),
                "pattern": pattern,
                "total_volume_usd": vol_data.get("_total_volume_usd", 0),
                "global_buy_ratio": vol_data.get("_global_buy_ratio", 0.5),
            },
            confidence=confidence,
            recommended_action=self._get_recommendation(severity, pattern)
        )

        # Smart deduplication
        if not self._should_alert(detection):
            return None

        self._record_alert(detection)
        return detection

    def _analyze_ratio_correlation(
        self,
        buy_sell_ratios: Dict[str, float],
        exchanges: List[str]
    ) -> tuple[float, Dict[str, Any]]:
        """
        Analyze how similar buy/sell ratios are across exchanges.

        Returns correlation score 0-1 and analysis details.
        """
        ratios = [buy_sell_ratios.get(ex, 0.5) for ex in exchanges if ex in buy_sell_ratios]

        if len(ratios) < 2:
            return 0.0, {}

        # Calculate variance - low variance = high correlation
        mean_ratio = sum(ratios) / len(ratios)
        variance = sum((r - mean_ratio) ** 2 for r in ratios) / len(ratios)

        # Convert variance to correlation score
        # Max variance for ratio (0-1) is 0.25 (when values are 0 and 1)
        # Lower variance = higher correlation
        max_variance = 0.25
        correlation = 1 - (variance / max_variance)
        correlation = max(0, min(1, correlation))

        return correlation, {
            "mean_ratio": mean_ratio,
            "variance": variance,
            "ratios": ratios,
        }

    def _analyze_volume_similarity(
        self,
        volume_shares: Dict[str, float],
        exchanges: List[str]
    ) -> float:
        """
        Analyze how evenly distributed volume is across exchanges.

        Returns similarity score 0-1.
        Perfectly even distribution would be suspicious.
        """
        shares = [volume_shares.get(ex, 0) for ex in exchanges if ex in volume_shares]

        if len(shares) < 2:
            return 0.0

        # Expected share if evenly distributed
        expected_share = 1.0 / len(shares)

        # Calculate how close to even distribution
        deviations = [abs(s - expected_share) for s in shares]
        avg_deviation = sum(deviations) / len(deviations)

        # Lower deviation = more suspicious (too even)
        # But also check if one exchange dominates (less suspicious)
        max_share = max(shares)

        if max_share > 0.7:
            # One exchange dominates - less suspicious
            return 0.3

        # Convert to similarity score
        # Small deviation from even = suspicious
        if avg_deviation < 0.05:
            return 0.95  # Very suspicious - too even
        elif avg_deviation < 0.1:
            return 0.8
        elif avg_deviation < 0.2:
            return 0.5
        else:
            return 0.2

    def _determine_severity(self, score: float, exchange_count: int) -> Severity:
        """Determine severity based on correlation and exchange count."""
        if score >= self.config.critical_threshold and exchange_count >= self.config.min_exchanges_critical:
            return Severity.CRITICAL
        elif score >= self.config.alert_threshold and exchange_count >= self.config.min_exchanges_alert:
            return Severity.ALERT
        elif score >= self.config.warning_threshold and exchange_count >= self.config.min_exchanges_warning:
            return Severity.WARNING
        else:
            return Severity.INFO

    def _identify_pattern(
        self,
        vol_data: Dict[str, Any],
        exchanges: List[str]
    ) -> str:
        """Identify the likely manipulation pattern."""
        buy_ratio = vol_data.get("_global_buy_ratio", 0.5)
        total_trades = vol_data.get("_total_trades", 0)

        if buy_ratio > 0.8:
            return "Coordinated buying pattern - possible pump group"
        elif buy_ratio < 0.2:
            return "Coordinated selling pattern - possible dump group"
        elif total_trades > 1000:
            return "High-frequency synchronized trading - possible bot network"
        else:
            return "Synchronized neutral trading - possible wash trading"

    def _get_recommendation(self, severity: Severity, pattern: str) -> str:
        """Get action recommendation."""
        if severity == Severity.CRITICAL:
            return (
                f"CRITICAL: {pattern}. "
                "Investigate for coordinated manipulation. Consider reporting to exchanges."
            )
        elif severity == Severity.ALERT:
            return (
                "High volume correlation detected. Monitor for manipulation patterns. "
                "Check for common trading entities across exchanges."
            )
        else:
            return "Monitor volume patterns. Could be legitimate market activity or early manipulation."
