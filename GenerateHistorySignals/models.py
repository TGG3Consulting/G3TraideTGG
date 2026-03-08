# -*- coding: utf-8 -*-
"""
Models - Shared data classes for backtesting.

These are separated to avoid circular imports between
strategy_runner.py and xlsx_exporter.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from strategies import Signal


@dataclass
class Trade:
    """Completed trade from backtesting."""
    signal: Signal
    exit_date: datetime
    exit_price: float
    pnl_pct: float           # Gross PnL (before fees)
    result: str              # WIN, LOSS, TIMEOUT, SKIPPED
    hold_days: int = 0
    fee_pct: float = 0.0     # Total fees (entry + exit)
    funding_fee_pct: float = 0.0  # Funding fee (for position hold)
    net_pnl_pct: float = 0.0 # Net PnL (after all fees)
    slippage_pct: float = 0.0
    trade_status: str = "traded"  # traded, skipped_position, skipped_liquidity, skipped_daily_limit, skipped_monthly_limit, skipped_month_filter, skipped_day_filter, skipped_regime
    funding_periods: int = 0  # Number of 8-hour funding periods
    current_dd: float = 0.0  # Current drawdown at time of trade
    order_size: float = 100.0  # Order size in USD (for dynamic sizing)
    # Regime and volatility data (for analysis and filtering)
    coin_regime: str = ""       # STRONG_BULL/BULL/SIDEWAYS/BEAR/STRONG_BEAR
    coin_volatility: float = 0.0  # ATR% at signal time (14-day)
    atr_pct: float = 0.0        # Alias for coin_volatility


@dataclass
class BacktestResult:
    """Results from backtesting signals."""
    total_signals: int
    total_trades: int
    wins: int
    losses: int
    timeouts: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    long_pnl: float
    short_pnl: float
    trades: List[Trade] = field(default_factory=list)
    trailing_stops: int = 0  # Trailing stop exits (counted as WIN)
    # Enhanced backtesting fields
    skipped_liquidity: int = 0
    skipped_position: int = 0
    skipped_daily_limit: int = 0
    skipped_monthly_limit: int = 0
    skipped_month_filter: int = 0
    skipped_day_filter: int = 0
    skipped_regime: int = 0
    regime_dynamic_count: int = 0
    order_size_usd: float = 0.0
    taker_fee_pct: float = 0.05
    total_fees_pct: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    avg_hold_win: float = 0.0
    avg_hold_loss: float = 0.0
    avg_hold_timeout: float = 0.0
    position_mode: str = "single"
    # Risk management limits
    daily_max_dd: float = 5.0
    monthly_max_dd: float = 20.0
    days_stopped: int = 0  # Number of days trading was stopped due to daily limit
    monthly_stopped: bool = False  # Whether trading was stopped due to monthly limit
    # Trailing stop settings
    trailing_stop_enabled: bool = False
    trailing_stop_callback_rate: float = 0.0
    trailing_stop_activation_pct: float = 0.0
