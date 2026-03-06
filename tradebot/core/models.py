# -*- coding: utf-8 -*-
"""
Trade Bot Core Models - Модели данных ядра.

Все модели exchange-agnostic - не содержат биржевой специфики.
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
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


class OrderStatus(Enum):
    """Статус ордера."""
    PENDING = "PENDING"          # Создан, но не отправлен
    SUBMITTED = "SUBMITTED"      # Отправлен на биржу
    OPEN = "OPEN"                # Активен на бирже
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"            # Полностью исполнен
    CANCELLED = "CANCELLED"      # Отменён
    REJECTED = "REJECTED"        # Отклонён биржей
    EXPIRED = "EXPIRED"          # Истёк


class PositionStatus(Enum):
    """Статус позиции."""
    PENDING = "PENDING"          # Ожидает открытия
    OPEN = "OPEN"                # Открыта
    CLOSING = "CLOSING"          # В процессе закрытия
    CLOSED = "CLOSED"            # Закрыта


class SignalAction(Enum):
    """Действие по сигналу."""
    FULL = "FULL"                # Полный размер позиции
    DYN = "DYN"                  # Динамический размер
    OFF = "OFF"                  # Пропустить


# =============================================================================
# TRADE SIGNAL - Входящий сигнал от генератора
# =============================================================================

@dataclass
class TradeSignal:
    """
    Торговый сигнал от генератора (telegram_runner.py).

    Это данные, которые приходят через API от генератора сигналов.
    Содержит всё необходимое для размещения ордера.
    """
    # === ОБЯЗАТЕЛЬНЫЕ ПОЛЯ (минимум для ордера) ===
    signal_id: str               # Уникальный ID: "20240115_BTCUSDT_LONG"
    symbol: str                  # Торговая пара: "BTCUSDT"
    direction: str               # "LONG" или "SHORT"
    entry_price: float           # Цена входа
    stop_loss: float             # Цена стоп-лосса
    take_profit: float           # Цена тейк-профита

    # === КОНТЕКСТ СИГНАЛА ===
    strategy: str = ""           # Название стратегии: "ls_fade", "momentum"
    signal_date: Optional[datetime] = None  # Когда сгенерирован сигнал
    reason: str = ""             # Причина сигнала (для логов)

    # === SIZING ===
    action: SignalAction = SignalAction.FULL  # FULL/DYN/OFF

    # === РИСК-ПАРАМЕТРЫ (в процентах) ===
    sl_pct: float = 0.0          # SL в % от entry
    tp_pct: float = 0.0          # TP в % от entry

    # === ДОПОЛНИТЕЛЬНЫЙ КОНТЕКСТ ===
    coin_regime: str = ""        # BULL/BEAR/SIDEWAYS/etc
    coin_volatility: float = 0.0 # ATR%

    # === METADATA ===
    metadata: Dict[str, Any] = field(default_factory=dict)

    # === TIMESTAMPS ===
    received_at: Optional[datetime] = None  # Когда получен ботом

    def __post_init__(self):
        """Валидация и нормализация."""
        # Нормализуем direction
        self.direction = self.direction.upper()
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"Invalid direction: {self.direction}")

        # Нормализуем symbol
        self.symbol = self.symbol.upper()

        # Проставляем received_at если не задан
        if self.received_at is None:
            self.received_at = datetime.utcnow()

        # Рассчитываем sl_pct/tp_pct если не заданы
        if self.sl_pct == 0.0 and self.entry_price > 0:
            if self.direction == "LONG":
                self.sl_pct = abs(self.entry_price - self.stop_loss) / self.entry_price * 100
            else:
                self.sl_pct = abs(self.stop_loss - self.entry_price) / self.entry_price * 100

        if self.tp_pct == 0.0 and self.entry_price > 0:
            if self.direction == "LONG":
                self.tp_pct = abs(self.take_profit - self.entry_price) / self.entry_price * 100
            else:
                self.tp_pct = abs(self.entry_price - self.take_profit) / self.entry_price * 100

    @property
    def is_long(self) -> bool:
        """True если LONG."""
        return self.direction == "LONG"

    @property
    def is_short(self) -> bool:
        """True если SHORT."""
        return self.direction == "SHORT"

    @property
    def position_side(self) -> PositionSide:
        """Возвращает PositionSide для Hedge Mode."""
        return PositionSide.LONG if self.is_long else PositionSide.SHORT

    @property
    def entry_side(self) -> OrderSide:
        """Сторона ордера для входа."""
        return OrderSide.BUY if self.is_long else OrderSide.SELL

    @property
    def exit_side(self) -> OrderSide:
        """Сторона ордера для выхода."""
        return OrderSide.SELL if self.is_long else OrderSide.BUY

    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь для JSON."""
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "strategy": self.strategy,
            "signal_date": self.signal_date.isoformat() if self.signal_date else None,
            "reason": self.reason,
            "action": self.action.value,
            "sl_pct": self.sl_pct,
            "tp_pct": self.tp_pct,
            "coin_regime": self.coin_regime,
            "coin_volatility": self.coin_volatility,
            "metadata": self.metadata,
            "received_at": self.received_at.isoformat() if self.received_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeSignal":
        """Создание из словаря (JSON)."""
        # Обработка action
        action_str = data.get("action", "FULL")
        if isinstance(action_str, str):
            action = SignalAction(action_str.upper())
        else:
            action = action_str

        # Обработка дат
        signal_date = data.get("signal_date")
        if isinstance(signal_date, str):
            signal_date = datetime.fromisoformat(signal_date)

        received_at = data.get("received_at")
        if isinstance(received_at, str):
            received_at = datetime.fromisoformat(received_at)

        return cls(
            signal_id=data["signal_id"],
            symbol=data["symbol"],
            direction=data["direction"],
            entry_price=float(data["entry_price"]),
            stop_loss=float(data["stop_loss"]),
            take_profit=float(data["take_profit"]),
            strategy=data.get("strategy", ""),
            signal_date=signal_date,
            reason=data.get("reason", ""),
            action=action,
            sl_pct=float(data.get("sl_pct", 0.0)),
            tp_pct=float(data.get("tp_pct", 0.0)),
            coin_regime=data.get("coin_regime", ""),
            coin_volatility=float(data.get("coin_volatility", 0.0)),
            metadata=data.get("metadata", {}),
            received_at=received_at,
        )


