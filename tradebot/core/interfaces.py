# -*- coding: utf-8 -*-
"""
Trade Bot Core Interfaces - Абстрактный интерфейс биржи.

Все адаптеры бирж (Binance, Bybit, etc.) ДОЛЖНЫ реализовать этот интерфейс.
Ядро работает ТОЛЬКО через этот интерфейс.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Callable
from decimal import Decimal

from .models import (
    OrderSide,
    PositionSide,
)
from .exceptions import ExchangeError

# Callback типы для User Data Stream
OrderUpdateCallback = Callable[[Dict[str, Any]], None]
AccountUpdateCallback = Callable[[Dict[str, Any]], None]

# Callback типы для Error Recovery
CriticalErrorCallback = Callable[["ExchangeError"], None]
IPBanCallback = Callable[[int], None]  # (retry_after_seconds)


class ExchangeInterface(ABC):
    """
    Абстрактный интерфейс биржи.

    Ядру ПЛЕВАТЬ на конкретную биржу - оно работает через этот интерфейс.

    Callbacks (опциональные, устанавливаются извне):
        on_critical_error: Вызывается при критической ошибке (Auth, Liquidation)
        on_ip_ban: Вызывается при IP бане с временем ожидания
    """

    # === ERROR CALLBACKS (optional, set by caller) ===
    on_critical_error: Optional[CriticalErrorCallback] = None
    on_ip_ban: Optional[IPBanCallback] = None

    # === ИДЕНТИФИКАЦИЯ ===

    @property
    @abstractmethod
    def name(self) -> str:
        """Название биржи (binance, bybit, okx)."""
        pass

    @property
    @abstractmethod
    def is_testnet(self) -> bool:
        """True если работаем на testnet."""
        pass

    # === ПОДКЛЮЧЕНИЕ ===

    @abstractmethod
    async def connect(self) -> bool:
        """Подключиться к бирже."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Отключиться от биржи."""
        pass

    # === БАЛАНС ===

    @abstractmethod
    async def get_balance(self, asset: str = "USDT") -> Decimal:
        """Получить доступный баланс."""
        pass

    # === ОРДЕРА ===

    @abstractmethod
    async def place_market_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        position_side: PositionSide = PositionSide.BOTH,
        reduce_only: bool = False,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Разместить MARKET ордер.

        Args:
            max_retries: Макс. попыток при сетевых ошибках (default: 3)

        Returns:
            Ответ биржи с данными ордера
        """
        pass

    @abstractmethod
    async def place_stop_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        stop_price: Decimal,
        position_side: PositionSide = PositionSide.BOTH,
        reduce_only: bool = True,
        max_retries: int = 3,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Разместить STOP_MARKET ордер (для SL) через Algo Order API.

        ВАЖНО: Использует /fapi/v1/algoOrder с algoType=CONDITIONAL.
        Возвращает algoId вместо orderId.

        Args:
            stop_price: Цена активации (triggerPrice)
            client_order_id: Клиентский ID (clientAlgoId)
            max_retries: Макс. попыток при сетевых ошибках

        Returns:
            Результат с algoId
        """
        pass

    @abstractmethod
    async def place_take_profit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        stop_price: Decimal,
        position_side: PositionSide = PositionSide.BOTH,
        reduce_only: bool = True,
        max_retries: int = 3,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Разместить TP ордер как LIMIT ордер.

        ВАЖНО: Использует обычный /fapi/v1/order с type=LIMIT.

        Args:
            stop_price: Цена TP (используется как price)
            client_order_id: Клиентский ID (newClientOrderId)
            max_retries: Макс. попыток при сетевых ошибках

        Returns:
            Результат с orderId
        """
        pass

    @abstractmethod
    async def cancel_order(
        self,
        symbol: str,
        order_id: str,
        max_retries: int = 3,
    ) -> bool:
        """
        Отменить обычный ордер (LIMIT, MARKET).

        Args:
            order_id: Binance orderId
            max_retries: Макс. попыток при сетевых ошибках
        """
        pass

    @abstractmethod
    async def cancel_algo_order(
        self,
        symbol: str,
        algo_id: Optional[int] = None,
        client_algo_id: Optional[str] = None,
        max_retries: int = 3,
    ) -> bool:
        """
        Отменить Algo ордер (SL STOP_MARKET, Trailing Stop).

        Args:
            algo_id: Binance algoId
            client_algo_id: Клиентский algoId
            max_retries: Макс. попыток при сетевых ошибках
        """
        pass

    @abstractmethod
    async def cancel_all_orders(self, symbol: str) -> int:
        """Отменить все ордера по символу."""
        pass

    @abstractmethod
    async def get_open_orders(
        self,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Получить все открытые ордера.

        Args:
            symbol: Торговая пара (опционально, если None - все символы)

        Returns:
            Список открытых ордеров с полями:
            - orderId, symbol, side, positionSide
            - type (STOP_MARKET, TAKE_PROFIT_MARKET, etc)
            - origQty, price, stopPrice
            - status, reduceOnly
        """
        pass

    @abstractmethod
    async def get_open_algo_orders(
        self,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Получить все открытые Algo ордера (SL STOP_MARKET, Trailing Stop).

        ВАЖНО: get_open_orders() НЕ возвращает Algo ордера!
        SL и Trailing Stop размещаются через Algo Order API и возвращаются здесь.

        Args:
            symbol: Торговая пара (опционально)

        Returns:
            Список Algo ордеров с полями:
            - algoId, clientAlgoId
            - symbol, side, positionSide
            - orderType (STOP_MARKET, TRAILING_STOP_MARKET)
            - quantity, triggerPrice
            - algoStatus (NEW, etc)
        """
        pass

    # === ПОЗИЦИИ ===

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Получить текущую позицию по символу."""
        pass

    @abstractmethod
    async def get_all_positions(self) -> List[Dict[str, Any]]:
        """Получить все открытые позиции."""
        pass

    @abstractmethod
    async def get_position_by_side(
        self, symbol: str, position_side: PositionSide
    ) -> Optional[Dict[str, Any]]:
        """
        Получить позицию по символу И стороне (для Hedge Mode).

        В Hedge Mode на один символ может быть 2 позиции:
        - positionSide=LONG
        - positionSide=SHORT

        Args:
            symbol: Торговая пара
            position_side: LONG или SHORT

        Returns:
            Позиция если есть, None если нет
        """
        pass

    # === РЫНОЧНЫЕ ДАННЫЕ ===

    @abstractmethod
    async def get_price(self, symbol: str) -> Decimal:
        """Получить текущую цену."""
        pass

    # === ИНФОРМАЦИЯ О СИМВОЛЕ ===

    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Получить информацию о символе (tick_size, step_size, etc.)."""
        pass

    @abstractmethod
    def round_quantity(self, symbol: str, quantity: Decimal) -> Decimal:
        """Округлить количество по правилам биржи."""
        pass

    @abstractmethod
    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        """Округлить цену по правилам биржи."""
        pass

    # === LEVERAGE ===

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Установить плечо."""
        pass

    # === USER DATA STREAM (WebSocket) ===

    @abstractmethod
    async def start_user_data_stream(
        self,
        on_order_update: Optional[OrderUpdateCallback] = None,
        on_account_update: Optional[AccountUpdateCallback] = None,
    ) -> bool:
        """
        Запустить User Data Stream WebSocket.

        Слушает события ордеров и позиций в реальном времени.

        Args:
            on_order_update: Callback для ORDER_TRADE_UPDATE событий
            on_account_update: Callback для ACCOUNT_UPDATE событий

        Returns:
            True если успешно запущен
        """
        pass

    @abstractmethod
    async def stop_user_data_stream(self) -> None:
        """Остановить User Data Stream WebSocket."""
        pass
