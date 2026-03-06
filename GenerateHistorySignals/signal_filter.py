# -*- coding: utf-8 -*-
"""
Signal Filter - Логика фильтрации сигналов для Telegram прода.

Содержит матрицы и функции, вынесенные из strategy_runner.py
для использования в telegram_runner.py без изменения оригинального кода.

# =============================================================================
# !!! КРИТИЧЕСКИЙ МОДУЛЬ - НЕ ИЗМЕНЯТЬ !!!
# =============================================================================
# Этот модуль используется в LIVE торговле через telegram_runner.py
# Все матрицы (MONTH_DATA, DAY_DATA, COIN_REGIME_MATRIX, VOL_FILTER_THRESHOLDS)
# откалиброваны на реальных бэктестах.
#
# КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО менять:
# - Значения в матрицах
# - Логику функции filter_signal()
# - Пороги фильтрации
#
# Любые изменения = потеря денег на реальном счёте!
# Последняя проверка: 2025-03-06
# =============================================================================
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from strategies import Signal, DailyCandle


# =============================================================================
# МАТРИЦЫ (скопированы из strategy_runner.py БЕЗ изменений)
# !!! НЕ МЕНЯТЬ БЕЗ ПОЛНОГО РЕ-БЭКТЕСТА !!!
# =============================================================================

# Historical performance data: strategy -> month/day -> (pnl%, maxdd%)
# Based on real backtest data from ANALYSIS_RESULTS.md
MONTH_DATA = {
    'ls_fade': {1: (105, -27), 2: (-29, -68), 3: (49, -29), 4: (71, -30), 5: (13, -26), 6: (106, -13), 7: (40, -37), 8: (13, -33), 9: (-20, -48), 10: (34, -30), 11: (-10, -45), 12: (42, -13)},
    'momentum': {1: (43, -30), 2: (28, -35), 3: (-15, -27), 4: (8, -28), 5: (-56, -62), 6: (63, -16), 7: (62, -14), 8: (-43, -68), 9: (-25, -31), 10: (-24, -38), 11: (72, -17), 12: (16, -17)},
    'reversal': {1: (5, -8), 2: (-8, -9), 3: (19, -4), 4: (14, -8), 5: (16, -2), 6: (-11, -12), 7: (-1, -11), 8: (15, -5), 9: (0, -5), 10: (-15, -19), 11: (-1, -12), 12: (-9, -13)},
    'mean_reversion': {1: (13, -2), 2: (2, -3), 3: (11, -3), 4: (8, -5), 5: (24, -1), 6: (3, 0), 7: (-3, -13), 8: (17, -1), 9: (3, -4), 10: (4, 0), 11: (-3, -15), 12: (10, -6)},
    'momentum_ls': {1: (69, -18), 2: (10, -25), 3: (6, -19), 4: (23, -14), 5: (-34, -40), 6: (75, -13), 7: (34, -14), 8: (-21, -46), 9: (-15, -21), 10: (-6, -22), 11: (26, -14), 12: (22, -13)},
}

DAY_DATA = {  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    'ls_fade': {0: (63, -15), 1: (77, -16), 2: (43, -18), 3: (64, -17), 4: (41, -26), 5: (59, -19), 6: (69, -21)},
    'momentum': {0: (-9, -35), 1: (7, -27), 2: (-27, -47), 3: (36, -22), 4: (59, -16), 5: (50, -21), 6: (13, -32)},
    'reversal': {0: (0, -10), 1: (4, -12), 2: (9, -7), 3: (13, -8), 4: (8, -16), 5: (-2, -15), 6: (-4, -7)},
    'mean_reversion': {0: (16, -3), 1: (21, -4), 2: (18, -4), 3: (10, -4), 4: (-2, -7), 5: (8, -5), 6: (18, -4)},
    'momentum_ls': {0: (14, -14), 1: (30, -17), 2: (-12, -34), 3: (38, -13), 4: (32, -15), 5: (47, -13), 6: (39, -24)},
}

# COIN REGIME MATRIX: coin_regime -> strategy -> action
# Actions: 'OFF' = skip, 'DYN' = $1 dynamic, 'FULL' = $100 full size
# Based on 53,538 trades WITHOUT look-ahead bias (2026-03-04, bias fully fixed)
COIN_REGIME_MATRIX = {
    'STRONG_BULL': {
        'ls_fade': 'DYN',        # 29.5% WR, +569% PnL
        'momentum': 'DYN',       # 34.3% WR, +2088% PnL
        'reversal': 'OFF',       # 27.4% WR, -6% PnL
        'mean_reversion': 'DYN', # 31.0% WR, +644% PnL
        'momentum_ls': 'DYN',    # 30.2% WR, +375% PnL
    },
    'BULL': {
        'ls_fade': 'DYN',        # 34.2% WR, +2253% PnL
        'momentum': 'DYN',       # 31.2% WR, +1384% PnL
        'reversal': 'OFF',       # 25.9% WR, -104% PnL
        'mean_reversion': 'FULL',# 43.1% WR, +1171% PnL
        'momentum_ls': 'DYN',    # 32.8% WR, +803% PnL
    },
    'SIDEWAYS': {
        'ls_fade': 'DYN',        # 34.8% WR, +2735% PnL
        'momentum': 'DYN',       # 30.5% WR, +1190% PnL
        'reversal': 'OFF',       # 24.4% WR, -169% PnL
        'mean_reversion': 'FULL',# 52.3% WR, +279% PnL
        'momentum_ls': 'DYN',    # 34.8% WR, +1659% PnL
    },
    'BEAR': {
        'ls_fade': 'DYN',        # 33.2% WR, +2659% PnL
        'momentum': 'FULL',      # 36.3% WR, +4052% PnL
        'reversal': 'OFF',       # 21.5% WR, -749% PnL
        'mean_reversion': 'OFF', # 25.3% WR, -15% PnL
        'momentum_ls': 'FULL',   # 37.8% WR, +4281% PnL
    },
    'STRONG_BEAR': {
        'ls_fade': 'OFF',        # 25.3% WR, -521% PnL
        'momentum': 'OFF',       # 27.8% WR, +103% PnL
        'reversal': 'OFF',       # 24.1% WR, -101% PnL
        'mean_reversion': 'DYN', # 29.8% WR, +26% PnL
        'momentum_ls': 'OFF',    # 27.7% WR, +102% PnL
    },
}

# Per-strategy volatility filter thresholds
VOL_FILTER_THRESHOLDS = {
    'ls_fade':        {'vol_low': 4.5, 'vol_high': 22.0},
    'mean_reversion': {'vol_low': None, 'vol_high': 25.0},
    'momentum':       {'vol_low': 2.0, 'vol_high': 25.0},
    'momentum_ls':    {'vol_low': 4.5, 'vol_high': 25.0},
    'reversal':       {'vol_low': 7.5, 'vol_high': 21.0},
}


# =============================================================================
# ФУНКЦИИ (скопированы из strategy_runner.py БЕЗ изменений)
# =============================================================================

def calculate_coin_regime(candles: List[DailyCandle], target_date: datetime, lookback: int = 14) -> str:
    """
    Calculate coin regime based on price change over lookback period.

    Args:
        candles: List of daily candles (sorted by date ascending)
        target_date: Date to calculate regime for
        lookback: Number of days to look back (default 14)

    Returns:
        Regime string: 'STRONG_BULL', 'BULL', 'SIDEWAYS', 'BEAR', 'STRONG_BEAR', or 'UNKNOWN'
    """
    if not candles or len(candles) < lookback:
        return 'UNKNOWN'

    # Build date -> candle index map
    date_to_idx = {}
    for i, c in enumerate(candles):
        date_str = c.date.strftime('%Y-%m-%d')
        date_to_idx[date_str] = i

    target_str = target_date.strftime('%Y-%m-%d')

    # Find PREVIOUS day candle (no look-ahead bias - close not known at entry)
    current_idx = None
    for offset in range(1, 4):
        check_date = (target_date - timedelta(days=offset)).strftime('%Y-%m-%d')
        if check_date in date_to_idx:
            current_idx = date_to_idx[check_date]
            break

    if current_idx is None:
        return 'UNKNOWN'

    # Find lookback candle
    past_date = target_date - timedelta(days=lookback)
    past_idx = None
    for offset in range(1, 4):
        check_date = (past_date - timedelta(days=offset)).strftime('%Y-%m-%d')
        if check_date in date_to_idx:
            past_idx = date_to_idx[check_date]
            break

    if past_idx is None:
        return 'UNKNOWN'

    # Calculate price change
    current_close = candles[current_idx].close
    past_close = candles[past_idx].close

    if past_close == 0:
        return 'UNKNOWN'

    change_pct = (current_close - past_close) / past_close * 100

    # Determine regime
    if change_pct > 20:
        return 'STRONG_BULL'
    elif change_pct > 5:
        return 'BULL'
    elif change_pct > -5:
        return 'SIDEWAYS'
    elif change_pct > -20:
        return 'BEAR'
    else:
        return 'STRONG_BEAR'


def calculate_volatility(candles: List[DailyCandle], target_date: datetime, lookback: int = 14) -> float:
    """
    Calculate coin volatility as ATR% over lookback period.

    Uses PREVIOUS day's data to avoid look-ahead bias.

    Args:
        candles: List of daily candles (sorted by date ascending)
        target_date: Date to calculate volatility for
        lookback: Number of days for ATR calculation (default 14)

    Returns:
        ATR as percentage of price (0.0 if insufficient data)
    """
    if not candles or len(candles) < lookback + 1:
        return 0.0

    # Build date -> candle index map
    date_to_idx = {}
    for i, c in enumerate(candles):
        date_str = c.date.strftime('%Y-%m-%d')
        date_to_idx[date_str] = i

    # Find PREVIOUS day candle (no look-ahead bias)
    end_idx = None
    for offset in range(1, 4):
        check_date = (target_date - timedelta(days=offset)).strftime('%Y-%m-%d')
        if check_date in date_to_idx:
            end_idx = date_to_idx[check_date]
            break

    if end_idx is None or end_idx < lookback:
        return 0.0

    # Calculate True Range for last 'lookback' candles
    true_ranges = []
    for i in range(end_idx - lookback + 1, end_idx + 1):
        if i < 1:
            continue
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    # ATR = average of True Ranges
    atr = sum(true_ranges) / len(true_ranges)

    # Current close for percentage calculation
    current_close = candles[end_idx].close
    if current_close == 0:
        return 0.0

    # Return ATR as percentage
    return (atr / current_close) * 100


def calculate_regime_change_pct(candles: List[DailyCandle], target_date: datetime, lookback: int = 14) -> float:
    """
    Calculate price change percentage for regime (for display in alerts).
    """
    if not candles or len(candles) < lookback:
        return 0.0

    date_to_idx = {}
    for i, c in enumerate(candles):
        date_str = c.date.strftime('%Y-%m-%d')
        date_to_idx[date_str] = i

    current_idx = None
    for offset in range(1, 4):
        check_date = (target_date - timedelta(days=offset)).strftime('%Y-%m-%d')
        if check_date in date_to_idx:
            current_idx = date_to_idx[check_date]
            break

    if current_idx is None:
        return 0.0

    past_date = target_date - timedelta(days=lookback)
    past_idx = None
    for offset in range(1, 4):
        check_date = (past_date - timedelta(days=offset)).strftime('%Y-%m-%d')
        if check_date in date_to_idx:
            past_idx = date_to_idx[check_date]
            break

    if past_idx is None:
        return 0.0

    current_close = candles[current_idx].close
    past_close = candles[past_idx].close

    if past_close == 0:
        return 0.0

    return (current_close - past_close) / past_close * 100


# =============================================================================
# РЕЗУЛЬТАТ ФИЛЬТРАЦИИ
# =============================================================================

@dataclass
class FilterResult:
    """Результат применения фильтров к сигналу."""
    passed: bool
    skip_reason: Optional[str]  # "skipped_regime", "skipped_vol_low", "skipped_vol_high", None
    coin_regime: str            # "STRONG_BULL", "BEAR", etc.
    coin_volatility: float      # ATR%
    regime_action: str          # "OFF", "DYN", "FULL"
    regime_change_pct: float    # Price change % for display


# =========================================================================
# !!! КРИТИЧЕСКАЯ ФУНКЦИЯ - НЕ ИЗМЕНЯТЬ !!!
# =========================================================================
# Эта функция используется в LIVE торговле через telegram_runner.py
# Логика полностью протестирована на 53,538 трейдах.
#
# КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО менять логику фильтрации!
# Последняя проверка: 2025-03-06
# =========================================================================
def filter_signal(
    signal: Signal,
    candles: List[DailyCandle],
    strategy_name: str,
    coin_regime_enabled: bool = False,
    vol_filter_low_enabled: bool = False,
    vol_filter_high_enabled: bool = False,
    coin_regime_lookback: int = 14,
) -> FilterResult:
    """
    Применяет все фильтры к сигналу.

    !!! НЕ ИЗМЕНЯТЬ ЛОГИКУ - ИСПОЛЬЗУЕТСЯ В LIVE ТОРГОВЛЕ !!!

    Логика идентична strategy_runner.py:807-884.

    Args:
        signal: Сигнал для проверки
        candles: Свечи для расчёта режима и волатильности
        strategy_name: Название стратегии
        coin_regime_enabled: Включить фильтр по режиму монеты
        vol_filter_low_enabled: Фильтр низкой волатильности
        vol_filter_high_enabled: Фильтр высокой волатильности
        coin_regime_lookback: Период для расчёта режима

    Returns:
        FilterResult с результатом фильтрации
    """
    # Рассчитываем всегда (для отображения в алерте)
    coin_regime = calculate_coin_regime(candles, signal.date, lookback=coin_regime_lookback)
    coin_vol = calculate_volatility(candles, signal.date, lookback=14)
    regime_change_pct = calculate_regime_change_pct(candles, signal.date, lookback=coin_regime_lookback)

    # Определяем действие матрицы
    regime_action = 'FULL'
    if coin_regime != 'UNKNOWN' and coin_regime in COIN_REGIME_MATRIX:
        regime_action = COIN_REGIME_MATRIX[coin_regime].get(strategy_name, 'FULL')

    # COIN REGIME FILTER
    if coin_regime_enabled and coin_regime != 'UNKNOWN' and coin_regime in COIN_REGIME_MATRIX:
        if regime_action == 'OFF':
            return FilterResult(
                passed=False,
                skip_reason="skipped_regime",
                coin_regime=coin_regime,
                coin_volatility=coin_vol,
                regime_action=regime_action,
                regime_change_pct=regime_change_pct,
            )

    # VOLATILITY FILTER
    if coin_vol > 0 and strategy_name in VOL_FILTER_THRESHOLDS:
        strat_vol_cfg = VOL_FILTER_THRESHOLDS[strategy_name]

        # Low volatility filter
        if vol_filter_low_enabled:
            strat_vol_low = strat_vol_cfg.get('vol_low')
            if strat_vol_low is not None and coin_vol < strat_vol_low:
                return FilterResult(
                    passed=False,
                    skip_reason="skipped_vol_low",
                    coin_regime=coin_regime,
                    coin_volatility=coin_vol,
                    regime_action=regime_action,
                    regime_change_pct=regime_change_pct,
                )

        # High volatility filter
        if vol_filter_high_enabled:
            strat_vol_high = strat_vol_cfg.get('vol_high')
            if strat_vol_high is not None and coin_vol > strat_vol_high:
                return FilterResult(
                    passed=False,
                    skip_reason="skipped_vol_high",
                    coin_regime=coin_regime,
                    coin_volatility=coin_vol,
                    regime_action=regime_action,
                    regime_change_pct=regime_change_pct,
                )

    # Все фильтры пройдены
    return FilterResult(
        passed=True,
        skip_reason=None,
        coin_regime=coin_regime,
        coin_volatility=coin_vol,
        regime_action=regime_action,
        regime_change_pct=regime_change_pct,
    )
