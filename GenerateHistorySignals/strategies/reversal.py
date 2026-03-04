# -*- coding: utf-8 -*-
"""
Reversal Strategy

Trade reversals after extreme moves with early confirmation.

Logic:
- LONG: After big 7d drop + 3d bounce starting (reversal up)
- SHORT: After big 7d rally + 3d pullback starting (reversal down)

IMPORTANT: Entry is at NEXT day's OPEN to avoid look-ahead bias.
- Day X close: We see 7d + 3d price changes (available after day X closes)
- If reversal pattern detected, we generate signal
- Entry = Day X+1 OPEN (realistic - we place order overnight)
"""

from typing import List
from .base import BaseStrategy, StrategyConfig, StrategyData, Signal


class ReversalStrategy(BaseStrategy):
    """
    Reversal Strategy - Trade reversals after extreme moves.

    Enter when price shows signs of reversing after a big move.
    """

    name = "reversal"
    description = "Trade reversals after extreme price moves"

    @classmethod
    def default_config(cls) -> StrategyConfig:
        return StrategyConfig(
            sl_pct=5.0,
            tp_pct=10.0,
            max_hold_days=14,
            lookback=7,
            params={
                "momentum_threshold": 5.0,  # 5% move to qualify as "extreme"
            }
        )

    def generate_signals(self, data: StrategyData) -> List[Signal]:
        """
        Generate signals on reversals.

        LONG when: 7d big drop + 3d bounce starting
        SHORT when: 7d big rally + 3d pullback starting

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

            # 7-day momentum (using Day X close - available after day ends)
            price_7d_ago = prev_candles[0].close
            price_change_pct = (candle.close - price_7d_ago) / price_7d_ago * 100

            # 3-day momentum (for reversal confirmation)
            price_change_3d = 0
            if len(prev_candles) >= 3:
                price_3d_ago = prev_candles[-3].close
                price_change_3d = (candle.close - price_3d_ago) / price_3d_ago * 100

            signal = None
            entry_price = next_candle.open  # Entry at NEXT day's OPEN (no look-ahead)

            # Volume check - skip if insufficient liquidity (Доработка #2)
            if not self._has_sufficient_volume(next_candle):
                continue

            # LONG: After big drop + 3d bounce starting
            if price_change_pct <= -momentum_threshold and price_change_3d > 0:
                signal = Signal(
                    date=next_candle.date,  # Signal date = entry date
                    symbol=data.symbol,
                    direction="LONG",
                    entry=entry_price,
                    stop_loss=entry_price * (1 - sl_pct / 100),
                    take_profit=entry_price * (1 + tp_pct / 100),
                    reason=f"Reversal: 7d {price_change_pct:.1f}%, 3d +{price_change_3d:.1f}%",
                    metadata={
                        "price_change_7d": price_change_pct,
                        "price_change_3d": price_change_3d,
                        "signal_date": candle.date.isoformat(),
                    }
                )

            # SHORT: After big rally + 3d pullback starting
            elif price_change_pct >= momentum_threshold and price_change_3d < 0:
                signal = Signal(
                    date=next_candle.date,  # Signal date = entry date
                    symbol=data.symbol,
                    direction="SHORT",
                    entry=entry_price,
                    stop_loss=entry_price * (1 + sl_pct / 100),
                    take_profit=entry_price * (1 - tp_pct / 100),
                    reason=f"Reversal: 7d +{price_change_pct:.1f}%, 3d {price_change_3d:.1f}%",
                    metadata={
                        "price_change_7d": price_change_pct,
                        "price_change_3d": price_change_3d,
                        "signal_date": candle.date.isoformat(),
                    }
                )

            if signal:
                signals.append(signal)

        return signals
