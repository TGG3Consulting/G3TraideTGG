# -*- coding: utf-8 -*-
"""
Trade Bot Core - Ядро торгового бота.

Модели данных и абстракции, НЕ зависящие от конкретной биржи.
"""

from .models import (
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
    PositionStatus,
    TradeOrder,
    Position,
)
from .interfaces import ExchangeInterface
from .exceptions import (
    ErrorCategory,
    BinanceError,
    NetworkError,
    RateLimitError,
    IPBanError,
    AuthError,
    LiquidationError,
    InsufficientBalanceError,
    OrderRejectedError,
    CancelFailedError,
    ValidationError,
    WebSocketError,
    parse_binance_error,
)

__all__ = [
    # Models
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "PositionSide",
    "PositionStatus",
    "TradeOrder",
    "Position",
    # Interfaces
    "ExchangeInterface",
    # Exceptions
    "ErrorCategory",
    "BinanceError",
    "NetworkError",
    "RateLimitError",
    "IPBanError",
    "AuthError",
    "LiquidationError",
    "InsufficientBalanceError",
    "OrderRejectedError",
    "CancelFailedError",
    "ValidationError",
    "WebSocketError",
    "parse_binance_error",
]
