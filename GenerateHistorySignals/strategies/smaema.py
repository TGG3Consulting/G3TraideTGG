# -*- coding: utf-8 -*-
"""
SMAEMA Strategy - SMA/EMA Crossover Strategy

Golden Cross (fast crosses slow from below) = LONG
Death Cross (fast crosses slow from above) = SHORT

All parameters are REQUIRED (no defaults):
- fast_type: SMA or EMA
- fast_period: period for fast MA
- slow_type: SMA or EMA
- slow_period: period for slow MA
- offset_pct: entry offset from close (+ above, - below)
- order_lifetime: candles to wait for entry, else skip
- tp_pct: take profit %
- sl_pct: stop loss %
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any

from .base import BaseStrategy, StrategyConfig, StrategyData, Signal, DailyCandle
from .smaema_indicators import SMA, EMA, calculate_ma
from .smaema_crossover import CrossoverDetector, CrossoverType


@dataclass
class SMAEMAConfig:
    """SMAEMA-specific configuration (all required)."""
    fast_type: str        # "SMA" or "EMA"
    fast_period: int
    slow_type: str        # "SMA" or "EMA"
    slow_period: int
    offset_pct: float     # Entry offset: + above close, - below close
    order_lifetime: int   # Candles to wait for entry
    tp_pct: float         # Take profit %
    sl_pct: float         # Stop loss %

    def validate(self) -> None:
        """Validate configuration."""
        if self.fast_type.upper() not in ("SMA", "EMA"):
            raise ValueError(f"fast_type must be SMA or EMA, got: {self.fast_type}")
        if self.slow_type.upper() not in ("SMA", "EMA"):
            raise ValueError(f"slow_type must be SMA or EMA, got: {self.slow_type}")
        if self.fast_period < 1:
            raise ValueError(f"fast_period must be >= 1, got: {self.fast_period}")
        if self.slow_period < 1:
            raise ValueError(f"slow_period must be >= 1, got: {self.slow_period}")
        if self.order_lifetime < 1:
            raise ValueError(f"order_lifetime must be >= 1, got: {self.order_lifetime}")
        if self.tp_pct <= 0:
            raise ValueError(f"tp_pct must be > 0, got: {self.tp_pct}")
        if self.sl_pct <= 0:
            raise ValueError(f"sl_pct must be > 0, got: {self.sl_pct}")


class SMAEMAStrategy(BaseStrategy):
    """SMA/EMA Crossover Strategy.

    Generates signals on MA crossovers:
    - Golden Cross (fast > slow after being below) = LONG
    - Death Cross (fast < slow after being above) = SHORT

    Entry is offset from close by offset_pct.
    If entry not reached within order_lifetime candles, signal is skipped.
    """

    name = "smaema"
    description = "SMA/EMA crossover strategy with offset entry and order lifetime"

    # Required parameters for this strategy
    REQUIRED_PARAMS = [
        "fast_type", "fast_period",
        "slow_type", "slow_period",
        "offset_pct", "order_lifetime",
    ]

    def __init__(self, config: Optional[StrategyConfig] = None):
        """Initialize SMAEMA strategy.

        Args:
            config: Strategy configuration with required params in config.params
        """
        super().__init__(config)
        self._smaema_config: Optional[SMAEMAConfig] = None

        # Extract and validate SMAEMA-specific config
        if config and config.params:
            self._init_smaema_config(config)

    def _init_smaema_config(self, config: StrategyConfig) -> None:
        """Initialize SMAEMA-specific configuration from params."""
        params = config.params

        # Check all required params are present
        missing = [p for p in self.REQUIRED_PARAMS if p not in params]
        if missing:
            raise ValueError(
                f"SMAEMA strategy requires parameters: {', '.join(missing)}"
            )

        self._smaema_config = SMAEMAConfig(
            fast_type=str(params["fast_type"]).upper(),
            fast_period=int(params["fast_period"]),
            slow_type=str(params["slow_type"]).upper(),
            slow_period=int(params["slow_period"]),
            offset_pct=float(params["offset_pct"]),
            order_lifetime=int(params["order_lifetime"]),
            tp_pct=config.tp_pct,
            sl_pct=config.sl_pct,
        )
        self._smaema_config.validate()

    @classmethod
    def default_config(cls) -> StrategyConfig:
        """Return default configuration.

        Note: SMAEMA has NO defaults - all params must be specified.
        This returns a config that will fail validation.
        """
        return StrategyConfig(
            sl_pct=5.0,
            tp_pct=10.0,
            params={}  # Empty - will fail validation
        )

    @classmethod
    def check_params_available(cls, params: Dict[str, Any]) -> bool:
        """Check if all required SMAEMA params are available.

        Args:
            params: Dict with parameter values

        Returns:
            True if all required params present, False otherwise
        """
        return all(p in params for p in cls.REQUIRED_PARAMS)

    @classmethod
    def get_missing_params(cls, params: Dict[str, Any]) -> List[str]:
        """Get list of missing required parameters.

        Args:
            params: Dict with parameter values

        Returns:
            List of missing parameter names
        """
        return [p for p in cls.REQUIRED_PARAMS if p not in params]

    def generate_signals(self, data: StrategyData) -> List[Signal]:
        """Generate trading signals from candle data.

        Algorithm:
        1. Calculate fast_ma and slow_ma for all candles
        2. Detect crossovers
        3. For each crossover:
           - Calculate entry = close * (1 + offset_pct/100)
           - Check if entry reached within order_lifetime candles
           - If reached, create signal with actual entry date
           - If not reached, skip signal
        4. Apply alternation (no consecutive same-direction signals)

        Args:
            data: StrategyData with candles

        Returns:
            List of Signal objects
        """
        if not self._smaema_config:
            raise RuntimeError(
                "SMAEMA strategy not properly configured. "
                "Provide all required parameters: " + ", ".join(self.REQUIRED_PARAMS)
            )

        candles = data.candles
        cfg = self._smaema_config

        # Need enough candles for slow MA
        min_candles = max(cfg.fast_period, cfg.slow_period) + 1
        if len(candles) < min_candles:
            return []

        # Calculate MAs for all candles (using tester_arithmetic=True for batch)
        close_prices = [c.close for c in candles]

        fast_ma_values = self._calculate_ma_series(
            close_prices, cfg.fast_type, cfg.fast_period
        )
        slow_ma_values = self._calculate_ma_series(
            close_prices, cfg.slow_type, cfg.slow_period
        )

        # Find crossovers
        signals: List[Signal] = []

        # DEBUG counters
        debug_crossovers = 0
        debug_skipped_entry = 0

        # Start from where both MAs are available
        start_idx = max(cfg.fast_period, cfg.slow_period)

        for i in range(start_idx, len(candles)):
            # Get MA values for previous and current candle
            fast_prev = fast_ma_values[i - 1]
            fast_curr = fast_ma_values[i]
            slow_prev = slow_ma_values[i - 1]
            slow_curr = slow_ma_values[i]

            # Skip if any MA value is None
            if None in (fast_prev, fast_curr, slow_prev, slow_curr):
                continue

            # Detect crossover
            crossover = CrossoverDetector.detect(
                fast_prev, fast_curr, slow_prev, slow_curr
            )

            if crossover == CrossoverType.NONE:
                continue

            debug_crossovers += 1

            # Determine direction
            direction = "LONG" if crossover == CrossoverType.BULLISH else "SHORT"

            # NO ALTERNATION - per C++ tester behavior
            # C++ tester does not skip same-direction signals

            # Calculate entry price with offset
            # Per C++ tester logic:
            # - LONG: entry = close * (1 - offset/100) — buy BELOW close
            # - SHORT: entry = close * (1 + offset/100) — sell ABOVE close
            candle = candles[i]
            if direction == "LONG":
                entry = candle.close * (1 - cfg.offset_pct / 100)
            else:  # SHORT
                entry = candle.close * (1 + cfg.offset_pct / 100)

            # Check if entry is reached within order_lifetime candles
            entry_result = self._check_entry_reached(
                candles, i, entry, direction, cfg.order_lifetime
            )

            if entry_result is None:
                # Entry not reached, skip this signal
                debug_skipped_entry += 1
                continue

            actual_entry_idx, actual_entry_price = entry_result

            # Calculate TP/SL
            if direction == "LONG":
                take_profit = actual_entry_price * (1 + cfg.tp_pct / 100)
                stop_loss = actual_entry_price * (1 - cfg.sl_pct / 100)
            else:  # SHORT
                take_profit = actual_entry_price * (1 - cfg.tp_pct / 100)
                stop_loss = actual_entry_price * (1 + cfg.sl_pct / 100)

            # Create signal
            signal = Signal(
                date=candles[actual_entry_idx].date,
                symbol=data.symbol,
                direction=direction,
                entry=actual_entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=self._format_reason(
                    crossover, cfg, candle, fast_curr, slow_curr
                ),
                metadata={
                    "crossover_idx": i,
                    "crossover_date": candle.date.isoformat(),
                    "entry_idx": actual_entry_idx,
                    "fast_ma": float(fast_curr),
                    "slow_ma": float(slow_curr),
                    "offset_pct": cfg.offset_pct,
                    "order_lifetime": cfg.order_lifetime,
                },
            )

            signals.append(signal)

        # DEBUG output
        import sys
        print(f"  [DEBUG SMAEMA] Crossovers detected: {debug_crossovers}", file=sys.stderr, flush=True)
        print(f"  [DEBUG SMAEMA] Skipped (entry not reached): {debug_skipped_entry}", file=sys.stderr, flush=True)
        print(f"  [DEBUG SMAEMA] Final signals: {len(signals)}", file=sys.stderr, flush=True)

        return signals

    def _calculate_ma_series(
        self,
        prices: List[float],
        ma_type: str,
        period: int,
    ) -> List[Optional[float]]:
        """Calculate MA values for all price points.

        Uses INCREMENTAL calculation for O(n) complexity instead of O(n²).

        Args:
            prices: List of close prices
            ma_type: "SMA" or "EMA"
            period: MA period

        Returns:
            List of MA values (None for indices where MA not available)
        """
        result: List[Optional[float]] = []

        # Use incremental calculators for O(n) complexity
        if ma_type.upper() == "SMA":
            calculator = SMA(period, tester_arithmetic=True)
        else:
            calculator = EMA(period, tester_arithmetic=True)

        for price in prices:
            ma_value = calculator.update(price)
            if ma_value is not None:
                result.append(float(ma_value))
            else:
                result.append(None)

        return result

    def _check_entry_reached(
        self,
        candles: List[DailyCandle],
        signal_idx: int,
        entry_price: float,
        direction: str,
        order_lifetime: int,
    ) -> Optional[tuple]:
        """Check if entry price is reached within order_lifetime candles.

        Per C++ tester and SMAEMA_STRATEGY_PLAN.md logic:
        - LONG: entry is BELOW close (limit buy) → check low <= entry
        - SHORT: entry is ABOVE close (limit sell) → check high >= entry

        Args:
            candles: All candles
            signal_idx: Index of signal candle (crossover candle)
            entry_price: Target entry price
            direction: "LONG" or "SHORT"
            order_lifetime: Number of candles to check

        Returns:
            (entry_idx, actual_entry_price) if reached, None if not
        """
        # Check candles from signal_idx+1 to signal_idx+order_lifetime
        end_idx = min(signal_idx + order_lifetime + 1, len(candles))

        for i in range(signal_idx + 1, end_idx):
            candle = candles[i]

            if direction == "LONG":
                # LONG: entry is below close, price must fall to entry
                # C++ tester uses STRICT inequality: low < entry
                if candle.low < entry_price:
                    return (i, entry_price)
            else:  # SHORT
                # SHORT: entry is above close, price must rise to entry
                # C++ tester uses STRICT inequality: high > entry
                if candle.high > entry_price:
                    return (i, entry_price)

        return None

    def _format_reason(
        self,
        crossover: CrossoverType,
        cfg: SMAEMAConfig,
        candle: DailyCandle,
        fast_ma: float,
        slow_ma: float,
    ) -> str:
        """Format signal reason string.

        Args:
            crossover: Type of crossover
            cfg: SMAEMA config
            candle: Signal candle
            fast_ma: Fast MA value
            slow_ma: Slow MA value

        Returns:
            Human-readable reason string
        """
        cross_name = "Golden Cross" if crossover == CrossoverType.BULLISH else "Death Cross"
        return (
            f"{cross_name}: {cfg.fast_type}{cfg.fast_period}={fast_ma:.4f} "
            f"{'>' if crossover == CrossoverType.BULLISH else '<'} "
            f"{cfg.slow_type}{cfg.slow_period}={slow_ma:.4f} | "
            f"close={candle.close:.4f}"
        )
