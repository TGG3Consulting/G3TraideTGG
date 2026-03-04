# -*- coding: utf-8 -*-
"""
Data models for the manipulation detection screener.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


# =============================================================================
# ENUMS
# =============================================================================

class VulnerabilityLevel(Enum):
    """Уровень уязвимости пары к манипуляциям."""
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class AlertSeverity(Enum):
    """Серьёзность алерта."""
    INFO = 1
    WARNING = 2
    ALERT = 3
    CRITICAL = 4


# =============================================================================
# SYMBOL DATA
# =============================================================================

@dataclass
class SymbolStats:
    """Статистика торговой пары за 24 часа."""
    symbol: str
    price: Decimal
    volume_24h_usd: Decimal
    price_change_24h: Decimal
    trade_count_24h: int
    quote_asset: str
    base_asset: str = ""

    def __post_init__(self):
        if not self.base_asset:
            self.base_asset = self.symbol.replace(self.quote_asset, "")

    @property
    def is_usdt_pair(self) -> bool:
        return self.quote_asset == "USDT"

    @property
    def is_tradeable(self) -> bool:
        return self.volume_24h_usd > 0


@dataclass
class VulnerableSymbol:
    """Пара, уязвимая к манипуляциям."""
    symbol: str
    stats: SymbolStats
    vulnerability_level: VulnerabilityLevel
    vulnerability_reasons: list[str]
    order_book_depth_usd: Decimal  # $ нужно чтобы сдвинуть цену на 2%
    spread_percent: Decimal

    @property
    def manipulation_ease_score(self) -> int:
        """
        0-100: насколько легко манипулировать этой парой.
        Чем выше — тем легче.
        """
        score = 0

        # Чем меньше нужно денег для сдвига — тем легче
        if self.order_book_depth_usd < 1000:
            score += 40
        elif self.order_book_depth_usd < 5000:
            score += 30
        elif self.order_book_depth_usd < 20000:
            score += 20
        elif self.order_book_depth_usd < 50000:
            score += 10

        # Большой спред = низкая ликвидность
        if self.spread_percent > 2:
            score += 30
        elif self.spread_percent > 1:
            score += 20
        elif self.spread_percent > 0.5:
            score += 10

        # Низкий объём
        if self.stats.volume_24h_usd < 50_000:
            score += 30
        elif self.stats.volume_24h_usd < 200_000:
            score += 20
        elif self.stats.volume_24h_usd < 500_000:
            score += 10

        return min(100, score)


# =============================================================================
# TRADE DATA
# =============================================================================

@dataclass
class Trade:
    """Отдельная сделка."""
    price: Decimal
    qty: Decimal
    time: int  # timestamp ms
    is_buyer_maker: bool

    @property
    def value_usd(self) -> Decimal:
        return self.price * self.qty

    @property
    def side(self) -> str:
        # is_buyer_maker=True означает что покупатель был мейкером,
        # т.е. продавец был агрессором (market sell)
        return "SELL" if self.is_buyer_maker else "BUY"


# =============================================================================
# REAL-TIME STATE
# =============================================================================

@dataclass
class SymbolState:
    """Текущее состояние пары для детекции в реальном времени."""
    symbol: str

    # Price data
    last_price: Decimal = Decimal("0")
    price_1m_ago: Decimal = Decimal("0")
    price_5m_ago: Decimal = Decimal("0")
    price_1h_ago: Decimal = Decimal("0")

    # Volume data (rolling windows)
    volume_1m: Decimal = Decimal("0")
    volume_5m: Decimal = Decimal("0")
    volume_1h: Decimal = Decimal("0")
    avg_volume_1h: Decimal = Decimal("0")  # Baseline

    # Trade data (recent trades for pattern analysis)
    trades_1m: list[Trade] = field(default_factory=list)
    trades_5m: list[Trade] = field(default_factory=list)

    # Order book snapshot
    best_bid: Decimal = Decimal("0")
    best_ask: Decimal = Decimal("0")
    bid_volume_20: Decimal = Decimal("0")  # Top 20 levels (legacy)
    ask_volume_20: Decimal = Decimal("0")  # Top 20 levels (legacy)

    # ATR-based orderbook (adaptive depth based on volatility)
    bid_volume_atr: Decimal = Decimal("0")  # Volume within ±ATR% from mid
    ask_volume_atr: Decimal = Decimal("0")  # Volume within ±ATR% from mid
    raw_bids: list = field(default_factory=list)  # Full orderbook [(price, qty), ...]
    raw_asks: list = field(default_factory=list)  # Full orderbook [(price, qty), ...]

    # Klines for ATR calculation (last 60 1-minute candles)
    klines_1h: list = field(default_factory=list)  # [(high, low, close), ...]
    atr_1h_pct: Decimal = Decimal("5")  # ATR as % of price, clamped for orderbook depth
    atr_1h_pct_raw: Decimal = Decimal("0")  # FIX-ATR-RAW: реальный ATR без clamp, для SL/TP
    atr_is_real: bool = False  # FIX-H-3: True только после реального расчёта ATR

    # FIX-L-2: дневной ATR для глубины стакана
    klines_1d: list = field(default_factory=list)  # [(high, low, close), ...]
    atr_daily_pct: Decimal = Decimal("5")  # RAW — для SL/TP
    atr_daily_pct_depth: Decimal = Decimal("5")  # Clamped — для orderbook depth
    atr_daily_is_real: bool = False  # True после загрузки дневных klines

    # Timestamps
    last_trade_time: int = 0
    last_depth_time: int = 0
    last_update: datetime = field(default_factory=datetime.now)

    # Price history for baseline
    price_history: list[Decimal] = field(default_factory=list)

    @property
    def spread_pct(self) -> Decimal:
        """Текущий спред в процентах. Округлено до 4 знаков."""
        if self.best_bid == 0:
            return Decimal("0")
        raw = (self.best_ask - self.best_bid) / self.best_bid * 100
        return Decimal(str(round(float(raw), 4)))

    @property
    def mid_price(self) -> Decimal:
        """Средняя цена между bid и ask."""
        if self.best_bid == 0 or self.best_ask == 0:
            return self.last_price
        return (self.best_bid + self.best_ask) / 2

    @property
    def book_imbalance(self) -> Decimal:
        """
        Дисбаланс стакана (legacy, top 20 levels): -1 (все asks) до +1 (все bids).
        0 = сбалансирован.

        ИСПРАВЛЕНО: Округление до 4 знаков для предотвращения 25 знаков после запятой.
        """
        total = self.bid_volume_20 + self.ask_volume_20
        if total == 0:
            return Decimal("0")
        # Округляем результат деления до 4 знаков
        raw_imbalance = (self.bid_volume_20 - self.ask_volume_20) / total
        return Decimal(str(round(float(raw_imbalance), 4)))

    @property
    def book_imbalance_atr(self) -> Optional[Decimal]:
        """
        Дисбаланс стакана (ATR-based): использует глубину ±ATR% от mid price.

        FIX-IMBALANCE-1: семантически различные возвраты:
        - None = нет данных для анализа (total=0 или volume<$100)
        - Decimal("0") = реально сбалансированный стакан (bid ≈ ask)
        """
        bid = self.bid_volume_atr
        ask = self.ask_volume_atr
        total = bid + ask

        # FIX-IMBALANCE-1: нет данных вообще
        if total == 0:
            return None

        # FIX-IMBALANCE-1: данные есть но недостаточны для анализа
        if bid < 100 or ask < 100:
            return None

        raw_imbalance = (bid - ask) / total
        return Decimal(str(round(float(raw_imbalance), 4)))

    @property
    def price_change_1m_pct(self) -> Decimal:
        """Изменение цены за последнюю минуту в %."""
        if self.price_1m_ago == 0:
            return Decimal("0")
        return (self.last_price - self.price_1m_ago) / self.price_1m_ago * 100

    @property
    def price_change_5m_pct(self) -> Decimal:
        """Изменение цены за последние 5 минут в %."""
        if self.price_5m_ago == 0:
            return Decimal("0")
        return (self.last_price - self.price_5m_ago) / self.price_5m_ago * 100

    @property
    def price_change_1h_pct(self) -> Decimal:
        """Изменение цены за последний час в %."""
        if self.price_1h_ago == 0:
            return Decimal("0")
        return (self.last_price - self.price_1h_ago) / self.price_1h_ago * 100

    @property
    def volume_spike_ratio(self) -> Decimal:
        """
        Отношение текущего объёма к среднему.
        >10 = подозрительно, >50 = критично.
        Округлено до 2 знаков.
        """
        # Средний объём за 5 минут на основе часового
        avg_5m = self.avg_volume_1h / 12 if self.avg_volume_1h > 0 else Decimal("1")
        if avg_5m == 0:
            return Decimal("0")
        raw = self.volume_5m / avg_5m
        return Decimal(str(round(float(raw), 2)))

    @property
    def trade_count_1m(self) -> int:
        """Количество трейдов за последнюю минуту."""
        return len(self.trades_1m)

    @property
    def trade_count_5m(self) -> int:
        """Количество трейдов за последние 5 минут."""
        return len(self.trades_5m)

    @property
    def buy_ratio_5m(self) -> Optional[Decimal]:
        """
        Доля покупок среди последних трейдов.

        ИСПРАВЛЕНО: Возвращает None вместо 0.5 когда нет данных.
        Это предотвращает показ бессмысленных 50%/50% в алертах.
        """
        if not self.trades_5m:
            return None  # Нет данных = None, не дефолтные 50%
        buys = sum(1 for t in self.trades_5m if t.side == "BUY")
        return Decimal(str(round(buys / len(self.trades_5m), 4)))

    def reset_minute_counters(self):
        """Сброс минутных счётчиков (вызывается каждую минуту)."""
        self.price_1m_ago = self.last_price
        self.volume_1m = Decimal("0")
        self.trades_1m.clear()

    def reset_5min_counters(self):
        """Сброс 5-минутных счётчиков."""
        self.price_5m_ago = self.last_price
        self.volume_5m = Decimal("0")
        self.trades_5m.clear()

    def reset_hourly_counters(self):
        """Сброс часовых счётчиков."""
        self.price_1h_ago = self.last_price
        # Обновляем baseline
        if self.volume_1h > 0:
            self.avg_volume_1h = (self.avg_volume_1h + self.volume_1h) / 2
        self.volume_1h = Decimal("0")


# =============================================================================
# DETECTION RESULTS
# =============================================================================

@dataclass
class Detection:
    """Результат детекции манипуляции."""
    symbol: str
    timestamp: datetime
    severity: AlertSeverity
    detection_type: str
    score: int  # 0-100
    details: dict[str, Any]
    evidence: list[str]

    def to_alert_payload(self) -> dict:
        """Формат для отправки в Binance API."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "severity": self.severity.name,
            "type": self.detection_type,
            "score": self.score,
            "details": {
                k: str(v) if isinstance(v, Decimal) else v
                for k, v in self.details.items()
            },
            "evidence": self.evidence,
        }

    def __str__(self) -> str:
        return (
            f"[{self.severity.name}] {self.symbol} - {self.detection_type} "
            f"(score={self.score})"
        )


@dataclass
class VolumeBaseline:
    """Baseline объёма для детекции аномалий."""
    mean: Decimal
    std: Decimal
    median: Decimal
    p95: Decimal
    samples: int = 0
