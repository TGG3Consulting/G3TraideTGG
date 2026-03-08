# -*- coding: utf-8 -*-
"""
Trade Bot Core Models - Модели данных ядра.

Все модели exchange-agnostic - не содержат биржевой специфики.
Используют Signal из strategies/base.py напрямую.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


# =============================================================================
# ENUMS
# =============================================================================

class OrderSide(Enum):
    """Направление ордера."""
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(Enum):
    """Направление позиции (для Hedge Mode)."""
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"  # One-way mode


class OrderType(Enum):
    """Тип ордера."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"


class OrderStatus(Enum):
    """Статус ордера."""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PositionStatus(Enum):
    """Статус позиции."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"


# =============================================================================
# TRADE ORDER - Ордер на биржу
# =============================================================================

@dataclass
class TradeOrder:
    """
    Ордер для отправки на биржу.

    Создаётся из Signal + размер позиции.
    """
    # === ИДЕНТИФИКАЦИЯ ===
    order_id: str                # Наш внутренний ID
    signal_id: str               # ID сигнала из Signal.signal_id
    symbol: str                  # BTCUSDT

    # === ПАРАМЕТРЫ ОРДЕРА ===
    side: OrderSide              # BUY/SELL
    order_type: OrderType        # MARKET/LIMIT/etc
    quantity: float              # Количество в базовой валюте
    price: Optional[float] = None        # Цена (для LIMIT)
    stop_price: Optional[float] = None   # Стоп-цена

    # === ПОЗИЦИЯ ===
    position_side: PositionSide = PositionSide.BOTH
    reduce_only: bool = False

    # === СТАТУС ===
    status: OrderStatus = OrderStatus.PENDING

    # === EXCHANGE DATA ===
    exchange_order_id: str = ""
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    commission: float = 0.0

    # === TIMESTAMPS ===
    created_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None

    # === ERROR ===
    error_message: str = ""

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()


# =============================================================================
# POSITION - Открытая позиция
# =============================================================================

@dataclass
class Position:
    """
    Открытая позиция.

    Создаётся после исполнения entry ордера.
    """
    # === ИДЕНТИФИКАЦИЯ ===
    position_id: str
    signal_id: str
    symbol: str

    # === НАПРАВЛЕНИЕ ===
    side: PositionSide           # LONG/SHORT

    # === РАЗМЕР ===
    quantity: float              # Фактически исполненное количество
    entry_price: float

    # === УРОВНИ ===
    stop_loss: float
    take_profit: float

    # === PARTIAL FILL TRACKING ===
    requested_quantity: float = 0.0  # Запрошенное количество (для partial fill tracking)
    is_partial_fill: bool = False    # True если entry был partial fill

    # === СТАТУС ===
    status: PositionStatus = PositionStatus.PENDING

    # === СВЯЗАННЫЕ ОРДЕРА ===
    entry_order_id: str = ""
    sl_order_id: str = ""
    tp_order_id: str = ""
    trailing_stop_order_id: str = ""  # Trailing Stop ордер (альтернатива TP)

    # === TRAILING STOP PARAMS ===
    trailing_stop_enabled: bool = False
    trailing_stop_callback_rate: float = 0.0  # 1.0 = 1%
    trailing_stop_activation_price: float = 0.0  # Цена активации (0 = сразу)

    # === P&L ===
    realized_pnl: float = 0.0
    total_commission: float = 0.0

    # === EXIT INFO ===
    exit_price: float = 0.0
    exit_reason: str = ""        # "TP", "SL", "MANUAL"

    # === TIMESTAMPS ===
    created_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    # === CONTEXT ===
    strategy: str = ""
    regime_action: str = "FULL"  # FULL/DYN/OFF - влияет на размер
    max_hold_days: int = 14      # Автозакрытие по таймауту

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()

    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN

    @property
    def is_long(self) -> bool:
        return self.side == PositionSide.LONG

    def calculate_pnl_pct(self, current_price: float) -> float:
        """Рассчитать нереализованный P&L в %."""
        if self.entry_price == 0:
            return 0.0
        if self.is_long:
            return (current_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - current_price) / self.entry_price * 100

    def is_expired(self) -> bool:
        """Проверить истёк ли max_hold_days."""
        if not self.opened_at or not self.is_open:
            return False
        from datetime import timedelta
        hold_duration = datetime.utcnow() - self.opened_at
        return hold_duration >= timedelta(days=self.max_hold_days)

    def get_hold_days(self) -> float:
        """Получить количество дней удержания позиции."""
        if not self.opened_at:
            return 0.0
        hold_duration = datetime.utcnow() - self.opened_at
        return hold_duration.total_seconds() / (24 * 3600)
