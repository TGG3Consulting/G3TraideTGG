# -*- coding: utf-8 -*-
"""
StrategyRunner - Runs strategy-based signal generation over historical data.

Uses modular strategies from the strategies/ module to generate signals
on daily timeframe data.

This is separate from the legacy SignalRunner which uses minute-by-minute
AccumulationDetector logic.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import asdict
import json

from data_downloader import SymbolHistoryData
from strategies import (
    get_strategy,
    list_strategies,
    BaseStrategy,
    StrategyConfig,
    StrategyData,
    Signal,
    DailyCandle,
)

# Import shared models (Trade, BacktestResult)
from models import Trade, BacktestResult

# =============================================================================
# !!! ИСТОРИЧЕСКИЕ ДАННЫЕ БЭКТЕСТА - НЕ ИЗМЕНЯТЬ !!!
# =============================================================================
# Historical performance data: strategy -> month/day -> (pnl%, maxdd%)
# Based on real backtest data from ANALYSIS_RESULTS.md
# Используется для фильтрации сигналов по месяцу/дню недели.
# !!! НЕ МЕНЯТЬ БЕЗ ПОЛНОГО РЕ-БЭКТЕСТА !!!
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

# =============================================================================
# !!! КРИТИЧЕСКИЕ ДАННЫЕ - НЕ ИЗМЕНЯТЬ !!!
# =============================================================================
# COIN_REGIME_MATRIX и VOL_FILTER_THRESHOLDS - результат бэктеста на 53,538
# трейдах. Эти значения используются в LIVE торговле.
#
# КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО менять без полного ре-бэктеста!
# Последняя калибровка: 2026-03-04
# =============================================================================

# COIN REGIME MATRIX: coin_regime -> strategy -> action
# Actions: 'OFF' = skip, 'DYN' = $1 dynamic, 'FULL' = $100 full size
# Based on 53,538 trades WITHOUT look-ahead bias (2026-03-04, bias fully fixed)
# Rules: WR>=35% & PnL>0 = FULL | WR>=28% & PnL>0 = DYN | else = OFF
# !!! НЕ МЕНЯТЬ БЕЗ ПОЛНОГО РЕ-БЭКТЕСТА !!!
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
        'mean_reversion': 'FULL',# 43.1% WR, +1171% PnL ✓
        'momentum_ls': 'DYN',    # 32.8% WR, +803% PnL
    },
    'SIDEWAYS': {
        'ls_fade': 'DYN',        # 34.8% WR, +2735% PnL
        'momentum': 'DYN',       # 30.5% WR, +1190% PnL
        'reversal': 'OFF',       # 24.4% WR, -169% PnL
        'mean_reversion': 'FULL',# 52.3% WR, +279% PnL ✓✓
        'momentum_ls': 'DYN',    # 34.8% WR, +1659% PnL
    },
    'BEAR': {
        'ls_fade': 'DYN',        # 33.2% WR, +2659% PnL
        'momentum': 'FULL',      # 36.3% WR, +4052% PnL ✓
        'reversal': 'OFF',       # 21.5% WR, -749% PnL
        'mean_reversion': 'OFF', # 25.3% WR, -15% PnL
        'momentum_ls': 'FULL',   # 37.8% WR, +4281% PnL ✓✓
    },
    'STRONG_BEAR': {
        'ls_fade': 'OFF',        # 25.3% WR, -521% PnL
        'momentum': 'OFF',       # 27.8% WR, +103% PnL (WR < 28%)
        'reversal': 'OFF',       # 24.1% WR, -101% PnL
        'mean_reversion': 'DYN', # 29.8% WR, +26% PnL
        'momentum_ls': 'OFF',    # 27.7% WR, +102% PnL (WR < 28%)
    },
}

# Per-strategy volatility filter thresholds (calibrated on MULTI mode, 2020-2024 train, 2025-2026 validation)
# vol_low: skip if coin_vol < threshold (too quiet, no movement)
# vol_high: skip if coin_vol > threshold (too chaotic)
# None = don't apply filter
# !!! НЕ МЕНЯТЬ БЕЗ ПОЛНОГО РЕ-БЭКТЕСТА !!!
VOL_FILTER_THRESHOLDS = {
    'ls_fade':        {'vol_low': 4.5, 'vol_high': 22.0},
    'mean_reversion': {'vol_low': None, 'vol_high': 25.0},  # No vol_low filter - works in low vol
    'momentum':       {'vol_low': 2.0, 'vol_high': 25.0},
    'momentum_ls':    {'vol_low': 4.5, 'vol_high': 25.0},
    'reversal':       {'vol_low': 7.5, 'vol_high': 21.0},
}


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
    for offset in range(1, 4):  # Start from 1 to avoid look-ahead bias
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


# ML Filter (optional)
try:
    from ml.filter import MLSignalFilter
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False
    MLSignalFilter = None


class StrategyRunner:
    """
    Runs strategy-based historical signal generation.

    Uses daily candles and modular strategies from strategies/ module.
    """

    def __init__(
        self,
        strategy_name: str = "ls_fade",
        config: Optional[StrategyConfig] = None,
        output_dir: str = "output",
        use_ml: bool = False,
        ml_model_dir: str = "models",
        ml_min_confidence: float = 0.35,
        ml_min_filter_score: float = 0.45,
        data_interval: str = "daily",
    ):
        """
        Initialize the strategy runner.

        Args:
            strategy_name: Name of strategy to use (e.g., "ls_fade", "momentum")
            config: Optional custom strategy configuration
            output_dir: Directory for output files
            use_ml: Enable ML filtering of signals (default: False)
            ml_model_dir: Directory with trained ML models
            ml_min_confidence: Minimum confidence threshold for ML filter
            ml_min_filter_score: Minimum filter score threshold
            data_interval: Data interval ("daily", "4h", "1h", "15m", "5m", "1m")
        """
        self.strategy = get_strategy(strategy_name, config)
        self.strategy_name = strategy_name
        self.output_dir = output_dir
        self.use_ml = use_ml
        self.data_interval = data_interval
        self.ml_filter: Optional[MLSignalFilter] = None

        # Load ML filter if enabled
        if use_ml:
            if not ML_AVAILABLE:
                raise ImportError("ML module not available. Install dependencies or check ml/ folder.")
            self.ml_filter = MLSignalFilter(
                model_dir=ml_model_dir,
                per_strategy=True,
                min_confidence=ml_min_confidence,
                min_filter_score=ml_min_filter_score,
            )
            self.ml_filter.load()

        # Stats
        self.total_signals = 0
        self.signals_by_symbol: Dict[str, int] = {}
        self.ml_filtered_count = 0
        self.ml_passed_count = 0

    @staticmethod
    def aggregate_to_daily(klines: List[Dict]) -> List[DailyCandle]:
        """Aggregate 1-minute klines to daily candles with ALL available data."""
        daily = {}

        for k in klines:
            ts = k.get("timestamp", 0)
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            date_key = dt.strftime("%Y-%m-%d")

            # quote_volume = price * volume (USDT volume)
            qv = float(k.get("quote_volume", 0)) or float(k["close"]) * float(k["volume"])
            # Additional fields from Binance klines
            trades = int(k.get("trades_count", 0))
            taker_buy_vol = float(k.get("taker_buy_volume", 0))
            taker_buy_quote = float(k.get("taker_buy_quote_volume", 0))

            if date_key not in daily:
                daily[date_key] = {
                    "date": dt.replace(hour=0, minute=0, second=0),
                    "open": float(k["open"]),
                    "high": float(k["high"]),
                    "low": float(k["low"]),
                    "close": float(k["close"]),
                    "volume": float(k["volume"]),
                    "quote_volume": qv,
                    "trades_count": trades,
                    "taker_buy_volume": taker_buy_vol,
                    "taker_buy_quote_volume": taker_buy_quote,
                }
            else:
                daily[date_key]["high"] = max(daily[date_key]["high"], float(k["high"]))
                daily[date_key]["low"] = min(daily[date_key]["low"], float(k["low"]))
                daily[date_key]["close"] = float(k["close"])
                daily[date_key]["volume"] += float(k["volume"])
                daily[date_key]["quote_volume"] += qv
                daily[date_key]["trades_count"] += trades
                daily[date_key]["taker_buy_volume"] += taker_buy_vol
                daily[date_key]["taker_buy_quote_volume"] += taker_buy_quote

        candles = []
        for date_key in sorted(daily.keys()):
            d = daily[date_key]
            candles.append(DailyCandle(
                date=d["date"],
                open=d["open"],
                high=d["high"],
                low=d["low"],
                close=d["close"],
                volume=d["volume"],
                quote_volume=d["quote_volume"],
                trades_count=d["trades_count"],
                taker_buy_volume=d["taker_buy_volume"],
                taker_buy_quote_volume=d["taker_buy_quote_volume"],
            ))

        return candles

    def aggregate_to_interval(self, klines: List[Dict], interval: str = None) -> List[DailyCandle]:
        """
        Aggregate klines to specified interval.

        Args:
            klines: Raw klines data
            interval: Target interval ("daily", "4h", "1h", "15m", "5m", "1m")
                     If None, uses self.data_interval

        Returns:
            List of candles aggregated to interval
        """
        if interval is None:
            interval = self.data_interval

        # For daily, use existing optimized method
        if interval == "daily":
            return self.aggregate_to_daily(klines)

        # For 1m: NO AGGREGATION - direct 1:1 conversion (passthrough)
        # Each kline becomes exactly one candle
        if interval == "1m":
            return self._klines_to_candles_passthrough(klines)

        # Interval in milliseconds (for intervals that need grouping)
        interval_ms = {
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
        }
        ms_per_interval = interval_ms.get(interval, 24 * 60 * 60 * 1000)

        # Group by interval
        candles_dict = {}
        for k in klines:
            ts = k.get("timestamp", 0)
            interval_ts = (ts // ms_per_interval) * ms_per_interval

            qv = float(k.get("quote_volume", 0)) or float(k["close"]) * float(k["volume"])
            trades = int(k.get("trades_count", 0))
            taker_buy_vol = float(k.get("taker_buy_volume", 0))
            taker_buy_quote = float(k.get("taker_buy_quote_volume", 0))

            if interval_ts not in candles_dict:
                candles_dict[interval_ts] = {
                    "timestamp": interval_ts,
                    "open": float(k["open"]),
                    "high": float(k["high"]),
                    "low": float(k["low"]),
                    "close": float(k["close"]),
                    "volume": float(k["volume"]),
                    "quote_volume": qv,
                    "trades_count": trades,
                    "taker_buy_volume": taker_buy_vol,
                    "taker_buy_quote_volume": taker_buy_quote,
                }
            else:
                d = candles_dict[interval_ts]
                d["high"] = max(d["high"], float(k["high"]))
                d["low"] = min(d["low"], float(k["low"]))
                d["close"] = float(k["close"])
                d["volume"] += float(k["volume"])
                d["quote_volume"] += qv
                d["trades_count"] += trades
                d["taker_buy_volume"] += taker_buy_vol
                d["taker_buy_quote_volume"] += taker_buy_quote

        # Convert to DailyCandle list
        result = []
        for ts in sorted(candles_dict.keys()):
            d = candles_dict[ts]
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            result.append(DailyCandle(
                date=dt,
                open=d["open"],
                high=d["high"],
                low=d["low"],
                close=d["close"],
                volume=d["volume"],
                quote_volume=d["quote_volume"],
                trades_count=d["trades_count"],
                taker_buy_volume=d["taker_buy_volume"],
                taker_buy_quote_volume=d["taker_buy_quote_volume"],
            ))

        return result

    def _klines_to_candles_passthrough(self, klines: List[Dict]) -> List[DailyCandle]:
        """
        Convert klines to candles WITHOUT any aggregation (1:1 mapping).

        Used when interval matches the raw kline timeframe (e.g., 1m klines with 1m interval).
        Each kline becomes exactly one candle.
        """
        return self.klines_to_candles_static(klines)

    @staticmethod
    def klines_to_candles_static(klines: List[Dict]) -> List[DailyCandle]:
        """
        Static method: Convert klines to candles WITHOUT any aggregation (1:1 mapping).

        Can be called without instantiating StrategyRunner.
        Each kline becomes exactly one candle.
        """
        result = []
        for k in klines:
            ts = k.get("timestamp", 0)
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)

            qv = float(k.get("quote_volume", 0)) or float(k["close"]) * float(k["volume"])
            trades = int(k.get("trades_count", 0))
            taker_buy_vol = float(k.get("taker_buy_volume", 0))
            taker_buy_quote = float(k.get("taker_buy_quote_volume", 0))

            result.append(DailyCandle(
                date=dt,
                open=float(k["open"]),
                high=float(k["high"]),
                low=float(k["low"]),
                close=float(k["close"]),
                volume=float(k["volume"]),
                quote_volume=qv,
                trades_count=trades,
                taker_buy_volume=taker_buy_vol,
                taker_buy_quote_volume=taker_buy_quote,
            ))

        return result

    @staticmethod
    def aggregate_to_interval_static(klines: List[Dict], interval: str) -> List[DailyCandle]:
        """
        Static method: Aggregate klines to specified interval.

        Can be called without instantiating StrategyRunner.
        For 1m: NO aggregation (1:1 mapping).
        """
        # For 1m: NO aggregation - direct passthrough
        if interval == "1m":
            return StrategyRunner.klines_to_candles_static(klines)

        # For daily: use existing static method
        if interval == "daily":
            return StrategyRunner.aggregate_to_daily(klines)

        # Interval in milliseconds
        interval_ms = {
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
        }
        ms_per_interval = interval_ms.get(interval, 24 * 60 * 60 * 1000)

        # Group by interval
        candles_dict = {}
        for k in klines:
            ts = k.get("timestamp", 0)
            interval_ts = (ts // ms_per_interval) * ms_per_interval

            qv = float(k.get("quote_volume", 0)) or float(k["close"]) * float(k["volume"])
            trades = int(k.get("trades_count", 0))
            taker_buy_vol = float(k.get("taker_buy_volume", 0))
            taker_buy_quote = float(k.get("taker_buy_quote_volume", 0))

            if interval_ts not in candles_dict:
                candles_dict[interval_ts] = {
                    "timestamp": interval_ts,
                    "open": float(k["open"]),
                    "high": float(k["high"]),
                    "low": float(k["low"]),
                    "close": float(k["close"]),
                    "volume": float(k["volume"]),
                    "quote_volume": qv,
                    "trades_count": trades,
                    "taker_buy_volume": taker_buy_vol,
                    "taker_buy_quote_volume": taker_buy_quote,
                }
            else:
                d = candles_dict[interval_ts]
                d["high"] = max(d["high"], float(k["high"]))
                d["low"] = min(d["low"], float(k["low"]))
                d["close"] = float(k["close"])
                d["volume"] += float(k["volume"])
                d["quote_volume"] += qv
                d["trades_count"] += trades
                d["taker_buy_volume"] += taker_buy_vol
                d["taker_buy_quote_volume"] += taker_buy_quote

        # Convert to DailyCandle list
        result = []
        for ts in sorted(candles_dict.keys()):
            d = candles_dict[ts]
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            result.append(DailyCandle(
                date=dt,
                open=d["open"],
                high=d["high"],
                low=d["low"],
                close=d["close"],
                volume=d["volume"],
                quote_volume=d["quote_volume"],
                trades_count=d["trades_count"],
                taker_buy_volume=d["taker_buy_volume"],
                taker_buy_quote_volume=d["taker_buy_quote_volume"],
            ))

        return result

    def _build_ml_features(
        self,
        signal: Signal,
        candle: DailyCandle,
        prev_candle: Optional[DailyCandle],
        ls_data: Optional[Dict],
        oi_data: Optional[Dict],
        funding_data: Optional[Dict],
    ) -> Dict[str, Any]:
        """
        Build feature dict for ML prediction from signal and market data.
        HONEST version - uses PREVIOUS DAY's candle data (no look-ahead bias).

        Args:
            signal: The generated signal
            candle: Daily candle for signal date (only Open is used)
            prev_candle: Previous day's candle (for H/L/C/V features)
            ls_data: Long/Short ratio data
            oi_data: Open Interest data
            funding_data: Funding rate data

        Returns:
            Dict of features for ML filter
        """
        # Calculate SL%, TP%, R:R - these are strategy params, known at signal time
        if signal.direction == "LONG":
            sl_pct = (signal.entry - signal.stop_loss) / signal.entry * 100
            tp_pct = (signal.take_profit - signal.entry) / signal.entry * 100
        else:
            sl_pct = (signal.stop_loss - signal.entry) / signal.entry * 100
            tp_pct = (signal.entry - signal.take_profit) / signal.entry * 100

        rr_ratio = tp_pct / sl_pct if sl_pct > 0 else 0

        features = {
            # Market Data - available from previous day/at signal time
            'Long %': float(ls_data.get('longAccount', 0.5)) * 100 if ls_data else 50.0,
            'Short %': float(ls_data.get('shortAccount', 0.5)) * 100 if ls_data else 50.0,
            'Funding Rate': float(funding_data.get('fundingRate', 0)) * 100 if funding_data else 0.0,
            'OI USD': float(oi_data.get('sumOpenInterestValue', 0)) if oi_data else 0.0,
            'OI Contracts': float(oi_data.get('sumOpenInterest', 0)) if oi_data else 0.0,

            # Only OPEN is known at entry time (entry price)
            'Open': candle.open,

            # PREVIOUS DAY's candle data (HONEST - no look-ahead bias)
            'Prev High': prev_candle.high if prev_candle else 0.0,
            'Prev Low': prev_candle.low if prev_candle else 0.0,
            'Prev Close': prev_candle.close if prev_candle else 0.0,
            'Prev Volume': prev_candle.volume if prev_candle else 0.0,
            'Prev Volume USD': prev_candle.quote_volume if prev_candle else 0.0,
            'Prev Trades Count': prev_candle.trades_count if prev_candle else 0,
            'Prev Taker Buy Vol': prev_candle.taker_buy_volume if prev_candle else 0.0,
            'Prev Taker Buy USD': prev_candle.taker_buy_quote_volume if prev_candle else 0.0,

            # Indicators - ADX calculated from historical candles before signal
            'ADX': signal.metadata.get('adx', 0.0),

            # Trade params - known at signal time
            'SL %': sl_pct,
            'TP %': tp_pct,
            'R:R Ratio': rr_ratio,

            # Chain - only past-looking features
            'Chain Seq': signal.chain_seq,
            'Gap Days': signal.chain_gap_days,
            'Chain First': signal.is_chain_first,
            # REMOVED: Chain Total, Chain Last - requires future knowledge

            # Time - known at signal time
            'DayOfWeek': signal.date.weekday(),
            'Month': signal.date.month,
            'Hour': signal.date.hour,
        }

        return features

    @staticmethod
    def calculate_funding_fee(
        funding_history: List[Dict],
        entry_date: datetime,
        exit_date: datetime,
        direction: str,
    ) -> tuple:
        """
        Calculate funding fee for a position.

        Funding happens at 00:00, 08:00, 16:00 UTC.
        Entry is at day's OPEN (after 00:00 UTC), so first funding we pay is 08:00.
        Exit date is when we close - we pay funding up to that point.

        Args:
            funding_history: List of funding rate records
            entry_date: Position entry date (day of entry, entry at OPEN)
            exit_date: Position exit date
            direction: LONG or SHORT

        Returns:
            Tuple of (funding_fee_pct, funding_periods)
        """
        if not funding_history:
            return 0.0, 0

        # Entry is at OPEN (after 00:00 UTC), so add 1 hour to skip 00:00 funding
        # First funding we pay is 08:00 of entry day
        entry_ts = int((entry_date + timedelta(hours=1)).timestamp() * 1000)

        # Exit - if same day, we might exit before next funding
        # If different day, include funding up to exit day's 00:00
        # For simplicity, include all funding up to end of exit day
        exit_ts = int((exit_date + timedelta(hours=23, minutes=59)).timestamp() * 1000)

        total_funding = 0.0
        periods = 0

        for f in funding_history:
            funding_time = f.get("fundingTime", 0)
            if entry_ts <= funding_time <= exit_ts:
                rate = float(f.get("fundingRate", 0))
                periods += 1

                # LONG pays positive funding, receives negative
                # SHORT receives positive funding, pays negative
                if direction == "LONG":
                    total_funding -= rate  # LONG pays positive, receives negative
                else:
                    total_funding += rate  # SHORT receives positive, pays negative

        # Convert to percentage
        funding_fee_pct = total_funding * 100

        return funding_fee_pct, periods

    # =========================================================================
    # !!! КРИТИЧЕСКАЯ СЕКЦИЯ - НЕ ИЗМЕНЯТЬ !!!
    # =========================================================================
    # Этот метод используется telegram_runner.py для LIVE торговли.
    # ВСЕ стратегии (ls_fade, momentum, mean_reversion, momentum_ls) были
    # протестированы и откалиброваны с этой логикой.
    #
    # ЛЮБЫЕ изменения здесь СЛОМАЮТ live-сигналы!
    #
    # Если нужно добавить функционал для SMAEMA или других новых стратегий -
    # делай это ОТДЕЛЬНО, не трогая существующую логику.
    #
    # Последняя проверка: 2025-03-06 - всё работает корректно.
    # =========================================================================
    def generate_signals(
        self,
        history: Dict[str, SymbolHistoryData],
        symbols: List[str],
        dedup_days: int = 14,
    ) -> List[Signal]:
        """
        Generate signals for all symbols using the configured strategy.

        !!! НЕ ИЗМЕНЯТЬ ЛОГИКУ - ИСПОЛЬЗУЕТСЯ В LIVE ТОРГОВЛЕ !!!

        Args:
            history: Historical data from downloader
            symbols: List of symbols to process
            dedup_days: Threshold days for chain grouping

        Returns:
            List of all generated signals (filtered by ML if use_ml=True)
        """
        all_signals = []

        # Store candles and market data for ML filtering later
        candles_by_symbol: Dict[str, List[DailyCandle]] = {}
        candle_by_date_symbol: Dict[str, Dict[str, DailyCandle]] = {}

        print(f"\n{'='*60}", flush=True)
        print(f"STRATEGY SIGNAL GENERATION", flush=True)
        print(f"Strategy: {self.strategy.name} - {self.strategy.description}", flush=True)
        print(f"Symbols: {len(symbols)}", flush=True)
        if self.use_ml:
            print(f"ML Filter: ENABLED", flush=True)
        print(f"{'='*60}\n", flush=True)

        for i, symbol in enumerate(symbols, 1):
            if symbol not in history:
                continue

            raw = history[symbol]

            # Aggregate to interval candles
            candles = self.aggregate_to_interval(raw.klines)
            import sys
            print(f"  [DEBUG] {symbol}: {len(raw.klines)} klines -> {len(candles)} candles (interval={self.data_interval})", file=sys.stderr, flush=True)
            candles_by_symbol[symbol] = candles
            # Index by timestamp (ms) for universal lookup
            candle_by_date_symbol[symbol] = {int(c.date.timestamp() * 1000): c for c in candles}

            # Build strategy data
            data = StrategyData(
                symbol=symbol,
                candles=candles,
                oi_history=raw.oi_history,
                ls_history=raw.ls_ratio_history,
                funding_history=raw.funding_history,
            )

            # Generate signals
            signals = self.strategy.generate_signals(data)

            # Calculate ADX for each signal
            for signal in signals:
                # Find candle index for signal date
                candle_idx = None
                for idx, candle in enumerate(candles):
                    if candle.date.date() == signal.date.date():
                        candle_idx = idx
                        break

                if candle_idx is not None and candle_idx > 0:
                    # Use candles up to signal date for ADX calculation
                    candles_for_adx = candles[:candle_idx + 1]
                    adx = self.strategy._calculate_adx(candles_for_adx, period=14)
                    signal.metadata['adx'] = round(adx, 2)
                else:
                    signal.metadata['adx'] = 0.0

            all_signals.extend(signals)

            self.signals_by_symbol[symbol] = len(signals)
            self.total_signals += len(signals)

            print(f"  [{i}/{len(symbols)}] {symbol}: {len(signals)} signals", flush=True)

        # Process chains (assign chain_id, chain_seq, etc.)
        from chain_processor import process_chains
        all_signals = process_chains(all_signals, dedup_days=dedup_days)

        # ML FILTERING
        if self.use_ml and self.ml_filter is not None:
            print(f"\n  Applying ML filter...", flush=True)

            filtered_signals = []
            for signal in all_signals:
                symbol = signal.symbol
                signal_ts = int(signal.date.timestamp() * 1000)

                # Get candle for signal date (by timestamp)
                candle = candle_by_date_symbol.get(symbol, {}).get(signal_ts)
                if candle is None:
                    # No candle data - skip signal
                    self.ml_filtered_count += 1
                    continue

                # Get PREVIOUS candle (for ML - no look-ahead bias)
                # For daily: prev day. For other TF: prev candle in list.
                candles_list = candles_by_symbol.get(symbol, [])
                candle_idx = next((i for i, c in enumerate(candles_list) if int(c.date.timestamp() * 1000) == signal_ts), -1)
                prev_candle = candles_list[candle_idx - 1] if candle_idx > 0 else None

                # Get market data
                raw = history.get(symbol)
                ls_data = self.strategy._get_ls_for_date(raw.ls_ratio_history, signal.date) if raw else None
                oi_data = self.strategy._get_oi_for_date(raw.oi_history, signal.date) if raw else None

                # Get funding data
                funding_data = None
                if raw and raw.funding_history:
                    target_ts = int(signal.date.timestamp() * 1000)
                    best_diff = float('inf')
                    for f in raw.funding_history:
                        ts = f.get("fundingTime", 0)
                        diff = abs(ts - target_ts)
                        if diff < best_diff and ts <= target_ts:
                            best_diff = diff
                            funding_data = f

                # Build features (with previous day's candle for HONEST ML)
                features = self._build_ml_features(signal, candle, prev_candle, ls_data, oi_data, funding_data)

                # Predict
                prediction = self.ml_filter.predict(
                    features,
                    strategy=self.strategy_name,
                    symbol=symbol,
                    direction=signal.direction,
                )

                if prediction.should_trade:
                    # Add ML metadata to signal
                    signal.metadata['ml_confidence'] = round(prediction.confidence, 3)
                    signal.metadata['ml_filter_score'] = round(prediction.filter_score, 3)
                    signal.metadata['ml_direction'] = prediction.predicted_direction
                    signal.metadata['ml_sl'] = round(prediction.predicted_sl, 2)
                    signal.metadata['ml_tp'] = round(prediction.predicted_tp, 2)
                    signal.metadata['ml_lifetime'] = round(prediction.predicted_lifetime, 1)
                    filtered_signals.append(signal)
                    self.ml_passed_count += 1
                else:
                    self.ml_filtered_count += 1

            print(f"  ML Filter: {self.ml_passed_count} passed, {self.ml_filtered_count} filtered", flush=True)
            all_signals = filtered_signals

        print(f"\n{'='*60}", flush=True)
        print(f"GENERATION COMPLETE", flush=True)
        print(f"Total signals: {len(all_signals)}", flush=True)
        if self.use_ml:
            print(f"ML Passed: {self.ml_passed_count}, Filtered: {self.ml_filtered_count}", flush=True)
        print(f"{'='*60}\n", flush=True)

        return all_signals

    def backtest_signals(
        self,
        signals: List[Signal],
        history: Dict[str, SymbolHistoryData],
        max_hold_days: int = 14,
        order_size_usd: float = 100.0,
        taker_fee_pct: float = 0.05,
        maker_fee_pct: float = 0.02,
        position_mode: str = "single",
        daily_max_dd: float = 5.0,
        monthly_max_dd: float = 20.0,
        dynamic_size_enabled: bool = False,
        normal_size: float = 100.0,
        protected_size: float = 1.0,
        month_off_dd: Optional[float] = None,
        month_off_pnl: Optional[float] = None,
        day_off_dd: Optional[float] = None,
        day_off_pnl: Optional[float] = None,
        coin_regime_enabled: bool = False,
        coin_regime_lookback: int = 14,
        vol_filter_low_enabled: bool = False,
        vol_filter_high_enabled: bool = False,
    ) -> BacktestResult:
        """
        Backtest signals against price data.

        Args:
            signals: List of signals to backtest
            history: Historical data
            max_hold_days: Maximum days to hold a position
            order_size_usd: Order size in USDT for liquidity check
            taker_fee_pct: Taker fee per side (default 0.05%)
            position_mode: "single" (1 per coin), "direction" (1 per coin per direction), "multi"
            daily_max_dd: Max daily drawdown % before stopping new trades for the day (default 5%)
            monthly_max_dd: Max monthly drawdown % before stopping all trading (default 20%)
            dynamic_size_enabled: Enable dynamic order sizing (default: False)
            normal_size: Order size after WIN (default: 100.0)
            protected_size: Order size after LOSS (default: 1.0)

        Returns:
            BacktestResult with performance metrics
        """
        import math

        trades = []

        # Dynamic sizing state: symbol -> 'NORMAL' or 'PROTECTED'
        symbol_size_state: Dict[str, str] = {}
        # DYN zone tracker: (symbol, strategy) -> (last_result, last_date)
        dyn_zone_tracker: Dict[tuple, tuple] = {}
        skipped_liquidity = 0
        skipped_position = 0
        skipped_daily_limit = 0
        skipped_monthly_limit = 0
        skipped_month_filter = 0
        skipped_day_filter = 0
        skipped_regime = 0
        regime_dynamic_count = 0

        # Risk management tracking
        current_day: Optional[str] = None
        current_month: Optional[str] = None
        current_day_pnl: float = 0.0
        current_month_pnl: float = 0.0
        monthly_stopped: bool = False
        days_stopped: int = 0
        daily_stopped_dates: set = set()

        # Equity and drawdown tracking
        cumulative_equity: float = 0.0
        peak_equity: float = 0.0

        # Position tracking: symbol -> {direction -> exit_date}
        # For single mode: symbol -> exit_date (any direction)
        # For direction mode: symbol -> {LONG: exit_date, SHORT: exit_date}
        open_positions: Dict[str, Any] = {}

        # Pre-aggregate all candles ONCE per symbol (optimization)
        candles_cache: Dict[str, List[DailyCandle]] = {}
        for symbol in history:
            candles_cache[symbol] = self.aggregate_to_interval(history[symbol].klines)

        # Sort signals by date for proper position tracking
        sorted_signals = sorted(signals, key=lambda s: s.date)

        for signal in sorted_signals:
            if signal.symbol not in candles_cache:
                continue

            candles = candles_cache[signal.symbol]

            # Build timestamp index (works for any interval)
            candle_by_ts = {int(c.date.timestamp() * 1000): c for c in candles}
            candle_timestamps = sorted(candle_by_ts.keys())

            signal_ts = int(signal.date.timestamp() * 1000)
            signal_date_str = signal.date.strftime("%Y-%m-%d")  # For daily tracking
            signal_month_str = signal.date.strftime("%Y-%m")
            signal_month = signal.date.month
            signal_day = signal.date.weekday()

            if signal_ts not in candle_by_ts:
                continue

            # Calculate volatility and regime early (for all trades including skipped)
            # Always calculate both for xlsx output, filtering is controlled separately
            coin_vol = calculate_volatility(candles, signal.date, lookback=14)
            coin_regime = calculate_coin_regime(candles, signal.date, lookback=coin_regime_lookback)

            # MONTH FILTER: skip if month exceeds thresholds
            if month_off_dd is not None or month_off_pnl is not None:
                if self.strategy_name in MONTH_DATA and signal_month in MONTH_DATA[self.strategy_name]:
                    m_pnl, m_dd = MONTH_DATA[self.strategy_name][signal_month]
                    skip_month = False
                    if month_off_dd is not None and m_dd < -month_off_dd:
                        skip_month = True
                    if month_off_pnl is not None and m_pnl < month_off_pnl:
                        skip_month = True
                    if skip_month:
                        skipped_month_filter += 1
                        trades.append(Trade(
                            signal=signal,
                            exit_date=signal.date,
                            exit_price=signal.entry,
                            pnl_pct=0.0,
                            result="SKIPPED",
                            hold_days=0,
                            trade_status="skipped_month_filter",
                            order_size=order_size_usd,
                            coin_regime=coin_regime,
                            coin_volatility=coin_vol,
                            atr_pct=coin_vol,
                        ))
                        continue

            # DAY FILTER: skip if day exceeds thresholds
            if day_off_dd is not None or day_off_pnl is not None:
                if self.strategy_name in DAY_DATA and signal_day in DAY_DATA[self.strategy_name]:
                    d_pnl, d_dd = DAY_DATA[self.strategy_name][signal_day]
                    skip_day = False
                    if day_off_dd is not None and d_dd < -day_off_dd:
                        skip_day = True
                    if day_off_pnl is not None and d_pnl < day_off_pnl:
                        skip_day = True
                    if skip_day:
                        skipped_day_filter += 1
                        trades.append(Trade(
                            signal=signal,
                            exit_date=signal.date,
                            exit_price=signal.entry,
                            pnl_pct=0.0,
                            result="SKIPPED",
                            hold_days=0,
                            trade_status="skipped_day_filter",
                            order_size=order_size_usd,
                            coin_regime=coin_regime,
                            coin_volatility=coin_vol,
                            atr_pct=coin_vol,
                        ))
                        continue
            # COIN REGIME FILTER: apply matrix (coin_regime already calculated above)
            regime_size_override = None  # None = use normal sizing, float = override size
            current_zone = 'FULL'  # Track zone for DYN tracker update
            if coin_regime_enabled and coin_regime != 'UNKNOWN' and coin_regime in COIN_REGIME_MATRIX:
                action = COIN_REGIME_MATRIX[coin_regime].get(self.strategy_name, 'FULL')
                if action == 'OFF':
                    skipped_regime += 1
                    trades.append(Trade(
                        signal=signal,
                        exit_date=signal.date,
                        exit_price=signal.entry,
                        pnl_pct=0.0,
                        result="SKIPPED",
                        hold_days=0,
                        trade_status="skipped_regime",
                        order_size=order_size_usd,
                        coin_regime=coin_regime,
                        coin_volatility=coin_vol,
                        atr_pct=coin_vol,
                    ))
                    continue
                elif action == 'DYN':
                    current_zone = 'DYN'
                    regime_dynamic_count += 1
                    # DYN zone: $1 default, $100 after WIN in DYN (reset if >30d gap)
                    dyn_key = (signal.symbol, self.strategy_name)
                    prev_data = dyn_zone_tracker.get(dyn_key)
                    if prev_data:
                        prev_result, prev_date = prev_data
                        days_gap = (signal.date - prev_date).days
                        if days_gap <= 30 and prev_result == 'WIN':
                            regime_size_override = normal_size  # $100 after WIN
                        else:
                            regime_size_override = protected_size  # $1 (gap >30d or LOSS)
                    else:
                        regime_size_override = protected_size  # $1 default
                # else FULL = use normal sizing

            # VOLATILITY FILTER: per-strategy thresholds from VOL_FILTER_THRESHOLDS
            if coin_vol > 0 and self.strategy_name in VOL_FILTER_THRESHOLDS:
                strat_vol_cfg = VOL_FILTER_THRESHOLDS[self.strategy_name]

                # Low volatility filter: skip if below per-strategy threshold
                if vol_filter_low_enabled:
                    strat_vol_low = strat_vol_cfg.get('vol_low')
                    if strat_vol_low is not None and coin_vol < strat_vol_low:
                        skipped_regime += 1
                        trades.append(Trade(
                            signal=signal,
                            exit_date=signal.date,
                            exit_price=signal.entry,
                            pnl_pct=0.0,
                            result="SKIPPED",
                            hold_days=0,
                            trade_status="skipped_vol_low",
                            order_size=order_size_usd,
                            coin_regime=coin_regime,
                            coin_volatility=coin_vol,
                            atr_pct=coin_vol,
                        ))
                        continue

                # High volatility filter: skip if above per-strategy threshold
                if vol_filter_high_enabled:
                    strat_vol_high = strat_vol_cfg.get('vol_high')
                    if strat_vol_high is not None and coin_vol > strat_vol_high:
                        skipped_regime += 1
                        trades.append(Trade(
                            signal=signal,
                            exit_date=signal.date,
                            exit_price=signal.entry,
                            pnl_pct=0.0,
                            result="SKIPPED",
                            hold_days=0,
                            trade_status="skipped_vol_high",
                            order_size=order_size_usd,
                            coin_regime=coin_regime,
                            coin_volatility=coin_vol,
                            atr_pct=coin_vol,
                        ))
                        continue

            # Reset daily counter on new day
            if signal_date_str != current_day:
                current_day = signal_date_str
                current_day_pnl = 0.0

            # Reset monthly counter on new month
            if signal_month_str != current_month:
                current_month = signal_month_str
                current_month_pnl = 0.0

            # MONTHLY LIMIT CHECK: if monthly limit hit, stop all new trading
            if monthly_stopped:
                skipped_monthly_limit += 1
                trades.append(Trade(
                    signal=signal,
                    exit_date=signal.date,
                    exit_price=signal.entry,
                    pnl_pct=0.0,
                    result="SKIPPED",
                    hold_days=0,
                    trade_status="skipped_monthly_limit",
                    order_size=order_size_usd,
                    coin_regime=coin_regime,
                    coin_volatility=coin_vol,
                    atr_pct=coin_vol,
                ))
                continue

            # DAILY LIMIT CHECK: if daily limit hit, skip new trades for this day
            if current_day_pnl <= -daily_max_dd:
                if signal_date_str not in daily_stopped_dates:
                    daily_stopped_dates.add(signal_date_str)
                    days_stopped += 1
                skipped_daily_limit += 1
                trades.append(Trade(
                    signal=signal,
                    exit_date=signal.date,
                    exit_price=signal.entry,
                    pnl_pct=0.0,
                    result="SKIPPED",
                    hold_days=0,
                    trade_status="skipped_daily_limit",
                    order_size=order_size_usd,
                    coin_regime=coin_regime,
                    coin_volatility=coin_vol,
                    atr_pct=coin_vol,
                ))
                continue

            start_idx = candle_timestamps.index(signal_ts)
            entry_candle = candle_by_ts[signal_ts]

            # LIQUIDITY CHECK: order_size must be < 0.1% of daily volume
            daily_volume = entry_candle.quote_volume
            if daily_volume > 0 and order_size_usd > daily_volume * 0.001:
                skipped_liquidity += 1
                trades.append(Trade(
                    signal=signal,
                    exit_date=signal.date,
                    exit_price=signal.entry,
                    pnl_pct=0.0,
                    result="SKIPPED",
                    hold_days=0,
                    trade_status="skipped_liquidity",
                    order_size=order_size_usd,
                    coin_regime=coin_regime,
                    coin_volatility=coin_vol,
                    atr_pct=coin_vol,
                ))
                continue

            # POSITION CHECK based on position_mode
            position_blocked = False
            if position_mode not in ("multi", "none"):
                symbol = signal.symbol
                if symbol in open_positions:
                    if position_mode == "single":
                        # Any position blocks new entry
                        if open_positions[symbol] > signal.date:
                            position_blocked = True
                    elif position_mode == "direction":
                        # Only same direction blocks
                        direction_key = signal.direction
                        if direction_key in open_positions[symbol]:
                            if open_positions[symbol][direction_key] > signal.date:
                                position_blocked = True

            if position_blocked:
                skipped_position += 1
                trades.append(Trade(
                    signal=signal,
                    exit_date=signal.date,
                    exit_price=signal.entry,
                    pnl_pct=0.0,
                    result="SKIPPED",
                    hold_days=0,
                    trade_status="skipped_position",
                    order_size=order_size_usd,
                    coin_regime=coin_regime,
                    coin_volatility=coin_vol,
                    atr_pct=coin_vol,
                ))
                continue

            # DYNAMIC SIZE: determine order size based on previous result
            if dynamic_size_enabled:
                symbol_state = symbol_size_state.get(signal.symbol, 'NORMAL')
                current_order_size = normal_size if symbol_state == 'NORMAL' else protected_size
            else:
                current_order_size = order_size_usd

            # REGIME SIZE OVERRIDE: if coin regime says DYN, use protected size
            if regime_size_override is not None:
                current_order_size = regime_size_override

            # SLIPPAGE: Impact = σ × √(order_size / daily_volume)
            slippage_pct = 0.0
            if daily_volume > 0:
                sigma = 2.0
                slippage_pct = sigma * math.sqrt(order_size_usd / daily_volume)

            # Look forward for exit
            result = "TIMEOUT"
            exit_price = signal.entry
            exit_date = signal.date

            for j in range(1, min(max_hold_days + 1, len(candle_timestamps) - start_idx)):
                future_ts = candle_timestamps[start_idx + j]
                future_candle = candle_by_ts[future_ts]

                # Per C++ tester: use STRICT inequalities (< and >)
                if signal.direction == "LONG":
                    if future_candle.low < signal.stop_loss:
                        result = "LOSS"
                        exit_price = signal.stop_loss
                        exit_date = future_candle.date
                        break
                    if future_candle.high > signal.take_profit:
                        result = "WIN"
                        exit_price = signal.take_profit
                        exit_date = future_candle.date
                        break
                else:  # SHORT
                    if future_candle.high > signal.stop_loss:
                        result = "LOSS"
                        exit_price = signal.stop_loss
                        exit_date = future_candle.date
                        break
                    if future_candle.low < signal.take_profit:
                        result = "WIN"
                        exit_price = signal.take_profit
                        exit_date = future_candle.date
                        break

                exit_price = future_candle.close
                exit_date = future_candle.date

            # Update position tracking
            if position_mode == "single":
                open_positions[signal.symbol] = exit_date
            elif position_mode == "direction":
                if signal.symbol not in open_positions:
                    open_positions[signal.symbol] = {}
                open_positions[signal.symbol][signal.direction] = exit_date

            # Calculate GROSS PnL (before fees)
            if signal.direction == "LONG":
                gross_pnl_pct = (exit_price - signal.entry) / signal.entry * 100
            else:
                gross_pnl_pct = (signal.entry - exit_price) / signal.entry * 100

            # TRADING FEES per C++ tester:
            # - Entry: maker_fee
            # - Exit WIN (TP): maker_fee
            # - Exit LOSS/TIMEOUT: taker_fee
            entry_fee_pct = maker_fee_pct
            exit_fee_pct = maker_fee_pct if result == "WIN" else taker_fee_pct
            total_fee_pct = entry_fee_pct + exit_fee_pct

            # FUNDING FEE: calculate from funding_history
            funding_fee_pct = 0.0
            funding_periods = 0
            if signal.symbol in history:
                funding_history = history[signal.symbol].funding_history
                funding_fee_pct, funding_periods = self.calculate_funding_fee(
                    funding_history, signal.date, exit_date, signal.direction
                )

            # NET PnL = gross - trading_fees - funding_fee - slippage
            net_pnl_pct = gross_pnl_pct - total_fee_pct + funding_fee_pct - slippage_pct

            hold_days = (exit_date - signal.date).days

            # Update equity tracking
            cumulative_equity += net_pnl_pct
            if cumulative_equity > peak_equity:
                peak_equity = cumulative_equity
            current_dd = peak_equity - cumulative_equity

            trades.append(Trade(
                signal=signal,
                exit_date=exit_date,
                exit_price=exit_price,
                pnl_pct=gross_pnl_pct,
                result=result,
                hold_days=hold_days,
                fee_pct=total_fee_pct,
                funding_fee_pct=funding_fee_pct,
                net_pnl_pct=net_pnl_pct,
                slippage_pct=slippage_pct,
                trade_status="traded",
                funding_periods=funding_periods,
                current_dd=current_dd,
                order_size=current_order_size,
                coin_regime=coin_regime,
                coin_volatility=coin_vol,
                atr_pct=coin_vol,
            ))

            # Update dynamic size state after trade result
            if dynamic_size_enabled:
                if result == 'LOSS':
                    symbol_size_state[signal.symbol] = 'PROTECTED'
                elif result == 'WIN':
                    symbol_size_state[signal.symbol] = 'NORMAL'
                # TIMEOUT keeps current state

            # Update DYN zone tracker (result + date for gap detection)
            if current_zone == 'DYN' and result in ('WIN', 'LOSS'):
                dyn_zone_tracker[(signal.symbol, self.strategy_name)] = (result, signal.date)

            # Update risk management counters (by entry date)
            current_day_pnl += net_pnl_pct
            current_month_pnl += net_pnl_pct

            # Check if monthly limit is now hit (for next signals)
            if current_month_pnl <= -monthly_max_dd:
                monthly_stopped = True

        # Calculate results - only count traded signals (not skipped)
        traded = [t for t in trades if t.trade_status == "traded"]

        if not traded:
            return BacktestResult(
                total_signals=len(signals),
                total_trades=0,
                wins=0,
                losses=0,
                timeouts=0,
                win_rate=0.0,
                total_pnl=0.0,
                avg_pnl=0.0,
                long_pnl=0.0,
                short_pnl=0.0,
                trades=trades,  # Include all trades (including skipped)
                skipped_liquidity=skipped_liquidity,
                skipped_position=skipped_position,
                skipped_daily_limit=skipped_daily_limit,
                skipped_monthly_limit=skipped_monthly_limit,
                skipped_month_filter=skipped_month_filter,
                skipped_day_filter=skipped_day_filter,
                skipped_regime=skipped_regime,
                regime_dynamic_count=regime_dynamic_count,
                order_size_usd=order_size_usd,
                taker_fee_pct=taker_fee_pct,
                position_mode=position_mode,
                daily_max_dd=daily_max_dd,
                monthly_max_dd=monthly_max_dd,
                days_stopped=days_stopped,
                monthly_stopped=monthly_stopped,
            )

        wins = sum(1 for t in traded if t.result == "WIN")
        losses = sum(1 for t in traded if t.result == "LOSS")
        timeouts = sum(1 for t in traded if t.result == "TIMEOUT")

        # Use NET PnL for all calculations (only traded)
        # When dynamic_size_enabled, weight PnL by order_size relative to normal_size
        if dynamic_size_enabled:
            # Weighted PnL: scale by order_size / normal_size
            total_pnl = sum(t.net_pnl_pct * t.order_size / normal_size for t in traded)
            long_trades = [t for t in traded if t.signal.direction == "LONG"]
            short_trades = [t for t in traded if t.signal.direction == "SHORT"]
            long_pnl = sum(t.net_pnl_pct * t.order_size / normal_size for t in long_trades) if long_trades else 0
            short_pnl = sum(t.net_pnl_pct * t.order_size / normal_size for t in short_trades) if short_trades else 0
        else:
            total_pnl = sum(t.net_pnl_pct for t in traded)
            long_trades = [t for t in traded if t.signal.direction == "LONG"]
            short_trades = [t for t in traded if t.signal.direction == "SHORT"]
            long_pnl = sum(t.net_pnl_pct for t in long_trades) if long_trades else 0
            short_pnl = sum(t.net_pnl_pct for t in short_trades) if short_trades else 0

        avg_pnl = total_pnl / len(traded)
        win_rate = wins / len(traded) * 100

        # Total fees (only traded)
        total_fees = sum(t.fee_pct for t in traded)

        # MAX DRAWDOWN calculation (only traded)
        # When dynamic_size_enabled, weight by order_size
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in sorted(traded, key=lambda x: x.signal.date):
            if dynamic_size_enabled:
                equity += t.net_pnl_pct * t.order_size / normal_size
            else:
                equity += t.net_pnl_pct
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        # Calmar Ratio = Total PnL / Max Drawdown
        calmar = total_pnl / max_dd if max_dd > 0 else 0.0

        # Hold time stats (only traded)
        win_trades = [t for t in traded if t.result == "WIN"]
        loss_trades = [t for t in traded if t.result == "LOSS"]
        timeout_trades = [t for t in traded if t.result == "TIMEOUT"]

        avg_hold_win = sum(t.hold_days for t in win_trades) / len(win_trades) if win_trades else 0.0
        avg_hold_loss = sum(t.hold_days for t in loss_trades) / len(loss_trades) if loss_trades else 0.0
        avg_hold_timeout = sum(t.hold_days for t in timeout_trades) / len(timeout_trades) if timeout_trades else 0.0

        return BacktestResult(
            total_signals=len(signals),
            total_trades=len(traded),
            wins=wins,
            losses=losses,
            timeouts=timeouts,
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_pnl=avg_pnl,
            long_pnl=long_pnl,
            short_pnl=short_pnl,
            trades=trades,  # Include all trades (including skipped)
            skipped_liquidity=skipped_liquidity,
            skipped_position=skipped_position,
            skipped_daily_limit=skipped_daily_limit,
            skipped_monthly_limit=skipped_monthly_limit,
            skipped_month_filter=skipped_month_filter,
            skipped_day_filter=skipped_day_filter,
            skipped_regime=skipped_regime,
            regime_dynamic_count=regime_dynamic_count,
            order_size_usd=order_size_usd,
            taker_fee_pct=taker_fee_pct,
            total_fees_pct=total_fees,
            max_drawdown=max_dd,
            calmar_ratio=calmar,
            avg_hold_win=avg_hold_win,
            avg_hold_loss=avg_hold_loss,
            avg_hold_timeout=avg_hold_timeout,
            position_mode=position_mode,
            daily_max_dd=daily_max_dd,
            monthly_max_dd=monthly_max_dd,
            days_stopped=days_stopped,
            monthly_stopped=monthly_stopped,
        )

    def write_signals_json(
        self,
        signals: List[Signal],
        filename: Optional[str] = None,
    ) -> str:
        """
        Write signals to JSON file.

        Args:
            signals: List of signals to write
            filename: Optional custom filename

        Returns:
            Path to written file
        """
        import os

        os.makedirs(self.output_dir, exist_ok=True)

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"signals_{self.strategy.name}_{timestamp}.json"

        filepath = os.path.join(self.output_dir, filename)

        # Convert signals to dicts
        data = {
            "strategy": {
                "name": self.strategy.name,
                "description": self.strategy.description,
                "config": {
                    "sl_pct": self.strategy.config.sl_pct,
                    "tp_pct": self.strategy.config.tp_pct,
                    "max_hold_days": self.strategy.config.max_hold_days,
                    "params": self.strategy.config.params,
                }
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_signals": len(signals),
            "signals": [
                {
                    "date": s.date.isoformat(),
                    "symbol": s.symbol,
                    "direction": s.direction,
                    "entry": s.entry,
                    "stop_loss": s.stop_loss,
                    "take_profit": s.take_profit,
                    "reason": s.reason,
                    "metadata": s.metadata,
                }
                for s in signals
            ]
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"Signals written to: {filepath}", flush=True)
        return filepath

    def print_backtest_summary(self, result: BacktestResult) -> None:
        """Print backtest results summary."""
        print(f"\n{'='*60}", flush=True)
        print(f"BACKTEST RESULTS: {self.strategy.name}", flush=True)
        print(f"{'='*60}", flush=True)
        print(f"  Signals:        {result.total_signals}", flush=True)
        print(f"  Trades:         {result.total_trades}", flush=True)
        if result.skipped_liquidity > 0:
            print(f"  Skipped (liq):  {result.skipped_liquidity}", flush=True)
        if result.skipped_position > 0:
            print(f"  Skipped (pos):  {result.skipped_position} ({result.position_mode} mode)", flush=True)
        print(f"  Win Rate:       {result.win_rate:.1f}%", flush=True)
        print(f"  W/L/T:          {result.wins}/{result.losses}/{result.timeouts}", flush=True)
        print(f"  Total PnL:      {result.total_pnl:+.1f}% (net)", flush=True)
        print(f"  Avg PnL:        {result.avg_pnl:+.2f}%", flush=True)
        print(f"  LONG PnL:       {result.long_pnl:+.1f}%", flush=True)
        print(f"  SHORT PnL:      {result.short_pnl:+.1f}%", flush=True)
        print(f"  Total Fees:     {result.total_fees_pct:.2f}%", flush=True)
        print(f"  Max Drawdown:   {result.max_drawdown:.1f}%", flush=True)
        if result.max_drawdown > 0:
            print(f"  Calmar Ratio:   {result.calmar_ratio:.2f}", flush=True)
        print(f"  Avg Hold (W/L/T): {result.avg_hold_win:.1f}/{result.avg_hold_loss:.1f}/{result.avg_hold_timeout:.1f} days", flush=True)
        print(f"{'='*60}\n", flush=True)

    def export_to_xlsx(
        self,
        result: BacktestResult,
        history: Dict[str, SymbolHistoryData],
        order_size_usd: float = 100.0,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        filename: Optional[str] = None,
        market_regime: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Export backtest results to XLSX.

        Args:
            result: BacktestResult with trades
            history: Historical data by symbol
            order_size_usd: Order size in USD
            start_date: Backtest start date
            end_date: Backtest end date
            filename: Optional custom filename
            market_regime: Market regime detection result

        Returns:
            Path to saved XLSX file
        """
        import os
        from xlsx_exporter import XLSXExporter

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"backtest_{self.strategy.name}_{timestamp}.xlsx"

        os.makedirs(self.output_dir, exist_ok=True)
        filepath = os.path.join(self.output_dir, filename)

        exporter = XLSXExporter(filepath, data_interval=self.data_interval)
        return exporter.export_backtest(
            trades=result.trades,
            history=history,
            config=self.strategy.config,
            result=result,
            order_size_usd=order_size_usd,
            strategy_name=self.strategy.name,
            start_date=start_date,
            end_date=end_date,
            market_regime=market_regime,
            data_interval=self.data_interval,
        )


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    from hybrid_downloader import HybridHistoryDownloader

    print("StrategyRunner - Test Run")
    print("=" * 60)

    # Show available strategies
    print("\nAvailable strategies:")
    for name, desc in list_strategies():
        print(f"  {name}: {desc}")

    # Test with LS Fade strategy
    print("\nTesting LS Fade strategy...")

    # Download test data
    downloader = HybridHistoryDownloader(
        cache_dir="cache",
        coinalyze_api_key="adb282f9-7e9e-4b6c-a669-b01c0304d506"
    )

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    start = datetime(2024, 12, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 1, tzinfo=timezone.utc)

    print(f"Downloading {len(symbols)} symbols...")
    history = downloader.download_with_coinalyze_backfill(symbols, start, end)

    # Create runner with LS Fade strategy
    runner = StrategyRunner(
        strategy_name="ls_fade",
        output_dir="output",
    )

    # Generate signals
    signals = runner.generate_signals(history, symbols)

    # Backtest
    result = runner.backtest_signals(signals, history)
    runner.print_backtest_summary(result)

    # Write to JSON
    if signals:
        runner.write_signals_json(signals)

    print("\nTest complete.")
