# -*- coding: utf-8 -*-
"""
Trade Bot Core Interfaces - Абстрактный интерфейс биржи.

Все адаптеры бирж (Binance, Bybit, etc.) должны реализовать этот интерфейс.
Ядро работает ТОЛЬКО через этот интерфейс - не знает про конкретные биржи.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from decimal import Decimal

from .models import (
    Order,
    OrderSide,
    OrderType,
    OrderStatus,
    Position,
    PositionSide,
    TradeSignal,
)


class ExchangeInterface(ABC):
    """
    Абстрактный интерфейс биржи.

    Все методы async - биржевые операции асинхронны.
    Адаптеры конкретных бирж наследуют этот класс.
    """

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
        """
        Подключиться к бирже.

        Returns:
            True если успешно
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Отключиться от биржи."""
        pass

    @abstractmethod
    async def is_connected(self) -> bool:
        """Проверить подключение."""
        pass

    # === БАЛАНС ===

    @abstractmethod
    async def get_balance(self, asset: str = "USDT") -> Decimal:
        """
        Получить баланс.

        Args:
            asset: Актив (USDT, BTC, etc.)

        Returns:
            Доступный баланс
        """
        pass

    @abstractmethod
    async def get_total_balance(self, asset: str = "USDT") -> Decimal:
        """
        Получить общий баланс (включая в позициях).

        Args:
            asset: Актив

        Returns:
            Общий баланс
        """
        pass

    # === ПОЗИЦИИ ===

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Получить текущую позицию по символу.

        Args:
            symbol: Торговая пара (BTCUSDT)

        Returns:
            Данные позиции или None если нет позиции
        """
        pass

    @abstractmethod
    async def get_all_positions(self) -> List[Dict[str, Any]]:
        """
        Получить все открытые позиции.

        Returns:
            Список позиций
        """
        pass

    # === ОРДЕРА ===

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: Decimal,
        price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
        position_side: PositionSide = PositionSide.BOTH,
        reduce_only: bool = False,
        time_in_force: str = "GTC",
    ) -> Dict[str, Any]:
        """
        Разместить ордер.

        Args:
            symbol: Торговая пара
            side: BUY/SELL
            order_type: MARKET/LIMIT/STOP_MARKET/etc
            quantity: Количество
            price: Цена (для LIMIT)
            stop_price: Стоп-цена (для STOP orders)
            position_side: LONG/SHORT/BOTH
            reduce_only: Только закрытие
            time_in_force: GTC/IOC/FOK

        Returns:
            Ответ биржи с данными ордера
        """
        pass

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """
        Отменить ордер.

        Args:
            symbol: Торговая пара
            order_id: ID ордера на бирже

        Returns:
            True если успешно
        """
        pass

    @abstractmethod
    async def cancel_all_orders(self, symbol: str) -> int:
        """
        Отменить все ордера по символу.

        Args:
            symbol: Торговая пара

        Returns:
            Количество отменённых ордеров
        """
        pass

    @abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Получить статус ордера.

        Args:
            symbol: Торговая пара
            order_id: ID ордера

        Returns:
            Данные ордера или None
        """
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Получить открытые ордера.

        Args:
            symbol: Торговая пара (None = все)

        Returns:
            Список ордеров
        """
        pass

    # === РЫНОЧНЫЕ ДАННЫЕ ===

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Получить текущую цену.

        Args:
            symbol: Торговая пара

        Returns:
            {price, bid, ask, volume, etc.}
        """
        pass

    @abstractmethod
    async def get_mark_price(self, symbol: str) -> Decimal:
        """
        Получить mark price (для фьючерсов).

        Args:
            symbol: Торговая пара

        Returns:
            Mark price
        """
        pass

    # === ИНФОРМАЦИЯ О СИМВОЛЕ ===

    @abstractmethod
    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Получить информацию о символе.

        Args:
            symbol: Торговая пара

        Returns:
            {min_qty, max_qty, step_size, tick_size, etc.}
        """
        pass

    @abstractmethod
    def round_quantity(self, symbol: str, quantity: Decimal) -> Decimal:
        """
        Округлить количество по правилам биржи.

        Args:
            symbol: Торговая пара
            quantity: Исходное количество

        Returns:
            Округлённое количество
        """
        pass

    @abstractmethod
    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        """
        Округлить цену по правилам биржи.

        Args:
            symbol: Торговая пара
            price: Исходная цена

        Returns:
            Округлённая цена
        """
        pass

    # === LEVERAGE ===

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Установить плечо.

        Args:
            symbol: Торговая пара
            leverage: Плечо (1-125)

        Returns:
            True если успешно
        """
        pass

    @abstractmethod
    async def get_leverage(self, symbol: str) -> int:
        """
        Получить текущее плечо.

        Args:
            symbol: Торговая пара

        Returns:
            Текущее плечо
        """
        pass

    # === MARGIN MODE ===

    @abstractmethod
    async def set_margin_type(self, symbol: str, margin_type: str) -> bool:
        """
        Установить тип маржи (ISOLATED/CROSSED).

        Args:
            symbol: Торговая пара
            margin_type: "ISOLATED" или "CROSSED"

        Returns:
            True если успешно
        """
        pass

    # === POSITION MODE ===

    @abstractmethod
    async def set_position_mode(self, hedge_mode: bool) -> bool:
        """
        Установить режим позиций.

        Args:
            hedge_mode: True = Hedge Mode (LONG/SHORT), False = One-way

        Returns:
            True если успешно
        """
        pass

    @abstractmethod
    async def get_position_mode(self) -> bool:
        """
        Получить режим позиций.

        Returns:
            True если Hedge Mode
        """
        pass
