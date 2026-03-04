# -*- coding: utf-8 -*-
"""
CX-004: OI Migration Detector.

Detects significant migration of Open Interest between exchanges,
which may indicate:
- Whales repositioning for a major move
- Risk-off behavior (moving to safer exchanges)
- Manipulation setup (concentrating positions)
- Regulatory arbitrage

Pattern:
1. OI drops significantly on Binance
2. OI rises proportionally on Bybit
3. Total OI remains similar
4. Often precedes large price movements

Thresholds (configurable):
- WARNING: OI shift > 10% in 1 hour
- ALERT: OI shift > 20% in 1 hour
- CRITICAL: OI shift > 30% in 1 hour
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
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
class OIMigrationConfig(DetectorConfig):
    """Configuration for OI migration detector."""
    enabled: bool = True
    dedup_seconds: int = 30

    # Shift thresholds (as decimal, e.g., 0.10 = 10%)
    warning_threshold: float = 0.10    # 10%
    alert_threshold: float = 0.20      # 20%
    critical_threshold: float = 0.30   # 30%

    # Analysis window
    window_seconds: int = 3600  # 1 hour

    # Minimum OI value to consider (USD)
    min_oi_usd: float = 10_000_000  # $10M


class OIMigrationDetector(BaseCrossDetector):
    """
    Detects migration of Open Interest between exchanges.

    Large OI migrations often precede significant market movements
    as whales reposition their leverage.
    """

    DETECTION_TYPE = DetectionType.OI_MIGRATION
    NAME = "oi_migration"

    def __init__(self, config: OIMigrationConfig = None):
        super().__init__(config or OIMigrationConfig())
        self.config: OIMigrationConfig = self.config
        self._previous_distribution: Dict[str, Dict[str, float]] = {}

    async def analyze(
        self,
        symbol: str,
        state: "StateStore"
    ) -> Optional[Detection]:
        """
        Analyze OI migration patterns.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            state: Cross-exchange state store

        Returns:
            Detection if significant migration detected
        """
        # Get current OI distribution
        distribution = state.get_oi_distribution(symbol)

        if not distribution or "_total_usd" not in distribution:
            return None

        total_oi = float(distribution.get("_total_usd", 0))

        # Check minimum OI
        if total_oi < self.config.min_oi_usd:
            return None

        # Get current percentages
        current_pcts = {
            k: float(v) / 100  # Convert from % to decimal
            for k, v in distribution.items()
            if not k.startswith("_")
        }

        # Compare with previous distribution
        previous_pcts = self._previous_distribution.get(symbol, {})

        # Store current for next comparison
        self._previous_distribution[symbol] = current_pcts.copy()

        if not previous_pcts:
            return None

        # Calculate migration (shift between exchanges)
        migration = self._calculate_migration(current_pcts, previous_pcts)

        if migration["max_shift"] < self.config.warning_threshold:
            return None

        # Determine severity
        severity = self._get_severity(migration["max_shift"])

        # Analyze migration pattern
        pattern = self._analyze_pattern(
            migration["gaining"],
            migration["losing"],
            current_pcts
        )

        # Calculate confidence
        confidence = self._calculate_confidence(
            migration["max_shift"],
            self.config.warning_threshold,
            self.config.critical_threshold * 1.5
        )

        # Get all exchanges
        exchanges = list(current_pcts.keys())

        # Create description
        gaining_str = ", ".join(f"{ex} (+{shift*100:.1f}%)" for ex, shift in migration["gaining"][:2])
        losing_str = ", ".join(f"{ex} ({shift*100:.1f}%)" for ex, shift in migration["losing"][:2])

        description = (
            f"OI migration detected: {gaining_str} gaining from {losing_str}. "
            f"Max shift: {migration['max_shift']*100:.1f}%. {pattern}"
        )

        detection = self._create_detection(
            symbol=symbol,
            severity=severity,
            exchanges=exchanges,
            description=description,
            details={
                "max_shift_pct": migration["max_shift"] * 100,
                "gaining_exchanges": migration["gaining"],
                "losing_exchanges": migration["losing"],
                "current_distribution": current_pcts,
                "previous_distribution": previous_pcts,
                "total_oi_usd": total_oi,
                "pattern": pattern,
            },
            confidence=confidence,
            recommended_action=self._get_recommendation(severity, pattern, migration)
        )

        # Smart deduplication
        if not self._should_alert(detection):
            return None

        self._record_alert(detection)
        return detection

    def _calculate_migration(
        self,
        current: Dict[str, float],
        previous: Dict[str, float]
    ) -> Dict[str, Any]:
        """Calculate OI migration between snapshots."""
        gaining = []
        losing = []
        max_shift = 0.0

        # Only compare exchanges present in both snapshots
        common_exchanges = set(current.keys()) & set(previous.keys())

        for exchange in common_exchanges:
            curr_pct = current[exchange]
            prev_pct = previous[exchange]
            shift = curr_pct - prev_pct

            if abs(shift) > max_shift:
                max_shift = abs(shift)

            if shift > 0.01:  # > 1% gain
                gaining.append((exchange, shift))
            elif shift < -0.01:  # > 1% loss
                losing.append((exchange, shift))

        # Sort by absolute shift
        gaining.sort(key=lambda x: x[1], reverse=True)
        losing.sort(key=lambda x: x[1])

        return {
            "gaining": gaining,
            "losing": losing,
            "max_shift": max_shift,
        }

    def _analyze_pattern(
        self,
        gaining: List[tuple],
        losing: List[tuple],
        current_pcts: Dict[str, float]
    ) -> str:
        """Analyze the pattern of OI migration."""
        if not gaining or not losing:
            return "Unclear migration pattern"

        top_gainer = gaining[0][0] if gaining else None
        top_loser = losing[0][0] if losing else None

        # Check if moving to/from specific exchange types
        major_exchanges = {"binance", "bybit", "okx"}
        smaller_exchanges = {"mexc", "bitmart", "bingx", "gate"}

        gainer_is_major = top_gainer in major_exchanges if top_gainer else False
        loser_is_major = top_loser in major_exchanges if top_loser else False

        if gainer_is_major and not loser_is_major:
            return f"Consolidation to major exchange ({top_gainer}) - possible risk-off"

        if not gainer_is_major and loser_is_major:
            return f"Migration to smaller exchange ({top_gainer}) - possible manipulation setup"

        # Check concentration
        top_concentration = max(current_pcts.values()) if current_pcts else 0
        if top_concentration > 0.6:
            dominant = max(current_pcts.items(), key=lambda x: x[1])[0]
            return f"High concentration on {dominant} ({top_concentration*100:.1f}%) - potential squeeze risk"

        return "Cross-exchange repositioning - whales adjusting exposure"

    def _get_recommendation(
        self,
        severity: Severity,
        pattern: str,
        migration: Dict[str, Any]
    ) -> str:
        """Get action recommendation."""
        gaining = migration.get("gaining", [])
        losing = migration.get("losing", [])

        if severity == Severity.CRITICAL:
            return (
                f"CRITICAL: Large OI migration in progress. {pattern}. "
                "Expect increased volatility. Monitor for potential squeeze or cascade liquidations."
            )
        elif severity == Severity.ALERT:
            gainer = gaining[0][0] if gaining else "unknown"
            return (
                f"Significant OI migration to {gainer}. "
                "Whales repositioning - anticipate directional move."
            )
        else:
            return (
                "Minor OI shifts detected. Monitor for escalation. "
                "Could indicate early whale positioning."
            )
