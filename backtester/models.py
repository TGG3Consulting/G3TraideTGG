# -*- coding: utf-8 -*-
"""
ManipBackTester - Модели данных.

Все dataclass'ы для работы бэктестера.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any


class Direction(Enum):
    """Направление сделки."""
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(Enum):
    """Причина закрытия позиции."""
    TP1 = "TP1"
    TP2 = "TP2"
    TP3 = "TP3"
    STOP_LOSS = "STOP_LOSS"
    TIMEOUT = "TIMEOUT"
    NOT_FILLED = "NOT_FILLED"
    PARTIAL_TP1 = "PARTIAL_TP1"  # Частичное закрытие на TP1
    PARTIAL_TP2 = "PARTIAL_TP2"  # Частичное закрытие на TP2


@dataclass
class TakeProfit:
    """Уровень тейк-профита."""
    label: str          # TP1, TP2, TP3
    price: Decimal
    percent: float      # % от входа
    portion: int        # % позиции для закрытия


@dataclass
class MLFeatures:
    """
    ВСЕ фичи для ML из сигнала.

    Извлекаются из: accumulation_score, signal.details,
    futures_snapshot, spot_snapshot, trigger_detection.

    Названия полей соответствуют путям в JSON с заменой '.' на '_'.
    """
    # === ACCUMULATION SCORE COMPONENTS (22) ===
    acc_oi_growth: int = 0
    acc_oi_stability: int = 0
    acc_funding_cheap: int = 0
    acc_funding_gradient: int = 0
    acc_crowd_bearish: int = 0
    acc_crowd_bullish: int = 0
    acc_coordinated_buying: int = 0
    acc_volume_accumulation: int = 0
    acc_cross_oi_migration: int = 0
    acc_cross_price_lead: int = 0
    acc_spot_bid_pressure: int = 0
    acc_spot_ask_weakness: int = 0
    acc_spot_imbalance_score: int = 0
    acc_futures_bid_pressure: int = 0
    acc_futures_ask_weakness: int = 0
    acc_futures_imbalance_score: int = 0
    acc_orderbook_divergence: int = 0
    acc_orderbook_total: int = 0
    acc_wash_trading_penalty: int = 0
    acc_extreme_funding_penalty: int = 0
    acc_orderbook_against_penalty: int = 0
    acc_total: int = 0

    # === FUTURES SNAPSHOT - OI ===
    futures_oi_value: float = 0.0
    futures_oi_value_usd: float = 0.0
    futures_oi_change_1m_pct: float = 0.0
    futures_oi_change_5m_pct: float = 0.0
    futures_oi_change_1h_pct: float = 0.0

    # === FUTURES SNAPSHOT - FUNDING ===
    futures_funding_rate: float = 0.0
    futures_funding_rate_pct: float = 0.0
    futures_funding_mark_price: float = 0.0

    # === FUTURES SNAPSHOT - LONG/SHORT RATIO ===
    futures_long_account_pct: float = 0.0
    futures_short_account_pct: float = 0.0
    futures_long_short_ratio: float = 0.0

    # === FUTURES SNAPSHOT - PRICE CHANGES ===
    futures_price_change_5m_pct: float = 0.0
    futures_price_change_1h_pct: float = 0.0

    # === SPOT SNAPSHOT - PRICE ===
    spot_price_bid: float = 0.0
    spot_price_ask: float = 0.0
    spot_price_last: float = 0.0
    spot_price_mid: float = 0.0
    spot_price_spread_pct: float = 0.0

    # === SPOT SNAPSHOT - PRICE CHANGES ===
    spot_price_change_1m_pct: float = 0.0
    spot_price_change_5m_pct: float = 0.0
    spot_price_change_1h_pct: float = 0.0

    # === SPOT SNAPSHOT - VOLUME ===
    spot_volume_1m: float = 0.0
    spot_volume_5m: float = 0.0
    spot_volume_1h: float = 0.0
    spot_volume_avg_1h: float = 0.0
    spot_volume_spike_ratio: float = 0.0

    # === SPOT SNAPSHOT - ORDERBOOK ===
    spot_orderbook_bid_volume_20: float = 0.0
    spot_orderbook_ask_volume_20: float = 0.0
    spot_orderbook_imbalance: float = 0.0

    # === SPOT SNAPSHOT - TRADES ===
    spot_trades_count_1m: int = 0
    spot_trades_count_5m: int = 0
    spot_trades_buy_ratio_5m: float = 0.0

    # === SIGNAL DETAILS ===
    signal_details_book_imbalance: float = 0.0
    signal_details_volume_ratio: float = 0.0
    signal_details_orderbook_score: int = 0
    signal_details_spot_bid_volume_atr: float = 0.0
    signal_details_spot_ask_volume_atr: float = 0.0
    signal_details_spot_imbalance_atr: float = 0.0
    signal_details_spot_atr_pct: float = 0.0

    # === TRIGGER DETECTION ===
    trigger_type: str = ""
    trigger_severity: int = 0
    trigger_score: int = 0

    # === TRIGGER DETECTION DETAILS ===
    trigger_details_bid_volume: float = 0.0
    trigger_details_ask_volume: float = 0.0
    trigger_details_buy_ratio: float = 0.0
    trigger_details_sell_ratio: float = 0.0
    trigger_details_trades_count: int = 0
    trigger_details_volume_5m: float = 0.0
    trigger_details_current_price: float = 0.0

    # === CONFIG (signal generation settings) ===
    config_min_accumulation_score: int = 0
    config_min_probability: int = 0
    config_min_risk_reward: float = 0.0
    config_default_sl_pct: float = 0.0
    config_tp1_ratio: float = 0.0
    config_tp2_ratio: float = 0.0
    config_tp3_ratio: float = 0.0

    # === TRIGGER DETECTION DETAILS (missing 2) ===
    trigger_details_long_account_pct: float = 0.0
    trigger_details_short_account_pct: float = 0.0

    # === TIMESTAMPS (extracted from signal) ===
    signal_hour: int = 0          # Hour of day (0-23)
    signal_minute: int = 0        # Minute (0-59)
    signal_day_of_week: int = 0   # Day of week (0=Monday, 6=Sunday)

    # === OI HISTORY (derived from array) ===
    oi_history_count: int = 0
    oi_history_first: float = 0.0
    oi_history_last: float = 0.0
    oi_history_min: float = 0.0
    oi_history_max: float = 0.0
    oi_history_avg: float = 0.0
    oi_history_std: float = 0.0
    oi_history_trend: float = 0.0       # (last - first) / first * 100
    oi_history_range_pct: float = 0.0   # (max - min) / avg * 100

    # === FUNDING HISTORY (derived from array) ===
    funding_history_count: int = 0
    funding_history_first: float = 0.0
    funding_history_last: float = 0.0
    funding_history_min: float = 0.0
    funding_history_max: float = 0.0
    funding_history_avg: float = 0.0
    funding_history_std: float = 0.0
    funding_history_trend: float = 0.0  # last - first

    # === PRICE HISTORY (derived from array) ===
    price_history_count: int = 0
    price_history_first: float = 0.0
    price_history_last: float = 0.0

    # === TRIGGER DETECTIONS (from array) ===
    trigger_detections_count: int = 0

    # === ADDITIONAL FIELDS (previously missing) ===
    # Entry zone boundaries
    entry_zone_low: float = 0.0
    entry_zone_high: float = 0.0

    # Scenarios (text)
    scenario_bullish: str = ""
    scenario_bearish: str = ""

    # Evidence (joined text)
    evidence_text: str = ""
    evidence_count: int = 0

    # Meta timestamps
    logged_at: str = ""
    futures_last_update: str = ""
    spot_last_update: str = ""
    oi_timestamp: str = ""
    funding_time: str = ""
    ls_ratio_timestamp: str = ""

    # === BACKWARD COMPATIBILITY ALIASES ===
    # These properties provide old names for backward compatibility
    @property
    def oi_change_1m_pct(self) -> float:
        return self.futures_oi_change_1m_pct

    @property
    def oi_change_5m_pct(self) -> float:
        return self.futures_oi_change_5m_pct

    @property
    def oi_change_1h_pct(self) -> float:
        return self.futures_oi_change_1h_pct

    @property
    def funding_rate_pct(self) -> float:
        return self.futures_funding_rate_pct

    @property
    def long_account_pct(self) -> float:
        return self.futures_long_account_pct

    @property
    def short_account_pct(self) -> float:
        return self.futures_short_account_pct

    @property
    def long_short_ratio(self) -> float:
        return self.futures_long_short_ratio

    @property
    def spot_spread_pct(self) -> float:
        return self.spot_price_spread_pct

    @property
    def volume_spike_ratio(self) -> float:
        return self.spot_volume_spike_ratio

    @property
    def buy_ratio_5m(self) -> float:
        return self.spot_trades_buy_ratio_5m

    @property
    def trades_count_1m(self) -> int:
        return self.spot_trades_count_1m

    @property
    def trades_count_5m(self) -> int:
        return self.spot_trades_count_5m

    @property
    def volume_1m(self) -> float:
        return self.spot_volume_1m

    @property
    def volume_5m(self) -> float:
        return self.spot_volume_5m

    @property
    def volume_1h(self) -> float:
        return self.spot_volume_1h

    @property
    def volume_avg_1h(self) -> float:
        return self.spot_volume_avg_1h

    @property
    def oi_value_usd(self) -> float:
        return self.futures_oi_value_usd

    @property
    def spot_bid_volume_atr(self) -> float:
        return self.signal_details_spot_bid_volume_atr

    @property
    def spot_ask_volume_atr(self) -> float:
        return self.signal_details_spot_ask_volume_atr

    @property
    def spot_imbalance_atr(self) -> float:
        return self.signal_details_spot_imbalance_atr

    @property
    def spot_bid_volume_20(self) -> float:
        return self.spot_orderbook_bid_volume_20

    @property
    def spot_ask_volume_20(self) -> float:
        return self.spot_orderbook_ask_volume_20

    @property
    def orderbook_score(self) -> int:
        return self.signal_details_orderbook_score

    @property
    def spot_atr_pct(self) -> float:
        return self.signal_details_spot_atr_pct

    @property
    def spot_orderbook_imbalance_legacy(self) -> float:
        return self.spot_orderbook_imbalance


@dataclass
class ParsedSignal:
    """Сигнал извлечённый из логов."""
    signal_id: str
    symbol: str
    timestamp: datetime
    direction: Direction

    # Точки входа/выхода из СИГНАЛА (не выдумывать!)
    entry_limit: Decimal        # Рекомендуемая цена входа
    entry_zone_low: Decimal
    entry_zone_high: Decimal
    current_price: Decimal      # Цена в момент сигнала

    stop_loss: Decimal
    stop_loss_pct: float

    # Take Profits
    tp1: TakeProfit
    tp2: TakeProfit
    tp3: TakeProfit

    # Risk/Reward
    risk_reward: float

    # Метаданные
    probability: int = 0
    confidence: str = ""
    signal_type: str = ""
    max_hold_hours: int = 24

    # Сырые данные для анализа
    evidence: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    accumulation_score: Dict[str, int] = field(default_factory=dict)

    # ML Features (все фичи для машинного обучения)
    ml_features: MLFeatures = field(default_factory=MLFeatures)


@dataclass
class Kline:
    """Свеча с Binance Futures."""
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    # Дополнительные данные
    quote_volume: Decimal = Decimal("0")
    trades_count: int = 0
    taker_buy_volume: Decimal = Decimal("0")
    taker_buy_quote_volume: Decimal = Decimal("0")


@dataclass
class PartialClose:
    """Частичное закрытие позиции."""
    timestamp: datetime
    price: Decimal
    portion_pct: int        # % от начальной позиции
    pnl: Decimal
    fee: Decimal
    tp_label: str           # TP1, TP2, TP3


@dataclass
class BacktestResult:
    """Результат бэктеста одного сигнала."""
    signal: ParsedSignal

    # Вход
    entry_filled: bool
    actual_entry_price: Optional[Decimal] = None
    actual_entry_time: Optional[datetime] = None

    # Выход
    exit_reason: ExitReason = ExitReason.NOT_FILLED
    final_exit_price: Optional[Decimal] = None
    final_exit_time: Optional[datetime] = None

    # PnL (в валюте котировки, нормализовано к 100% позиции)
    gross_pnl: Decimal = Decimal("0")           # До комиссий
    total_fees: Decimal = Decimal("0")          # Все комиссии
    total_funding: Decimal = Decimal("0")       # Funding оплачен
    net_pnl: Decimal = Decimal("0")             # Чистый PnL

    # В процентах от позиции
    pnl_percent: Decimal = Decimal("0")         # Gross %
    net_pnl_percent: Decimal = Decimal("0")     # Net %

    # Детали
    hold_time_hours: float = 0.0

    # Частичные закрытия
    partial_closes: List[PartialClose] = field(default_factory=list)

    # Какие TP сработали
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    sl_hit: bool = False

    # Рыночные данные в момент выхода
    market_data_at_exit: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BinanceFees:
    """Комиссии Binance Futures USDT-M."""
    # Стандартные комиссии (без VIP, без BNB скидки)
    maker: Decimal = Decimal("0.0002")   # 0.02%
    taker: Decimal = Decimal("0.0005")   # 0.05%

    # Средний funding rate (примерно 0.01% каждые 8 часов)
    avg_funding_rate: Decimal = Decimal("0.0001")

    def entry_fee(self, notional: Decimal, is_limit: bool = True) -> Decimal:
        """Комиссия за вход."""
        rate = self.maker if is_limit else self.taker
        return notional * rate

    def exit_fee(self, notional: Decimal, is_limit: bool = True) -> Decimal:
        """Комиссия за выход."""
        rate = self.maker if is_limit else self.taker
        return notional * rate

    def funding_cost(
        self,
        notional: Decimal,
        hold_hours: float,
        direction: Direction
    ) -> Decimal:
        """
        Расчёт funding за время удержания.

        Funding платится каждые 8 часов.
        Positive funding: LONG платит SHORT
        Negative funding: SHORT платит LONG

        Упрощённо используем средний funding rate.
        """
        funding_periods = int(hold_hours / 8)
        if funding_periods == 0:
            return Decimal("0")

        # Упрощение: LONG всегда платит при positive funding (рынок в целом bullish)
        # В реальности зависит от текущего funding rate
        return notional * self.avg_funding_rate * funding_periods


@dataclass
class BacktestSummary:
    """Итоговая статистика бэктеста."""
    # Общее
    total_signals: int = 0
    filled_signals: int = 0
    not_filled_signals: int = 0

    # Результаты
    wins: int = 0
    losses: int = 0
    breakeven: int = 0

    win_rate: float = 0.0

    # PnL
    total_gross_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    total_funding: Decimal = Decimal("0")
    total_net_pnl: Decimal = Decimal("0")

    # Средние
    avg_win_pct: Decimal = Decimal("0")
    avg_loss_pct: Decimal = Decimal("0")
    avg_hold_hours: float = 0.0

    # По exit reason
    exits_by_reason: Dict[str, int] = field(default_factory=dict)

    # По TP
    tp1_hits: int = 0
    tp2_hits: int = 0
    tp3_hits: int = 0
    sl_hits: int = 0
    timeout_exits: int = 0

    # Лучший/худший
    best_trade_pnl_pct: Decimal = Decimal("0")
    worst_trade_pnl_pct: Decimal = Decimal("0")
    best_trade_symbol: str = ""
    worst_trade_symbol: str = ""

    # Время
    backtest_start: Optional[datetime] = None
    backtest_end: Optional[datetime] = None
    signals_time_range: str = ""
