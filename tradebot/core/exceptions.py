# -*- coding: utf-8 -*-
"""
TradeBot Exceptions - Система исключений для обработки ошибок.

Классификация ошибок Binance API:
- Retryable: сетевые, rate limit, временные
- Fatal: auth, ликвидация, IP ban
- Recoverable: недостаток баланса, validation
"""

from enum import Enum
from typing import Optional, Dict, Any


class ErrorCategory(Enum):
    """Категории ошибок для определения стратегии обработки."""

    # Можно retry с exponential backoff
    NETWORK = "NETWORK"           # -1000, -1001, -1006, -1007
    RATE_LIMIT = "RATE_LIMIT"     # -1003, -1008, -1015, HTTP 429
    THROTTLED = "THROTTLED"       # -1008 system throttle

    # Критические - остановка бота
    IP_BAN = "IP_BAN"             # HTTP 418
    AUTH = "AUTH"                 # -1002, -1021, -1022, -2014, -2015
    LIQUIDATION = "LIQUIDATION"   # -2023

    # Пропустить сигнал + alert
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"  # -2018, -2019, -2024

    # Ордер отклонён - пропустить
    ORDER_REJECTED = "ORDER_REJECTED"  # -2010, -2020, -2021, -2025

    # Cancel не удался - продолжить
    CANCEL_FAILED = "CANCEL_FAILED"    # -2011, -2013

    # Ошибки валидации параметров
    VALIDATION = "VALIDATION"     # -4xxx серия, -1121

    # WebSocket
    WEBSOCKET = "WEBSOCKET"       # -1125, connection errors

    # Неизвестная ошибка
    UNKNOWN = "UNKNOWN"


# =============================================================================
# БАЗОВЫЙ КЛАСС (Exchange-Agnostic)
# =============================================================================

class ExchangeError(Exception):
    """
    Базовое исключение для всех бирж.

    Этот класс используется в ExchangeInterface для типизации callbacks.
    Конкретные реализации (BinanceError, BybitError) наследуют от него.

    Attributes:
        code: Код ошибки биржи
        message: Сообщение об ошибке
        category: Категория для определения стратегии обработки
        is_critical: True если требуется остановка бота
    """

    def __init__(
        self,
        code: int,
        message: str,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
    ):
        self.code = code
        self.message = message
        self.category = category
        super().__init__(f"[{code}] {message}")

    @property
    def is_critical(self) -> bool:
        """Критическая ошибка - нужна остановка бота."""
        return self.category in (
            ErrorCategory.IP_BAN,
            ErrorCategory.AUTH,
            ErrorCategory.LIQUIDATION,
        )


class BinanceError(ExchangeError):
    """
    Базовое исключение для ошибок Binance API.

    Attributes:
        code: Код ошибки Binance (-1000, -2010, etc)
        message: Сообщение об ошибке
        category: Категория для определения стратегии обработки
        http_status: HTTP статус код (если применимо)
        raw_response: Сырой ответ от API
        retryable: Можно ли повторить запрос
        retry_after: Время ожидания перед retry (секунды)
    """

    def __init__(
        self,
        code: int,
        message: str,
        category: ErrorCategory = ErrorCategory.UNKNOWN,
        http_status: int = 0,
        raw_response: Optional[Dict[str, Any]] = None,
        retry_after: int = 0,
    ):
        # Binance-specific attributes
        self.http_status = http_status
        self.raw_response = raw_response or {}
        self.retry_after = retry_after

        # Call ExchangeError __init__
        super().__init__(code=code, message=message, category=category)

    @property
    def retryable(self) -> bool:
        """Можно ли повторить запрос."""
        return self.category in (
            ErrorCategory.NETWORK,
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.THROTTLED,
        )

    @property
    def is_critical(self) -> bool:
        """Критическая ошибка - нужна остановка бота."""
        return self.category in (
            ErrorCategory.IP_BAN,
            ErrorCategory.AUTH,
            ErrorCategory.LIQUIDATION,
        )

    @property
    def should_skip_signal(self) -> bool:
        """Нужно пропустить текущий сигнал."""
        return self.category in (
            ErrorCategory.INSUFFICIENT_BALANCE,
            ErrorCategory.ORDER_REJECTED,
            ErrorCategory.VALIDATION,
        )


# =============================================================================
# СПЕЦИАЛИЗИРОВАННЫЕ ИСКЛЮЧЕНИЯ
# =============================================================================

class NetworkError(BinanceError):
    """Сетевая ошибка - retry с backoff."""

    def __init__(self, code: int, message: str, **kwargs):
        super().__init__(
            code=code,
            message=message,
            category=ErrorCategory.NETWORK,
            **kwargs
        )


class RateLimitError(BinanceError):
    """Rate limit превышен - exponential backoff."""

    def __init__(self, code: int, message: str, retry_after: int = 1, **kwargs):
        super().__init__(
            code=code,
            message=message,
            category=ErrorCategory.RATE_LIMIT,
            retry_after=retry_after,
            **kwargs
        )


class IPBanError(BinanceError):
    """IP заблокирован - пауза и retry."""

    def __init__(self, message: str = "IP auto-banned", retry_after: int = 120, **kwargs):
        super().__init__(
            code=418,
            message=message,
            category=ErrorCategory.IP_BAN,
            http_status=418,
            retry_after=retry_after,  # Начинаем с 2 минут
            **kwargs
        )


class AuthError(BinanceError):
    """Ошибка авторизации - остановка бота."""

    def __init__(self, message: str = "Invalid API key or permissions", code: int = -1002, **kwargs):
        super().__init__(
            code=code,
            message=message,
            category=ErrorCategory.AUTH,
            **kwargs
        )


