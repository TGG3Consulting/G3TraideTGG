# -*- coding: utf-8 -*-
"""
CX-006: Cross-Exchange Spoofing Detector.

Detects spoofing patterns that span multiple exchanges:

Pattern:
1. Place large fake order (wall) on Binance orderbook
2. This creates false impression of support/resistance
3. Execute real trades on Bybit at manipulated prices
4. Cancel the fake wall on Binance
5. Price moves back, spoofer profits

Signals:
- Large orderbook imbalance on one exchange
- Opposite volume activity on another exchange
- Rapid orderbook changes (wall appears/disappears)
- Price movement contrary to visible liquidity

Thresholds (configurable):
- Orderbook imbalance > 80% on one exchange
- Volume spike > 5x on another exchange
- Wall lifetime < 30 seconds
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Deque, Dict, List, Optional, Tuple, TYPE_CHECKING

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
class OrderbookSnapshot:
    """Snapshot of orderbook state."""
    exchange: str
    symbol: str
    timestamp: datetime
    imbalance: float
    bid_wall: Optional[Decimal] = None
    ask_wall: Optional[Decimal] = None


@dataclass
class SpoofingCrossConfig(DetectorConfig):
    """Configuration for cross-exchange spoofing detector."""
    enabled: bool = True
    dedup_seconds: int = 30

    # Orderbook imbalance threshold
    imbalance_threshold: float = 0.80  # 80% on one side

    # Volume spike threshold
    volume_spike_threshold: float = 5.0  # 5x normal

    # Wall lifetime (if disappears quickly = likely spoof)
    wall_lifetime_sec: int = 30

    # Alert thresholds (based on confidence score)
    warning_threshold: float = 0.5
    alert_threshold: float = 0.7
    critical_threshold: float = 0.85

    # History size
    history_size: int = 60


class SpoofingCrossDetector(BaseCrossDetector):
    """
    Detects cross-exchange spoofing patterns.

    Monitors for coordinated orderbook manipulation on one exchange
    while real execution happens on another.
    """

    DETECTION_TYPE = DetectionType.SPOOFING_CROSS
    NAME = "spoofing_cross"

    def __init__(self, config: SpoofingCrossConfig = None):
        super().__init__(config or SpoofingCrossConfig())
        self.config: SpoofingCrossConfig = self.config

        # Track orderbook history
        self._orderbook_history: Dict[str, Dict[str, Deque[OrderbookSnapshot]]] = {}

        # Track detected walls
        self._active_walls: Dict[str, Dict[str, datetime]] = {}

    async def analyze(
        self,
        symbol: str,
        state: "StateStore"
    ) -> Optional[Detection]:
        """
        Analyze for cross-exchange spoofing.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            state: Cross-exchange state store

        Returns:
            Detection if spoofing pattern detected
        """
        # Get current data
        orderbook_imbalance = state.get_orderbook_imbalance_cross(symbol)
        volume_data = state.get_volume_correlation(symbol)

        if not orderbook_imbalance:
            return None

        # Initialize history
        if symbol not in self._orderbook_history:
            self._orderbook_history[symbol] = {}
        if symbol not in self._active_walls:
            self._active_walls[symbol] = {}

        # Check for spoofing pattern
        pattern = self._detect_spoofing_pattern(
            symbol,
            orderbook_imbalance,
            volume_data,
            state
        )

        if not pattern:
            # Update history
            self._update_history(symbol, orderbook_imbalance)
            return None

        # Calculate confidence score
        confidence = self._calculate_spoofing_confidence(pattern)

        # Determine severity based on confidence
        severity = self._get_severity(confidence)

        if severity == Severity.INFO:
            return None

        # Get all exchanges
        exchanges = [ex for ex in orderbook_imbalance.keys() if not ex.startswith("_")]

        # Create description
        description = (
            f"Cross-exchange spoofing detected: Fake wall on {pattern['wall_exchange']} "
            f"(imbalance {pattern['imbalance']*100:.0f}%), "
            f"real volume on {pattern['execution_exchange']}. "
            f"{pattern['pattern_type']}."
        )

        detection = self._create_detection(
            symbol=symbol,
            severity=severity,
            exchanges=exchanges,
            description=description,
            details={
                "wall_exchange": pattern["wall_exchange"],
                "execution_exchange": pattern["execution_exchange"],
                "imbalance": pattern["imbalance"],
                "volume_spike": pattern.get("volume_spike", 1.0),
                "wall_lifetime_sec": pattern.get("wall_lifetime", 0),
                "pattern_type": pattern["pattern_type"],
                "orderbook_imbalances": {
                    k: v for k, v in orderbook_imbalance.items()
                    if not k.startswith("_")
                },
            },
            confidence=confidence,
            recommended_action=self._get_recommendation(severity, pattern)
        )

        # Smart deduplication
        if not self._should_alert(detection):
            return None

        self._record_alert(detection)
        return detection

    def _detect_spoofing_pattern(
        self,
        symbol: str,
        imbalances: Dict[str, float],
        volume_data: Dict[str, Any],
        state: "StateStore"
    ) -> Optional[Dict[str, Any]]:
        """Detect if current state matches spoofing pattern."""
        now = datetime.now(timezone.utc)

        # Find exchanges with extreme imbalance (potential wall)
        wall_candidates = []
        for exchange, imb in imbalances.items():
            if exchange.startswith("_"):
                continue
            if imb > self.config.imbalance_threshold:
                wall_candidates.append((exchange, imb, "ask"))  # Heavy bids = ask wall fake
            elif imb < (1 - self.config.imbalance_threshold):
                wall_candidates.append((exchange, imb, "bid"))  # Heavy asks = bid wall fake

        if not wall_candidates:
            # Check if previous wall just disappeared
            return self._check_wall_disappearance(symbol, imbalances, volume_data)

        # Check for volume spike on different exchange
        volume_shares = volume_data.get("volume_share", {})

        for wall_ex, imb, wall_type in wall_candidates:
            # Record wall
            self._active_walls[symbol][wall_ex] = now

            # Find execution exchange (different from wall exchange)
            for exec_ex, share in volume_shares.items():
                if exec_ex == wall_ex:
                    continue

                # Check if execution exchange has abnormal volume
                # Compare share to what would be expected
                expected_share = 1.0 / len(volume_shares) if volume_shares else 0.5

                if share > expected_share * 2:  # 2x expected = spike
                    return {
                        "wall_exchange": wall_ex,
                        "execution_exchange": exec_ex,
                        "imbalance": imb,
                        "volume_spike": share / expected_share,
                        "pattern_type": f"Fake {wall_type} wall on {wall_ex}, execution on {exec_ex}",
                        "wall_type": wall_type,
                    }

        return None

    def _check_wall_disappearance(
        self,
        symbol: str,
        imbalances: Dict[str, float],
        volume_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Check if a wall just disappeared (spoof confirmation)."""
        now = datetime.now(timezone.utc)
        walls = self._active_walls.get(symbol, {})

        for wall_ex, wall_time in list(walls.items()):
            age = (now - wall_time).total_seconds()

            # Wall disappeared within threshold
            if age < self.config.wall_lifetime_sec:
                current_imb = imbalances.get(wall_ex, 0.5)

                # Imbalance normalized = wall gone
                if 0.3 < current_imb < 0.7:
                    # Wall disappeared quickly = spoof confirmation
                    del walls[wall_ex]

                    return {
                        "wall_exchange": wall_ex,
                        "execution_exchange": "multiple",
                        "imbalance": current_imb,
                        "wall_lifetime": age,
                        "pattern_type": f"Spoof confirmed: Wall on {wall_ex} lasted {age:.0f}s",
                    }

            # Cleanup old walls
            elif age > self.config.wall_lifetime_sec * 3:
                del walls[wall_ex]

        return None

    def _update_history(
        self,
        symbol: str,
        imbalances: Dict[str, float]
    ) -> None:
        """Update orderbook history."""
        now = datetime.now(timezone.utc)

        for exchange, imb in imbalances.items():
            if exchange.startswith("_"):
                continue

            if exchange not in self._orderbook_history[symbol]:
                self._orderbook_history[symbol][exchange] = deque(
                    maxlen=self.config.history_size
                )

            snapshot = OrderbookSnapshot(
                exchange=exchange,
                symbol=symbol,
                timestamp=now,
                imbalance=imb,
            )
            self._orderbook_history[symbol][exchange].append(snapshot)

    def _calculate_spoofing_confidence(self, pattern: Dict[str, Any]) -> float:
        """Calculate confidence score for spoofing pattern."""
        confidence = 0.4  # Base confidence

        # Extreme imbalance increases confidence
        imb = pattern.get("imbalance", 0.5)
        imb_extreme = abs(imb - 0.5) * 2  # 0 to 1 scale
        confidence += imb_extreme * 0.2

        # Volume spike increases confidence
        spike = pattern.get("volume_spike", 1.0)
        if spike > 3:
            confidence += 0.2
        elif spike > 2:
            confidence += 0.1

        # Short wall lifetime increases confidence
        lifetime = pattern.get("wall_lifetime", float('inf'))
        if lifetime < self.config.wall_lifetime_sec:
            confidence += 0.2

        return min(1.0, confidence)

    def _get_recommendation(
        self,
        severity: Severity,
        pattern: Dict[str, Any]
    ) -> str:
        """Get action recommendation."""
        if severity == Severity.CRITICAL:
            return (
                f"CRITICAL: Active spoofing on {pattern['wall_exchange']}. "
                "Do NOT trust visible orderbook depth. Use limit orders only. "
                "Consider reporting to exchange."
            )
        elif severity == Severity.ALERT:
            return (
                f"Likely spoofing on {pattern['wall_exchange']}. "
                "Orderbook walls may be fake. Exercise caution with market orders."
            )
        else:
            return (
                "Potential spoofing activity. Monitor orderbook closely. "
                "Watch for walls that appear and disappear quickly."
            )
