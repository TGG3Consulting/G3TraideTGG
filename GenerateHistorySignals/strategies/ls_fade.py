# -*- coding: utf-8 -*-
"""
LS Fade Strategy

Trade against crowd extremes - when crowd positioning becomes extreme (>65% one direction),
fade their position expecting a reversal.

Performance (6 months, 18 altcoins, Aug 2024 - Jan 2025):
- Signals: 2456 (0.75 signals/day/coin)
- Win Rate: 30.5%
- Total PnL: +814.5% (with SL=4%, TP=10%)
- SHORT signals are highly profitable (+822.5%), LONGs near breakeven (-8%)

Logic:
- SHORT: When crowd is >65% LONG (fade bullish euphoria)
- LONG: When crowd is >65% SHORT (fade bearish panic)
"""

from typing import List
from .base import BaseStrategy, StrategyConfig, StrategyData, Signal, DailyCandle


class LSFadeStrategy(BaseStrategy):
    """
    LS Fade Strategy - Trade against crowd extremes.

    When the crowd is overwhelmingly positioned in one direction,
    take the opposite position expecting a reversal.
    """

    name = "ls_fade"
    description = "Fade crowd extremes (>65% one direction)"

    # !!! НЕ МЕНЯТЬ ПАРАМЕТРЫ - ОТКАЛИБРОВАНЫ ДЛЯ LIVE !!!
    @classmethod
    def default_config(cls) -> StrategyConfig:
        """Default configuration optimized from backtesting.

        !!! ПАРАМЕТРЫ ЗАПРЕЩЕНО МЕНЯТЬ - РЕЗУЛЬТАТ БЭКТЕСТА !!!
        """
        return StrategyConfig(
            sl_pct=4.0,          # 4% stop loss (optimized) - НЕ МЕНЯТЬ!
            tp_pct=10.0,         # 10% take profit - НЕ МЕНЯТЬ!
            max_hold_days=14,    # НЕ МЕНЯТЬ!
            lookback=7,          # НЕ МЕНЯТЬ!
            params={
                "ls_extreme": 0.65,  # 65% threshold - НЕ МЕНЯТЬ!
            }
        )

    # =========================================================================
    # !!! КРИТИЧЕСКАЯ СЕКЦИЯ - НЕ ИЗМЕНЯТЬ !!!
    # =========================================================================
    # Эта стратегия используется в LIVE торговле через telegram_runner.py
    # Логика генерации сигналов ПОЛНОСТЬЮ ПРОТЕСТИРОВАНА и ОТКАЛИБРОВАНА.
    #
    # КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:
    # - Менять пороги (ls_extreme, sl_pct, tp_pct)
    # - Менять логику определения направления (LONG/SHORT)
    # - Менять расчёт entry_price
    # - Менять формулы SL/TP
    #
    # Любые изменения = потеря денег на реальном счёте!
    # Последняя проверка: 2025-03-06
    # =========================================================================
    def generate_signals(self, data: StrategyData) -> List[Signal]:
        """
        Generate signals when crowd positioning is extreme.

        !!! НЕ ИЗМЕНЯТЬ - ИСПОЛЬЗУЕТСЯ В LIVE ТОРГОВЛЕ !!!

        SHORT when: long_pct >= ls_extreme (crowd too bullish)
        LONG when: short_pct >= ls_extreme (crowd too bearish)

        IMPORTANT: Entry is at NEXT day's OPEN to avoid look-ahead bias.
        - Day X: We see L/S ratio from day X-1 (available at start of day X)
        - If extreme, we generate signal
        - Entry = Day X+1 OPEN (realistic - we can place order overnight)
        """
        signals = []
        candles = data.candles
        lookback = self.config.lookback

        # Need at least lookback + 2 candles (current + next day for entry)
        if len(candles) < lookback + 2:
            return signals

        ls_extreme = self.config.get("ls_extreme", 0.65)
        sl_pct = self.config.sl_pct
        tp_pct = self.config.tp_pct

        # Stop at len-1 because we need next day's open for entry
        for i in range(lookback, len(candles) - 1):
            candle = candles[i]          # Day X (signal day)
            next_candle = candles[i + 1]  # Day X+1 (entry day)

            # Get L/S ratio for this date (uses data from day X-1 or earlier)
            ls = self._get_ls_for_date(data.ls_history, candle.date)
            if not ls:
                continue

            long_pct = float(ls.get("longAccount", 0.5))
            short_pct = float(ls.get("shortAccount", 0.5))

            signal = None
            entry_price = next_candle.open  # Entry at NEXT day's OPEN (no look-ahead)

            # Volume check - skip if insufficient liquidity (Доработка #2)
            if not self._has_sufficient_volume(next_candle):
                continue

            # LONG: Crowd extremely short (fade their bearishness)
            if short_pct >= ls_extreme:
                signal = Signal(
                    date=next_candle.date,  # Signal date = entry date
                    symbol=data.symbol,
                    direction="LONG",
                    entry=entry_price,
                    stop_loss=entry_price * (1 - sl_pct / 100),
                    take_profit=entry_price * (1 + tp_pct / 100),
                    reason=f"LS Fade: {short_pct:.0%} short",
                    metadata={
                        "long_pct": long_pct,
                        "short_pct": short_pct,
                        "ls_extreme": ls_extreme,
                        "signal_date": candle.date.isoformat(),  # When signal was generated
                    }
                )

            # SHORT: Crowd extremely long (fade their bullishness)
            elif long_pct >= ls_extreme:
                signal = Signal(
                    date=next_candle.date,  # Signal date = entry date
                    symbol=data.symbol,
                    direction="SHORT",
                    entry=entry_price,
                    stop_loss=entry_price * (1 + sl_pct / 100),
                    take_profit=entry_price * (1 - tp_pct / 100),
                    reason=f"LS Fade: {long_pct:.0%} long",
                    metadata={
                        "long_pct": long_pct,
                        "short_pct": short_pct,
                        "ls_extreme": ls_extreme,
                        "signal_date": candle.date.isoformat(),  # When signal was generated
                    }
                )

            if signal:
                signals.append(signal)

        return signals
