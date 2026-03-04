# -*- coding: utf-8 -*-
"""
Momentum Strategy

Trade with the trend - enter positions in the direction of strong price movement.

Logic:
- LONG: When 7-day price change >= threshold% (strong uptrend)
- SHORT: When 7-day price change <= -threshold% (strong downtrend)

IMPORTANT: Entry is at NEXT day's OPEN to avoid look-ahead bias.
- Day X close: We see 7-day price change (available after day X closes)
- If momentum threshold met, we generate signal
- Entry = Day X+1 OPEN (realistic - we place order overnight)
"""

from typing import List
from .base import BaseStrategy, StrategyConfig, StrategyData, Signal


class MomentumStrategy(BaseStrategy):
    """
    Pure Momentum Strategy - Trade with the trend.

    Enter positions when price shows strong directional movement.
    """

    name = "momentum"
    description = "Trade with strong price momentum"

    @classmethod
    def default_config(cls) -> StrategyConfig:
        return StrategyConfig(
            sl_pct=5.0,
            tp_pct=10.0,
            max_hold_days=14,
            lookback=7,
            params={
                "momentum_threshold": 5.0,  # 5% price change threshold
            }
        )

    def generate_signals(self, data: StrategyData) -> List[Signal]:
        """
        Generate signals based on price momentum.

        LONG when: 7-day price change >= threshold
        SHORT when: 7-day price change <= -threshold

        IMPORTANT: Entry is at NEXT day's OPEN to avoid look-ahead bias.
        """
        signals = []
        candles = data.candles
        lookback = self.config.lookback

        # Need at least lookback + 2 candles (current + next day for entry)
        if len(candles) < lookback + 2:
            return signals

        momentum_threshold = self.config.get("momentum_threshold", 5.0)
        sl_pct = self.config.sl_pct
        tp_pct = self.config.tp_pct

        # Stop at len-1 because we need next day's open for entry
        for i in range(lookback, len(candles) - 1):
            candle = candles[i]           # Day X (signal day)
            next_candle = candles[i + 1]  # Day X+1 (entry day)
            prev_candles = candles[i-lookback:i]

            # Calculate price momentum (using Day X close - available after day ends)
            price_7d_ago = prev_candles[0].close
            price_change_pct = (candle.close - price_7d_ago) / price_7d_ago * 100

            signal = None
            entry_price = next_candle.open  # Entry at NEXT day's OPEN (no look-ahead)

            # Volume check - skip if insufficient liquidity (Доработка #2)
            if not self._has_sufficient_volume(next_candle):
                continue

            # LONG: Strong uptrend
            if price_change_pct >= momentum_threshold:
                signal = Signal(
                    date=next_candle.date,  # Signal date = entry date
                    symbol=data.symbol,
                    direction="LONG",
                    entry=entry_price,
                    stop_loss=entry_price * (1 - sl_pct / 100),
                    take_profit=entry_price * (1 + tp_pct / 100),
                    reason=f"Momentum UP {price_change_pct:.1f}%",
                    metadata={
                        "price_change_7d": price_change_pct,
                        "threshold": momentum_threshold,
                        "signal_date": candle.date.isoformat(),
                    }
                )

            # SHORT: Strong downtrend
            elif price_change_pct <= -momentum_threshold:
                signal = Signal(
                    date=next_candle.date,  # Signal date = entry date
                    symbol=data.symbol,
                    direction="SHORT",
                    entry=entry_price,
                    stop_loss=entry_price * (1 + sl_pct / 100),
                    take_profit=entry_price * (1 - tp_pct / 100),
                    reason=f"Momentum DOWN {price_change_pct:.1f}%",
                    metadata={
                        "price_change_7d": price_change_pct,
                        "threshold": momentum_threshold,
                        "signal_date": candle.date.isoformat(),
                    }
                )

            if signal:
                signals.append(signal)

        return signals
