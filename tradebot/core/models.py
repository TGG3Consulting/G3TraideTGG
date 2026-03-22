# -*- coding: utf-8 -*-
"""
Trade Bot Core Models - Модели данных ядра.

Все модели exchange-agnostic - не содержат биржевой специфики.
Используют Signal из strategies/base.py напрямую.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from threading import Lock
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
            self.created_at = datetime.now(timezone.utc)


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

    # FIX #7: Exit order partial fill tracking
    # Если TP/SL частично исполнился а потом отменён - нужно знать сколько уже закрыто
    exit_filled_qty: float = 0.0     # Сколько уже исполнено по exit orders (SL/TP/Trailing)

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

    # Thread-safe lock (не включается в dataclass fields)
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        # Lock уже создан через field default_factory

    def close_safe(
        self,
        exit_reason: str,
        exit_price: float = 0.0,
        realized_pnl: float = 0.0,
    ) -> bool:
        """
        Thread-safe закрытие позиции.

        Гарантирует что status изменится только один раз,
        даже при concurrent вызовах из разных источников
        (WebSocket, REST sync, manual close).

        Returns:
            True если позиция была закрыта этим вызовом
            False если уже была закрыта ранее
        """
        with self._lock:
            if self.status == PositionStatus.CLOSED:
                return False  # Уже закрыта

            self.status = PositionStatus.CLOSED
            self.exit_reason = exit_reason
            self.exit_price = exit_price
            self.realized_pnl = realized_pnl
            self.closed_at = datetime.now(timezone.utc)
            return True

    @property
    def is_open(self) -> bool:
        """True если позиция OPEN (на бирже)."""
        return self.status == PositionStatus.OPEN

    @property
    def is_active(self) -> bool:
        """True если позиция не закрыта (PENDING или OPEN)."""
        return self.status in (PositionStatus.PENDING, PositionStatus.OPEN)

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
        hold_duration = datetime.now(timezone.utc) - self.opened_at
        return hold_duration >= timedelta(days=self.max_hold_days)

    def get_hold_days(self) -> float:
        """Получить количество дней удержания позиции."""
        if not self.opened_at:
            return 0.0
        # FIX #13: Для закрытой позиции считаем до closed_at, не до now
        if self.closed_at:
            hold_duration = self.closed_at - self.opened_at
        else:
            hold_duration = datetime.now(timezone.utc) - self.opened_at
        return hold_duration.total_seconds() / (24 * 3600)
