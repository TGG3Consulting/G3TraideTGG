# -*- coding: utf-8 -*-
"""
CX-005: Liquidity Hunt Detector.

Detects "liquidity hunting" patterns where manipulators trigger liquidations
on one exchange by manipulating price on another:

Pattern (Short Squeeze Hunt):
1. Identify liquidation clusters on Binance (many shorts at $50,000)
2. Execute large buy on low-liquidity exchange (MEXC)
3. Price spikes to $50,100 on MEXC
4. Arbitrage bots push Binance price up
5. Shorts get liquidated on Binance
6. Manipulator profits from liquidation cascade

Pattern (Long Squeeze Hunt):
1. Identify long liquidation levels on Binance
2. Flash crash on smaller exchange
3. Cascade liquidations
4. Quick recovery and profit taking

Signals:
- Rapid price movement on one exchange
- Liquidations spike on major exchange
- Quick price recovery (< 5 minutes)
- Abnormal orderbook before the move
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
class PriceMovement:
    """Record of a significant price movement."""
    exchange: str
    symbol: str
    timestamp: datetime
    price_before: Decimal
    price_after: Decimal
    change_pct: float
    recovered: bool = False


@dataclass
class LiquidityHuntConfig(DetectorConfig):
    """Configuration for liquidity hunt detector."""
    enabled: bool = True
    dedup_seconds: int = 30

    # Price movement thresholds
    price_drop_threshold: float = 0.02    # 2% drop
    price_spike_threshold: float = 0.02   # 2% spike

    # Recovery detection
    recovery_window_sec: int = 300        # 5 minutes
    recovery_threshold: float = 0.5       # 50% recovery

    # Alert thresholds
    warning_threshold: float = 0.02       # 2% move
    alert_threshold: float = 0.03         # 3% move
    critical_threshold: float = 0.05      # 5% move

    # Orderbook imbalance that precedes hunt
    imbalance_threshold: float = 0.7

    # History size
    history_size: int = 100


class LiquidityHuntDetector(BaseCrossDetector):
    """
    Detects liquidity hunting patterns across exchanges.

    Monitors for coordinated price movements designed to trigger
    liquidations on major exchanges.
    """

    DETECTION_TYPE = DetectionType.LIQUIDITY_HUNT
    NAME = "liquidity_hunt"

    def __init__(self, config: LiquidityHuntConfig = None):
        super().__init__(config or LiquidityHuntConfig())
        self.config: LiquidityHuntConfig = self.config

        # Track recent price movements for recovery detection
        self._price_movements: Dict[str, Deque[PriceMovement]] = {}

        # Track baseline prices per symbol/exchange
        self._baselines: Dict[str, Dict[str, Decimal]] = {}

    async def analyze(
        self,
        symbol: str,
        state: "StateStore"
    ) -> Optional[Detection]:
        """
        Analyze for liquidity hunt patterns.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            state: Cross-exchange state store

        Returns:
            Detection if liquidity hunt pattern detected
        """
        # Get current cross-exchange data
        cross_price = state.get_cross_price(symbol)
        orderbook_imbalance = state.get_orderbook_imbalance_cross(symbol)

        if not cross_price.prices or len(cross_price.prices) < 2:
            return None

        # Initialize history if needed
        if symbol not in self._price_movements:
            self._price_movements[symbol] = deque(maxlen=self.config.history_size)
        if symbol not in self._baselines:
            self._baselines[symbol] = {}

        # Check for price anomalies
        anomaly = self._detect_price_anomaly(symbol, cross_price.prices)

        if not anomaly:
            # Update baselines
            self._update_baselines(symbol, cross_price.prices)
            return None

        # Check for recovery (indicates manipulation)
        hunt_pattern = self._check_hunt_pattern(symbol, anomaly, orderbook_imbalance)

        if not hunt_pattern:
            # Record movement for future recovery detection
            self._record_movement(symbol, anomaly)
            return None

        # Determine severity
        severity = self._get_severity(abs(hunt_pattern["price_change"]))

        # Calculate confidence
        confidence = self._calculate_hunt_confidence(hunt_pattern)

        # Get all exchanges
        exchanges = list(cross_price.prices.keys())

        # Create description
        direction = "dump" if hunt_pattern["price_change"] < 0 else "pump"
        description = (
            f"Liquidity hunt detected: {direction} on {hunt_pattern['trigger_exchange']} "
            f"({hunt_pattern['price_change']*100:.2f}%), "
            f"{'recovered' if hunt_pattern['recovered'] else 'ongoing'}. "
            f"Target: {hunt_pattern['target_exchanges']} liquidations."
        )

        detection = self._create_detection(
            symbol=symbol,
            severity=severity,
            exchanges=exchanges,
            description=description,
            details={
                "trigger_exchange": hunt_pattern["trigger_exchange"],
                "target_exchanges": hunt_pattern["target_exchanges"],
                "price_change_pct": hunt_pattern["price_change"] * 100,
                "recovered": hunt_pattern["recovered"],
                "recovery_pct": hunt_pattern.get("recovery_pct", 0) * 100,
                "orderbook_imbalance": hunt_pattern.get("imbalance", {}),
                "pattern_type": "long_squeeze" if hunt_pattern["price_change"] < 0 else "short_squeeze",
                "duration_sec": hunt_pattern.get("duration_sec", 0),
            },
            confidence=confidence,
            recommended_action=self._get_recommendation(severity, hunt_pattern)
        )

        # Smart deduplication
        if not self._should_alert(detection):
            return None

        self._record_alert(detection)
        return detection

    def _detect_price_anomaly(
        self,
        symbol: str,
        prices: Dict[str, Decimal]
    ) -> Optional[Dict[str, Any]]:
        """Detect if any exchange has anomalous price movement."""
        baselines = self._baselines.get(symbol, {})

        if not baselines:
            return None

        anomalies = []

        for exchange, current_price in prices.items():
            baseline = baselines.get(exchange)
            if baseline is None or baseline == 0:
                continue

            change = float((current_price - baseline) / baseline)

            if abs(change) >= self.config.warning_threshold:
                anomalies.append({
                    "exchange": exchange,
                    "baseline": baseline,
                    "current": current_price,
                    "change": change,
                })

        if not anomalies:
            return None

        # Find the most anomalous
        most_anomalous = max(anomalies, key=lambda x: abs(x["change"]))
        return most_anomalous

    def _check_hunt_pattern(
        self,
        symbol: str,
        anomaly: Dict[str, Any],
        orderbook_imbalance: Dict[str, float]
    ) -> Optional[Dict[str, Any]]:
        """Check if anomaly fits liquidity hunt pattern."""
        # Get recent movements
        movements = self._price_movements.get(symbol, deque())

        # Check for recovery from previous movement
        for movement in reversed(movements):
            if movement.recovered:
                continue

            age = (datetime.now(timezone.utc) - movement.timestamp).total_seconds()
            if age > self.config.recovery_window_sec:
                continue

            # Check if current anomaly is recovery
            if self._is_recovery(movement, anomaly):
                movement.recovered = True
                return {
                    "trigger_exchange": movement.exchange,
                    "target_exchanges": self._identify_targets(movement.exchange, orderbook_imbalance),
                    "price_change": movement.change_pct,
                    "recovered": True,
                    "recovery_pct": abs(anomaly["change"] / movement.change_pct) if movement.change_pct else 0,
                    "imbalance": orderbook_imbalance,
                    "duration_sec": age,
                }

        # Check if current anomaly with orderbook imbalance indicates hunt
        imb = orderbook_imbalance.get(anomaly["exchange"], 0.5)

        # Unusual imbalance before move suggests manipulation
        if abs(imb - 0.5) > (self.config.imbalance_threshold - 0.5):
            direction = "buy" if anomaly["change"] > 0 else "sell"
            imb_direction = "buy" if imb > 0.5 else "sell"

            # Imbalance opposite to move direction = potential hunt setup
            if direction != imb_direction:
                return {
                    "trigger_exchange": anomaly["exchange"],
                    "target_exchanges": self._identify_targets(anomaly["exchange"], orderbook_imbalance),
                    "price_change": anomaly["change"],
                    "recovered": False,
                    "imbalance": orderbook_imbalance,
                    "duration_sec": 0,
                }

        return None

    def _is_recovery(self, movement: PriceMovement, current: Dict[str, Any]) -> bool:
        """Check if current price action is recovery from previous movement."""
        # Recovery = opposite direction move
        if (movement.change_pct > 0 and current["change"] < 0) or \
           (movement.change_pct < 0 and current["change"] > 0):
            # Check if recovery is significant
            recovery_ratio = abs(current["change"] / movement.change_pct) if movement.change_pct else 0
            return recovery_ratio >= self.config.recovery_threshold

        return False

    def _identify_targets(
        self,
        trigger: str,
        imbalances: Dict[str, float]
    ) -> List[str]:
        """Identify which exchanges are targets for liquidation cascade."""
        major_exchanges = ["binance", "bybit", "okx"]
        targets = [ex for ex in major_exchanges if ex != trigger]
        return targets

    def _record_movement(self, symbol: str, anomaly: Dict[str, Any]) -> None:
        """Record price movement for future analysis."""
        movement = PriceMovement(
            exchange=anomaly["exchange"],
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            price_before=anomaly["baseline"],
            price_after=anomaly["current"],
            change_pct=anomaly["change"],
        )
        self._price_movements[symbol].append(movement)

    def _update_baselines(self, symbol: str, prices: Dict[str, Decimal]) -> None:
        """Update baseline prices (exponential moving average)."""
        if symbol not in self._baselines:
            self._baselines[symbol] = {}

        alpha = 0.1  # Smoothing factor

        for exchange, price in prices.items():
            if exchange in self._baselines[symbol]:
                old = self._baselines[symbol][exchange]
                self._baselines[symbol][exchange] = old + Decimal(str(alpha)) * (price - old)
            else:
                self._baselines[symbol][exchange] = price

    def _calculate_hunt_confidence(self, pattern: Dict[str, Any]) -> float:
        """Calculate confidence score for hunt pattern."""
        confidence = 0.5

        # Recovery increases confidence
        if pattern.get("recovered"):
            confidence += 0.2

        # Large price change increases confidence
        change = abs(pattern.get("price_change", 0))
        if change > 0.05:
            confidence += 0.2
        elif change > 0.03:
            confidence += 0.1

        # Orderbook imbalance increases confidence
        imb = pattern.get("imbalance", {})
        if imb:
            max_imb = max(abs(v - 0.5) for v in imb.values() if not str(v).startswith("_"))
            if max_imb > 0.3:
                confidence += 0.1

        return min(1.0, confidence)

    def _get_recommendation(
        self,
        severity: Severity,
        pattern: Dict[str, Any]
    ) -> str:
        """Get action recommendation."""
        if severity == Severity.CRITICAL:
            return (
                f"CRITICAL: Active liquidity hunt detected on {pattern['trigger_exchange']}. "
                "Expect cascade liquidations. Reduce exposure or widen stops. "
                "Do NOT add to positions during this volatility."
            )
        elif severity == Severity.ALERT:
            return (
                "Liquidity hunt pattern detected. Monitor for cascade effect. "
                "Consider reducing leverage on affected pairs."
            )
        else:
            return (
                "Potential liquidity hunt signal. Monitor orderbook and funding rates. "
                "Be cautious with new positions."
            )