class LiquidationError(BinanceError):
    """Пользователь в режиме ликвидации - КРИТИЧЕСКАЯ остановка."""

    def __init__(self, message: str = "User in liquidation mode", **kwargs):
        super().__init__(
            code=-2023,
            message=message,
            category=ErrorCategory.LIQUIDATION,
            **kwargs
        )


class InsufficientBalanceError(BinanceError):
    """Недостаточно баланса/маржи - пропустить сигнал."""

    def __init__(self, message: str = "Margin is insufficient", code: int = -2019, **kwargs):
        super().__init__(
            code=code,
            message=message,
            category=ErrorCategory.INSUFFICIENT_BALANCE,
            **kwargs
        )


class OrderRejectedError(BinanceError):
    """Ордер отклонён - пропустить."""

    def __init__(self, code: int, message: str, **kwargs):
        super().__init__(
            code=code,
            message=message,
            category=ErrorCategory.ORDER_REJECTED,
            **kwargs
        )


class CancelFailedError(BinanceError):
    """Отмена ордера не удалась - продолжить."""

    def __init__(self, code: int, message: str, **kwargs):
        super().__init__(
            code=code,
            message=message,
            category=ErrorCategory.CANCEL_FAILED,
            **kwargs
        )


class ValidationError(BinanceError):
    """Ошибка валидации параметров."""

    def __init__(self, code: int, message: str, **kwargs):
        super().__init__(
            code=code,
            message=message,
            category=ErrorCategory.VALIDATION,
            **kwargs
        )


class WebSocketError(BinanceError):
    """Ошибка WebSocket."""

    def __init__(self, code: int = 0, message: str = "WebSocket error", **kwargs):
        super().__init__(
            code=code,
            message=message,
            category=ErrorCategory.WEBSOCKET,
            **kwargs
        )


# =============================================================================
# ПАРСЕР ОШИБОК
# =============================================================================

def parse_binance_error(
    http_status: int,
    response_text: str,
) -> BinanceError:
    """
    Парсит ответ Binance API и возвращает соответствующее исключение.

    Args:
        http_status: HTTP статус код
        response_text: Текст ответа

    Returns:
        Соответствующее исключение BinanceError
    """
    import json

    # Парсим JSON если возможно
    code = 0
    message = response_text
    raw_response = {}

    try:
        raw_response = json.loads(response_text)
        code = raw_response.get("code", 0)
        message = raw_response.get("msg", response_text)
    except json.JSONDecodeError:
        pass

    # HTTP ошибки
    if http_status == 418:
        return IPBanError(
            message=message,
            retry_after=120,  # Начинаем с 2 минут
            raw_response=raw_response,
        )

    if http_status == 429:
        return RateLimitError(
            code=-1003,
            message="Rate limit exceeded (HTTP 429)",
            retry_after=1,
            http_status=429,
            raw_response=raw_response,
        )

    if http_status == 403:
        return AuthError(
            code=-1002,
            message="WAF Limit or forbidden",
            http_status=403,
            raw_response=raw_response,
        )

    if http_status >= 500:
        return NetworkError(
            code=-1001,
            message=f"Server error: {http_status}",
            http_status=http_status,
            raw_response=raw_response,
        )

    # Парсим по коду ошибки Binance

    # Network errors
    if code in (-1000, -1001, -1006, -1007):
        return NetworkError(
            code=code,
            message=message,
            http_status=http_status,
            raw_response=raw_response,
        )

    # Rate limit
    if code in (-1003, -1008, -1015):
        return RateLimitError(
            code=code,
            message=message,
            retry_after=1 if code == -1008 else 5,
            http_status=http_status,
            raw_response=raw_response,
        )

    # Auth errors
    if code in (-1002, -1021, -1022, -2014, -2015, -2017):
        return AuthError(
            code=code,
            message=message,
            http_status=http_status,
            raw_response=raw_response,
        )

    # Liquidation
    if code == -2023:
        return LiquidationError(
            message=message,
            http_status=http_status,
            raw_response=raw_response,
        )

    # Insufficient balance/margin
    if code in (-2018, -2019, -2024, -4050, -4051):
        return InsufficientBalanceError(
            code=code,
            message=message,
            http_status=http_status,
            raw_response=raw_response,
        )

    # Order rejected
    if code in (-2010, -2020, -2021, -2025, -4045, -4087, -4088):
        return OrderRejectedError(
            code=code,
            message=message,
            http_status=http_status,
            raw_response=raw_response,
        )

    # Cancel failed
    if code in (-2011, -2013):
        return CancelFailedError(
            code=code,
            message=message,
            http_status=http_status,
            raw_response=raw_response,
        )

    # WebSocket (проверяем ДО validation чтобы -1125 не попал в -11xx range)
    if code == -1125:
        return WebSocketError(
            code=code,
            message=message,
            http_status=http_status,
            raw_response=raw_response,
        )

    # Symbol error
    if code == -1121:
        return ValidationError(
            code=code,
            message=message,
            http_status=http_status,
            raw_response=raw_response,
        )

    # Validation errors (-4xxx серия: от -4000 до -4999 и -11xx кроме -1121, -1125)
    if (-4999 <= code <= -4000) or code in range(-1135, -1100):
        return ValidationError(
            code=code,
            message=message,
            http_status=http_status,
            raw_response=raw_response,
        )

    # Unknown error
    return BinanceError(
        code=code,
        message=message,
        category=ErrorCategory.UNKNOWN,
        http_status=http_status,
        raw_response=raw_response,
    )
