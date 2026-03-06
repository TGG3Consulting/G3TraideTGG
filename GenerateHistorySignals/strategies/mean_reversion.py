# -*- coding: utf-8 -*-
"""
Mean Reversion Strategy

Trade extreme oversold/overbought conditions with crowd confirmation.

Logic:
- LONG: Big drop (oversold) + crowd bearish = buy the fear
- SHORT: Big rally (overbought) + crowd bullish = sell the greed

IMPORTANT: Entry is at NEXT day's OPEN to avoid look-ahead bias.
- Day X close: We see price change + L/S ratio (available after day X closes)
- If extreme condition met, we generate signal
- Entry = Day X+1 OPEN (realistic - we place order overnight)
"""

from typing import List
from .base import BaseStrategy, StrategyConfig, StrategyData, Signal


class MeanReversionStrategy(BaseStrategy):
    """
    Mean Reversion Strategy - Trade extreme conditions.

    Buy oversold with bearish crowd, sell overbought with bullish crowd.
    """

    name = "mean_reversion"
    description = "Mean reversion on extreme moves with crowd confirmation"

    # !!! НЕ МЕНЯТЬ ПАРАМЕТРЫ - ОТКАЛИБРОВАНЫ ДЛЯ LIVE !!!
    @classmethod
    def default_config(cls) -> StrategyConfig:
        """!!! ПАРАМЕТРЫ ЗАПРЕЩЕНО МЕНЯТЬ - РЕЗУЛЬТАТ БЭКТЕСТА !!!"""
        return StrategyConfig(
            sl_pct=7.0,          # Wider SL - НЕ МЕНЯТЬ!
            tp_pct=15.0,         # Larger TP - НЕ МЕНЯТЬ!
            max_hold_days=14,    # НЕ МЕНЯТЬ!
            lookback=7,          # НЕ МЕНЯТЬ!
            params={
                "oversold_threshold": -10.0,   # -10% = oversold - НЕ МЕНЯТЬ!
                "overbought_threshold": 15.0,  # +15% = overbought - НЕ МЕНЯТЬ!
                "crowd_bearish": 0.55,         # 55% short - НЕ МЕНЯТЬ!
                "crowd_bullish": 0.60,         # 60% long - НЕ МЕНЯТЬ!
            }
        )

    # =========================================================================
    # !!! КРИТИЧЕСКАЯ СЕКЦИЯ - НЕ ИЗМЕНЯТЬ !!!
    # =========================================================================
    # Эта стратегия используется в LIVE торговле через telegram_runner.py
    # Логика генерации сигналов ПОЛНОСТЬЮ ПРОТЕСТИРОВАНА и ОТКАЛИБРОВАНА.
    #
    # КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
    # - Менять пороги (oversold/overbought_threshold, crowd_bearish/bullish)
    # - Менять логику определения направления (LONG/SHORT)
    # - Менять расчёт entry_price
    # - Менять формулы SL/TP
    #
    # Любые изменения = потеря денег на реальном счёте!
    # Последняя проверка: 2025-03-06
    # =========================================================================
    def generate_signals(self, data: StrategyData) -> List[Signal]:
        """
        Generate signals on extreme conditions.

        !!! НЕ ИЗМЕНЯТЬ - ИСПОЛЬЗУЕТСЯ В LIVE ТОРГОВЛЕ !!!

        LONG when: Oversold (big drop) + crowd bearish
        SHORT when: Overbought (big rally) + crowd bullish

        IMPORTANT: Entry is at NEXT day's OPEN to avoid look-ahead bias.
        """
        signals = []
        candles = data.candles
        lookback = self.config.lookback

        # Need at least lookback + 2 candles (current + next day for entry)
        if len(candles) < lookback + 2:
            return signals

        oversold_threshold = self.config.get("oversold_threshold", -10.0)
        overbought_threshold = self.config.get("overbought_threshold", 15.0)
        crowd_bearish = self.config.get("crowd_bearish", 0.55)
        crowd_bullish = self.config.get("crowd_bullish", 0.60)
        sl_pct = self.config.sl_pct
        tp_pct = self.config.tp_pct

        # Stop at len-1 because we need next day's open for entry
        for i in range(lookback, len(candles) - 1):
            candle = candles[i]           # Day X (signal day)
            next_candle = candles[i + 1]  # Day X+1 (entry day)
            prev_candles = candles[i-lookback:i]

            # Get L/S ratio (from previous day - available at start of Day X)
            ls = self._get_ls_for_date(data.ls_history, candle.date)
            if not ls:
                continue

            long_pct = float(ls.get("longAccount", 0.5))
            short_pct = float(ls.get("shortAccount", 0.5))

            # Calculate price change (using Day X close - available after day ends)
            price_7d_ago = prev_candles[0].close
            price_change_pct = (candle.close - price_7d_ago) / price_7d_ago * 100

            signal = None
            entry_price = next_candle.open  # Entry at NEXT day's OPEN (no look-ahead)

            # Volume check - skip if insufficient liquidity (Доработка #2)
            if not self._has_sufficient_volume(next_candle):
                continue

            # LONG: Oversold + crowd bearish
            if price_change_pct <= oversold_threshold and short_pct >= crowd_bearish:
                signal = Signal(
                    date=next_candle.date,  # Signal date = entry date
                    symbol=data.symbol,
                    direction="LONG",
                    entry=entry_price,
                    stop_loss=entry_price * (1 - sl_pct / 100),
                    take_profit=entry_price * (1 + tp_pct / 100),
                    reason=f"MeanRev: {price_change_pct:.1f}%, {short_pct:.0%} short",
                    metadata={
                        "price_change_7d": price_change_pct,
                        "long_pct": long_pct,
                        "short_pct": short_pct,
                        "signal_date": candle.date.isoformat(),
                    }
                )

            # SHORT: Overbought + crowd bullish
            elif price_change_pct >= overbought_threshold and long_pct >= crowd_bullish:
                signal = Signal(
                    date=next_candle.date,  # Signal date = entry date
                    symbol=data.symbol,
                    direction="SHORT",
                    entry=entry_price,
                    stop_loss=entry_price * (1 + sl_pct / 100),
                    take_profit=entry_price * (1 - tp_pct / 100),
                    reason=f"MeanRev: +{price_change_pct:.1f}%, {long_pct:.0%} long",
                    metadata={
                        "price_change_7d": price_change_pct,
                        "long_pct": long_pct,
                        "short_pct": short_pct,
                        "signal_date": candle.date.isoformat(),
                    }
                )

            if signal:
                signals.append(signal)

        return signals