# =============================================================================
# ORDER - Ордер на бирже
# =============================================================================

@dataclass
class Order:
    """
    Ордер (отправляется на биржу).

    Exchange-agnostic представление ордера.
    """
    # === ИДЕНТИФИКАЦИЯ ===
    order_id: str                # Наш внутренний ID
    signal_id: str               # ID сигнала, породившего ордер
    symbol: str                  # Торговая пара

    # === ПАРАМЕТРЫ ОРДЕРА ===
    side: OrderSide              # BUY/SELL
    order_type: OrderType        # MARKET/LIMIT/STOP_MARKET/etc
    quantity: float              # Количество в базовой валюте
    price: Optional[float] = None        # Цена (для LIMIT)
    stop_price: Optional[float] = None   # Стоп-цена (для STOP orders)

    # === ПОЗИЦИЯ ===
    position_side: PositionSide = PositionSide.BOTH  # Для Hedge Mode
    reduce_only: bool = False    # Только закрытие позиции

    # === СТАТУС ===
    status: OrderStatus = OrderStatus.PENDING

    # === EXCHANGE DATA (заполняется после отправки) ===
    exchange_order_id: str = ""  # ID ордера на бирже
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    commission: float = 0.0
    commission_asset: str = ""

    # === TIMESTAMPS ===
    created_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None

    # === ERROR INFO ===
    error_code: str = ""
    error_message: str = ""

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.SUBMITTED)

    @property
    def is_terminal(self) -> bool:
        """Ордер в терминальном состоянии (больше не изменится)."""
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": self.price,
            "stop_price": self.stop_price,
            "position_side": self.position_side.value,
            "reduce_only": self.reduce_only,
            "status": self.status.value,
            "exchange_order_id": self.exchange_order_id,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "commission": self.commission,
            "commission_asset": self.commission_asset,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


# =============================================================================
# POSITION - Открытая позиция
# =============================================================================

@dataclass
class Position:
    """
    Позиция (открытая сделка).

    Отслеживает состояние позиции от открытия до закрытия.
    """
    # === ИДЕНТИФИКАЦИЯ ===
    position_id: str             # Наш внутренний ID
    signal_id: str               # ID сигнала, породившего позицию
    symbol: str                  # Торговая пара

    # === НАПРАВЛЕНИЕ ===
    side: PositionSide           # LONG/SHORT

    # === РАЗМЕР ===
    quantity: float              # Размер позиции в базовой валюте
    entry_price: float           # Средняя цена входа

    # === УРОВНИ ===
    stop_loss: float             # Цена SL
    take_profit: float           # Цена TP

    # === СТАТУС ===
    status: PositionStatus = PositionStatus.PENDING

    # === СВЯЗАННЫЕ ОРДЕРА ===
    entry_order_id: str = ""     # Ордер входа
    sl_order_id: str = ""        # SL ордер
    tp_order_id: str = ""        # TP ордер

    # === P&L ===
    unrealized_pnl: float = 0.0  # Нереализованный P&L
    realized_pnl: float = 0.0    # Реализованный P&L (после закрытия)
    total_commission: float = 0.0

    # === EXIT INFO ===
    exit_price: float = 0.0      # Цена выхода
    exit_reason: str = ""        # "TP", "SL", "MANUAL", "TIMEOUT"

    # === TIMESTAMPS ===
    created_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    # === METADATA ===
    strategy: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()

    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN

    @property
    def is_closed(self) -> bool:
        return self.status == PositionStatus.CLOSED

    @property
    def is_long(self) -> bool:
        return self.side == PositionSide.LONG

    @property
    def is_short(self) -> bool:
        return self.side == PositionSide.SHORT

    @property
    def notional_value(self) -> float:
        """Номинальная стоимость позиции в USDT."""
        return self.quantity * self.entry_price

    def calculate_pnl(self, current_price: float) -> float:
        """Рассчитать нереализованный P&L."""
        if self.is_long:
            return (current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - current_price) * self.quantity

    def calculate_pnl_pct(self, current_price: float) -> float:
        """Рассчитать нереализованный P&L в %."""
        if self.entry_price == 0:
            return 0.0
        if self.is_long:
            return (current_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - current_price) / self.entry_price * 100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "status": self.status.value,
            "entry_order_id": self.entry_order_id,
            "sl_order_id": self.sl_order_id,
            "tp_order_id": self.tp_order_id,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "total_commission": self.total_commission,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "strategy": self.strategy,
            "metadata": self.metadata,
        }
