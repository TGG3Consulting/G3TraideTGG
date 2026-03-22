# -*- coding: utf-8 -*-
"""
Binance Futures Adapter - Реализация ExchangeInterface для Binance USDT-M Futures.

Для LIVE торговли на Binance Futures.

REST Endpoints:
- Mainnet: https://fapi.binance.com
- Testnet: https://testnet.binancefuture.com

WebSocket Endpoints:
- Mainnet: wss://fstream.binance.com/ws/{listenKey}
- Testnet: wss://fstream.binancefuture.com/ws/{listenKey}

API Documentation:
- https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
"""

import asyncio
import hmac
import hashlib
import json
import logging
import time
from decimal import Decimal, ROUND_DOWN
from typing import Optional, Dict, Any, List, Callable
from urllib.parse import urlencode

import aiohttp

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:
    websockets = None
    ConnectionClosed = Exception

from ..core.interfaces import (
    ExchangeInterface,
    OrderUpdateCallback,
    AccountUpdateCallback,
    CriticalErrorCallback,
    IPBanCallback,
)
from ..core.models import OrderSide, PositionSide
from ..core.exceptions import (
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
    parse_binance_error,
)

logger = logging.getLogger(__name__)

# Retry конфигурация
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0  # секунды
MAX_RETRY_DELAY = 60.0  # максимальная задержка


class BinanceFuturesAdapter(ExchangeInterface):
    """
    Адаптер Binance Futures USDT-M для TradeEngine.

    Реализует ExchangeInterface для размещения ордеров, управления позициями.

    Usage:
        adapter = BinanceFuturesAdapter(
            api_key="...",
            api_secret="...",
            testnet=True,  # Для тестирования
        )
        await adapter.connect()

        # Разместить ордер
        result = await adapter.place_market_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.001"),
            position_side=PositionSide.LONG,
        )
    """

    # REST URLs
    MAINNET_URL = "https://fapi.binance.com"
    TESTNET_URL = "https://testnet.binancefuture.com"

    # WebSocket URLs для User Data Stream
    MAINNET_WS_URL = "wss://fstream.binance.com/ws"
    TESTNET_WS_URL = "wss://fstream.binancefuture.com/ws"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
    ):
        """
        Инициализация адаптера.

        Args:
            api_key: API ключ Binance
            api_secret: API секрет Binance
            testnet: True для testnet, False для mainnet
        """
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._base_url = self.TESTNET_URL if testnet else self.MAINNET_URL
        self._ws_base_url = self.TESTNET_WS_URL if testnet else self.MAINNET_WS_URL

        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # Symbol info cache
        self._symbol_info: Dict[str, Dict[str, Any]] = {}
        self._connected = False

        # Error Recovery State
        self._ip_banned = False
        self._ip_ban_until: float = 0  # timestamp когда бан истекает
        self._ip_ban_retry_count = 0   # счётчик попыток после бана
        self._critical_error: Optional[BinanceError] = None  # критическая ошибка

        # Callbacks для критических ошибок (inherited from ExchangeInterface)
        self.on_critical_error: Optional[CriticalErrorCallback] = None
        self.on_ip_ban: Optional[IPBanCallback] = None

        # User Data Stream
        self._listen_key: Optional[str] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._order_update_callback: Optional[OrderUpdateCallback] = None
        self._account_update_callback: Optional[AccountUpdateCallback] = None
        self._ws_running = False

        # FIX #8: Callback для уведомления о reconnect WebSocket
        # Используется для запуска REST sync после переподключения
        # чтобы восстановить пропущенные события
        self.on_ws_reconnected: Optional[Callable[[], Any]] = None

        # Server time offset (local_time - server_time in ms)
        # Used to correct timestamp in signed requests
        self._time_offset: int = 0
        self._time_sync_task: Optional[asyncio.Task] = None
        self._time_sync_interval: int = 600  # 10 минут

    def _create_task_with_handler(self, coro, name: str = "") -> asyncio.Task:
        """
        Create background task with exception handling.

        FIX: asyncio.create_task exceptions are now logged instead of lost.
        """
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(self._handle_task_exception)
        return task

    def _handle_task_exception(self, task: asyncio.Task) -> None:
        """Handle exceptions from background tasks."""
        try:
            exc = task.exception()
            if exc:
                logger.error(
                    f"Background task '{task.get_name()}' failed with exception: {exc}"
                )
        except asyncio.CancelledError:
            pass  # Task was cancelled, not an error

    # =========================================================================
    # ИДЕНТИФИКАЦИЯ
    # =========================================================================

    @property
    def name(self) -> str:
        """Название биржи."""
        return "binance"

    @property
    def is_testnet(self) -> bool:
        """True если работаем на testnet."""
        return self._testnet

    @property
    def is_connected(self) -> bool:
        """True если подключены к бирже и exchange_info загружен."""
        return self._connected and bool(self._symbol_info)

    @property
    def is_exchange_info_loaded(self) -> bool:
        """True если exchange_info загружен."""
        return bool(self._symbol_info)

    # =========================================================================
    # ПОДКЛЮЧЕНИЕ
    # =========================================================================

    async def connect(self) -> bool:
        """Подключиться к бирже."""
        try:
            # Создаём HTTP сессию
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=30)
                self._session = aiohttp.ClientSession(timeout=timeout)

            # Синхронизируем время с сервером (ВАЖНО: до любых signed запросов!)
            await self._sync_server_time()

            # Загружаем exchange info
            await self._load_exchange_info()

            # Убеждаемся что Hedge Mode включён
            hedge_ok = await self.ensure_hedge_mode()
            if not hedge_ok:
                logger.error("Failed to enable Hedge Mode - trading may fail!")

            # Запускаем периодическую синхронизацию времени (каждые 10 мин)
            if self._time_sync_task is None or self._time_sync_task.done():
                self._time_sync_task = self._create_task_with_handler(
                    self._periodic_time_sync(), name="time_sync"
                )

            self._connected = True
            logger.info(
                f"Connected to Binance Futures "
                f"({'TESTNET' if self._testnet else 'MAINNET'})"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Binance: {e}")
            return False

    async def disconnect(self) -> None:
        """Отключиться от биржи."""
        # Останавливаем периодическую синхронизацию времени
        if self._time_sync_task and not self._time_sync_task.done():
            self._time_sync_task.cancel()
            try:
                await self._time_sync_task
            except asyncio.CancelledError:
                pass
            self._time_sync_task = None

        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._connected = False
        logger.info("Disconnected from Binance Futures")

    async def _load_exchange_info(self) -> None:
        """Загрузить информацию о символах."""
        url = f"{self._base_url}/fapi/v1/exchangeInfo"
        async with self._session.get(url) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Exchange info failed: {resp.status} {text}")
            data = await resp.json()

        for s in data.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("contractType") != "PERPETUAL":
                continue

            symbol = s["symbol"]
            tick_size = Decimal("0.00000001")
            step_size = Decimal("0.00000001")
            min_qty = Decimal("0")
            min_notional = Decimal("5")

            for f in s.get("filters", []):
                if f["filterType"] == "PRICE_FILTER":
                    tick_size = Decimal(f["tickSize"])
                elif f["filterType"] == "LOT_SIZE":
                    step_size = Decimal(f["stepSize"])
                    min_qty = Decimal(f["minQty"])
                elif f["filterType"] == "MIN_NOTIONAL":
                    min_notional = Decimal(f.get("notional", "5"))

            self._symbol_info[symbol] = {
                "tick_size": tick_size,
                "step_size": step_size,
                "min_qty": min_qty,
                "min_notional": min_notional,
                "price_precision": s.get("pricePrecision", 8),
                "qty_precision": s.get("quantityPrecision", 8),
            }

        logger.info(f"Loaded {len(self._symbol_info)} symbols")

    async def _sync_server_time(self) -> None:
        """
        Синхронизировать локальное время с сервером Binance.

        Вычисляет offset между локальным временем и сервером.
        Это необходимо для подписанных запросов, которые требуют
        timestamp в пределах 1000ms от серверного времени.
        """
        url = f"{self._base_url}/fapi/v1/time"
        try:
            local_before = int(time.time() * 1000)
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    server_time = data.get("serverTime", 0)
                    local_after = int(time.time() * 1000)

                    # Берём среднее локальное время (учитываем latency)
                    local_avg = (local_before + local_after) // 2

                    # Offset: на сколько мы впереди/позади сервера
                    self._time_offset = server_time - local_avg

                    logger.info(
                        f"Time sync: server={server_time}, local={local_avg}, "
                        f"offset={self._time_offset}ms"
                    )
                else:
                    logger.warning(f"Failed to sync time: HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"Failed to sync server time: {e}")
            # Продолжаем без коррекции - возможно локальное время точное

    async def _periodic_time_sync(self) -> None:
        """Фоновая задача для периодической синхронизации времени (каждые 10 мин)."""
        while True:
            try:
                await asyncio.sleep(self._time_sync_interval)
                await self._sync_server_time()
            except asyncio.CancelledError:
                logger.debug("Periodic time sync task cancelled")
                break
            except Exception as e:
                logger.warning(f"Periodic time sync error: {e}")

    async def ensure_hedge_mode(self) -> bool:
        """
        Убедиться что аккаунт в Hedge Mode (dual position side).

        Hedge Mode позволяет одновременно держать LONG и SHORT позиции.
        Требуется для корректной работы с positionSide=LONG/SHORT.

        Returns:
            True если Hedge Mode активен или успешно включён
        """
        # 1. Проверяем текущий режим
        endpoint = "/fapi/v1/positionSide/dual"
        try:
            data = await self._signed_request("GET", endpoint)
            is_hedge = data.get("dualSidePosition", False)

            if is_hedge:
                logger.info("Position mode: Hedge Mode (OK)")
                return True

            # 2. Переключаем на Hedge Mode
            logger.info("Switching to Hedge Mode...")
            await self._signed_request("POST", endpoint, {"dualSidePosition": "true"})
            logger.info("Position mode: Switched to Hedge Mode")
            return True

        except Exception as e:
            error_msg = str(e)
            # -4059 = No need to change position side (уже в нужном режиме)
            if "-4059" in error_msg:
                logger.info("Position mode: Hedge Mode (already set)")
                return True
            # -4068 = Position side cannot be changed if open positions exist
            if "-4068" in error_msg:
                logger.error(
                    "Cannot switch to Hedge Mode: open positions exist! "
                    "Close all positions on Binance and restart."
                )
                return False
            logger.error(f"Failed to set Hedge Mode: {e}")
            return False

    # =========================================================================
    # БАЛАНС
    # =========================================================================

    async def get_balance(self, asset: str = "USDT") -> Decimal:
        """Получить доступный баланс."""
        endpoint = "/fapi/v2/balance"
        data = await self._signed_request("GET", endpoint)

        for item in data:
            if item.get("asset") == asset:
                return Decimal(str(item.get("availableBalance", "0")))

        return Decimal("0")

    # =========================================================================
    # ОРДЕРА
    # =========================================================================

    async def place_market_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        position_side: PositionSide = PositionSide.BOTH,
        reduce_only: bool = False,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> Dict[str, Any]:
        """
        Разместить MARKET ордер.

        Args:
            symbol: Торговая пара
            side: BUY/SELL
            quantity: Количество
            position_side: LONG/SHORT/BOTH
            reduce_only: Только уменьшение позиции
            max_retries: Максимум попыток при сетевых ошибках

        Returns:
            Результат ордера

        Raises:
            BinanceError: При ошибке API
        """
        params = {
            "symbol": symbol,
            "side": side.value,
            "type": "MARKET",
            "quantity": str(quantity),
            "positionSide": position_side.value,
            "newOrderRespType": "RESULT",  # Get executedQty & avgPrice in response
        }

        # ВАЖНО: reduceOnly НЕЛЬЗЯ использовать в Hedge Mode (positionSide=LONG/SHORT)
        # В Hedge Mode само направление ордера определяет reduce-only:
        # - positionSide=LONG + side=SELL = закрытие LONG (implicit reduce-only)
        # - positionSide=SHORT + side=BUY = закрытие SHORT (implicit reduce-only)
        # Ошибка API: "reduceOnly cannot be sent in Hedge Mode"
        if reduce_only and position_side == PositionSide.BOTH:
            params["reduceOnly"] = "true"

        logger.info(f"Placing MARKET order: {params}")

        result = await self._request_with_retry(
            "POST", "/fapi/v1/order", params, max_retries=max_retries
        )
        logger.info(f"Order result: {result}")
        return result

    async def place_stop_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        stop_price: Decimal,
        position_side: PositionSide = PositionSide.BOTH,
        reduce_only: bool = True,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Разместить STOP_MARKET ордер (для SL) через Algo Order API.

        ВАЖНО: С декабря 2025 Binance требует использовать /fapi/v1/algoOrder
        для STOP_MARKET ордеров. Старый /fapi/v1/order больше не работает.

        Args:
            symbol: Торговая пара
            side: BUY/SELL
            quantity: Количество
            stop_price: Цена активации (triggerPrice)
            position_side: LONG/SHORT/BOTH
            reduce_only: Только уменьшение позиции
            max_retries: Максимум попыток
            client_order_id: Клиентский ID ордера (clientAlgoId)

        Returns:
            Результат ордера с algoId

        Raises:
            BinanceError: При ошибке API
        """
        params = {
            "symbol": symbol,
            "side": side.value,
            "positionSide": position_side.value,
            "algoType": "CONDITIONAL",
            "type": "STOP_MARKET",
            "quantity": str(quantity),
            "triggerPrice": str(stop_price),
            "workingType": "CONTRACT_PRICE",
        }

        if client_order_id:
            params["clientAlgoId"] = client_order_id

        # ВАЖНО: reduceOnly НЕЛЬЗЯ использовать в Hedge Mode (positionSide=LONG/SHORT)
        # В Hedge Mode само направление ордера определяет reduce-only:
        # - positionSide=LONG + side=SELL = закрытие LONG (implicit reduce-only)
        # - positionSide=SHORT + side=BUY = закрытие SHORT (implicit reduce-only)
        # Ошибка API: "reduceOnly cannot be sent in Hedge Mode"
        if reduce_only and position_side == PositionSide.BOTH:
            params["reduceOnly"] = "true"

        logger.info(f"Placing STOP_MARKET via Algo Order API: {params}")

        result = await self._request_with_retry(
            "POST", "/fapi/v1/algoOrder", params, max_retries=max_retries
        )

        algo_id = result.get("algoId")
        logger.info(f"SL Algo order placed: algoId={algo_id}, status={result.get('algoStatus')}")
        return result

    async def place_take_profit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        stop_price: Decimal,
        position_side: PositionSide = PositionSide.BOTH,
        reduce_only: bool = True,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Разместить TP ордер как LIMIT ордер.

        ВАЖНО: TP размещается как обычный LIMIT ордер (не TAKE_PROFIT_MARKET).
        Это стандартная практика для Binance Futures.

        Args:
            symbol: Торговая пара
            side: BUY/SELL
            quantity: Количество
            stop_price: Цена TP (используется как price для LIMIT)
            position_side: LONG/SHORT/BOTH
            reduce_only: Только уменьшение позиции (игнорируется в Hedge Mode)
            max_retries: Максимум попыток
            client_order_id: Клиентский ID ордера

        Returns:
            Результат ордера

        Raises:
            BinanceError: При ошибке API
        """
        params = {
            "symbol": symbol,
            "side": side.value,
            "type": "LIMIT",
            "quantity": str(quantity),
            "price": str(stop_price),
            "positionSide": position_side.value,
            "timeInForce": "GTC",
        }

        if client_order_id:
            params["newClientOrderId"] = client_order_id

        # reduceOnly нельзя использовать в Hedge Mode (Binance ограничение)
        # В Hedge Mode, ордер на закрытие (SELL+LONG или BUY+SHORT) автоматически reduce-only

        logger.info(f"Placing TP LIMIT order: {params}")

        result = await self._request_with_retry(
            "POST", "/fapi/v1/order", params, max_retries=max_retries
        )

        order_id = result.get("orderId")
        logger.info(f"TP LIMIT order placed: orderId={order_id}, status={result.get('status')}")
        return result

    async def cancel_algo_order(
        self,
        symbol: str,
        algo_id: Optional[int] = None,
        client_algo_id: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> bool:
        """
        Отменить Algo ордер (SL).

        Args:
            symbol: Торговая пара
            algo_id: Binance Algo ID
            client_algo_id: Клиентский Algo ID
            max_retries: Максимум попыток

        Returns:
            True если успешно отменён

        Note:
            Нужен либо algo_id, либо client_algo_id.
        """
        if not algo_id and not client_algo_id:
            raise ValueError("Must provide algo_id or client_algo_id")

        params = {"symbol": symbol}

        if algo_id:
            params["algoId"] = algo_id
        if client_algo_id:
            params["clientAlgoId"] = client_algo_id

        logger.info(f"Canceling Algo order: {params}")

        try:
            result = await self._request_with_retry(
                "DELETE", "/fapi/v1/algoOrder", params, max_retries=max_retries
            )
            logger.info(f"Algo order canceled: {result}")
            return True

        except BinanceError as e:
            # Algo order not found or already canceled
            if e.code in (-2013, -2011):
                logger.warning(f"Algo order not found (already canceled/triggered): {e.message}")
                return True
            logger.error(f"Failed to cancel Algo order: [{e.code}] {e.message}")
            return False

    async def place_trailing_stop_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        callback_rate: float,
        activation_price: Optional[Decimal] = None,
        position_side: PositionSide = PositionSide.BOTH,
        reduce_only: bool = True,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Разместить TRAILING_STOP_MARKET ордер через Algo Order API.

        Binance Futures Algo API:
        - callbackRate: 0.1 - 10.0 (процент отката, 1.0 = 1%)
        - activatePrice: цена активации (опционально)

        Args:
            symbol: Торговая пара
            side: BUY/SELL (противоположная стороне позиции)
            quantity: Количество
            callback_rate: Процент отката (0.1 - 10.0)
            activation_price: Цена активации trailing stop (None = сразу)
            position_side: LONG/SHORT/BOTH
            reduce_only: Только уменьшение позиции
            max_retries: Максимум попыток
            client_order_id: Клиентский ID ордера (clientAlgoId)

        Returns:
            Результат ордера с algoId

        Raises:
            BinanceError: При ошибке API
            ValueError: Если callback_rate вне диапазона 0.1-10.0
        """
        # Валидация callback_rate (Algo API допускает 0.1-10.0)
        if callback_rate < 0.1 or callback_rate > 10.0:
            raise ValueError(
                f"callbackRate must be between 0.1 and 10.0, got {callback_rate}"
            )

        params = {
            "symbol": symbol,
            "side": side.value,
            "positionSide": position_side.value,
            "algoType": "CONDITIONAL",
            "type": "TRAILING_STOP_MARKET",
            "quantity": str(quantity),
            "callbackRate": str(callback_rate),
            "workingType": "CONTRACT_PRICE",
        }

        if activation_price is not None:
            params["activatePrice"] = str(activation_price)

        if client_order_id:
            params["clientAlgoId"] = client_order_id

        # ВАЖНО: reduceOnly НЕЛЬЗЯ использовать в Hedge Mode (positionSide=LONG/SHORT)
        # В Hedge Mode само направление ордера определяет reduce-only
        if reduce_only and position_side == PositionSide.BOTH:
            params["reduceOnly"] = "true"

        logger.info(f"Placing TRAILING_STOP_MARKET via Algo Order API: {params}")

        result = await self._request_with_retry(
            "POST", "/fapi/v1/algoOrder", params, max_retries=max_retries
        )

        algo_id = result.get("algoId")
        logger.info(f"Trailing stop Algo order placed: algoId={algo_id}")
        return result

    async def cancel_order(
        self,
        symbol: str,
        order_id: str,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> bool:
        """
        Отменить ордер.

        Args:
            symbol: Торговая пара
            order_id: ID ордера
            max_retries: Максимум попыток

        Returns:
            True если успешно

        Note:
            Не выбрасывает исключение если ордер уже отменён или не существует.
        """
        params = {
            "symbol": symbol,
            "orderId": order_id,
        }

        try:
            await self._request_with_retry(
                "DELETE", "/fapi/v1/order", params, max_retries=max_retries
            )
            logger.info(f"Cancelled order {order_id} for {symbol}")
            return True

        except CancelFailedError as e:
            # Ордер уже отменён или не существует - это OK
            logger.warning(f"Cancel order {order_id}: {e.message}")
            return True

        except BinanceError as e:
            logger.error(f"Failed to cancel order {order_id}: [{e.code}] {e.message}")
            return False

    async def cancel_all_orders(self, symbol: str) -> int:
        """Отменить все ордера по символу."""
        params = {"symbol": symbol}

        try:
            result = await self._signed_request(
                "DELETE", "/fapi/v1/allOpenOrders", params
            )
            count = result.get("code", 0) if isinstance(result, dict) else 0
            logger.info(f"Cancelled all orders for {symbol}")
            return count
        except Exception as e:
            logger.error(f"Failed to cancel all orders for {symbol}: {e}")
            return 0

    async def get_open_orders(
        self,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Получить все открытые ордера.

        GET /fapi/v1/openOrders

        Args:
            symbol: Торговая пара (опционально, если None - все символы)

        Returns:
            Список открытых ордеров с полями:
            - orderId, symbol, side, positionSide
            - type (STOP_MARKET, TAKE_PROFIT_MARKET, etc)
            - origQty, price, stopPrice
            - status, time, updateTime
        """
        endpoint = "/fapi/v1/openOrders"
        params = {}
        if symbol:
            params["symbol"] = symbol

        try:
            data = await self._request_with_retry("GET", endpoint, params)
            logger.info(f"Got {len(data)} open orders" + (f" for {symbol}" if symbol else ""))
            return data
        except BinanceError as e:
            logger.error(f"Failed to get open orders: [{e.code}] {e.message}")
            return []

    async def get_open_algo_orders(
        self,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Получить все открытые Algo ордера (SL STOP_MARKET, Trailing Stop).

        GET /fapi/v1/openAlgoOrders

        ВАЖНО: Обычный get_open_orders() НЕ возвращает Algo ордера!
        Algo ордера (созданные через /fapi/v1/algoOrder) нужно получать здесь.

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
        endpoint = "/fapi/v1/openAlgoOrders"
        params = {}
        if symbol:
            params["symbol"] = symbol

        try:
            data = await self._request_with_retry("GET", endpoint, params)
            logger.info(f"Got {len(data)} open Algo orders" + (f" for {symbol}" if symbol else ""))
            return data
        except BinanceError as e:
            logger.error(f"Failed to get open Algo orders: [{e.code}] {e.message}")
            return []

    async def get_order_details(
        self,
        symbol: str,
        order_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Получить детали ордера по ID.

        GET /fapi/v1/order

        Используется для получения информации об исполненных ордерах (TP).

        Args:
            symbol: Торговая пара
            order_id: ID ордера

        Returns:
            Детали ордера или None если не найден:
            - orderId, symbol, side, positionSide
            - type, status (FILLED, CANCELED, etc)
            - origQty, executedQty, avgPrice
            - time, updateTime
        """
        endpoint = "/fapi/v1/order"
        params = {
            "symbol": symbol,
            "orderId": order_id,
        }

        try:
            data = await self._signed_request("GET", endpoint, params)
            return data
        except BinanceError as e:
            if e.code == -2013:  # Order does not exist
                logger.debug(f"Order {order_id} not found for {symbol}")
            else:
                logger.warning(f"Failed to get order {order_id}: [{e.code}] {e.message}")
            return None

    async def get_algo_order_details(
        self,
        symbol: str,
        algo_id: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Получить детали Algo ордера по algoId.

        GET /fapi/v1/algoOrder

        Используется для получения информации об исполненных Algo ордерах (SL, Trailing).

        Args:
            symbol: Торговая пара
            algo_id: Algo ID ордера

        Returns:
            Детали ордера или None если не найден:
            - algoId, clientAlgoId
            - symbol, side, positionSide
            - orderType, algoStatus (FILLED, CANCELLED, etc)
            - quantity, executedQty, avgPrice, triggerPrice
        """
        endpoint = "/fapi/v1/algoOrder"
        params = {
            "symbol": symbol,
            "algoId": algo_id,
        }

        try:
            data = await self._signed_request("GET", endpoint, params)
            return data
        except BinanceError as e:
            if e.code == -2013:  # Order does not exist
                logger.debug(f"Algo order {algo_id} not found for {symbol}")
            else:
                logger.warning(f"Failed to get Algo order {algo_id}: [{e.code}] {e.message}")
            return None

    # =========================================================================
    # ПОЗИЦИИ
    # =========================================================================

    async def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Получить текущую позицию по символу."""
        endpoint = "/fapi/v2/positionRisk"
        params = {"symbol": symbol}
        data = await self._signed_request("GET", endpoint, params)

        for pos in data:
            if pos.get("symbol") == symbol:
                amt = Decimal(str(pos.get("positionAmt", "0")))
                if amt != 0:
                    return pos

        return None

    async def get_position_by_side(
        self, symbol: str, position_side: PositionSide
    ) -> Optional[Dict[str, Any]]:
        """
        Получить позицию по символу И стороне (для Hedge Mode).

        В Hedge Mode на один символ может быть 2 записи:
        - positionSide=LONG (positionAmt > 0)
        - positionSide=SHORT (positionAmt < 0)

        Args:
            symbol: Торговая пара
            position_side: LONG или SHORT

        Returns:
            Позиция если есть, None если нет
        """
        endpoint = "/fapi/v2/positionRisk"
        params = {"symbol": symbol}
        data = await self._signed_request("GET", endpoint, params)

        logger.debug(f"get_position_by_side({symbol}, {position_side.value}): API returned {len(data)} entries")

        for pos in data:
            if pos.get("symbol") != symbol:
                continue

            pos_side = pos.get("positionSide", "BOTH")
            amt = Decimal(str(pos.get("positionAmt", "0")))

            logger.debug(
                f"  Checking: positionSide={pos_side}, positionAmt={amt}, "
                f"looking_for={position_side.value}, match={(pos_side == position_side.value and amt != 0)}"
            )

            # В Hedge Mode: проверяем positionSide и что есть позиция
            if pos_side == position_side.value and amt != 0:
                logger.info(f"FOUND existing position: {symbol} {pos_side} qty={amt}")
                return pos

        logger.debug(f"No position found for {symbol} {position_side.value}")
        return None

    async def get_all_positions(self) -> List[Dict[str, Any]]:
        """Получить все открытые позиции."""
        endpoint = "/fapi/v2/positionRisk"
        data = await self._signed_request("GET", endpoint)

        positions = []
        for pos in data:
            amt = Decimal(str(pos.get("positionAmt", "0")))
            if amt != 0:
                positions.append(pos)

        return positions

    # =========================================================================
    # РЫНОЧНЫЕ ДАННЫЕ
    # =========================================================================

    async def get_price(self, symbol: str) -> Decimal:
        """Получить текущую цену."""
        url = f"{self._base_url}/fapi/v1/ticker/price"
        params = {"symbol": symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Price request failed: {resp.status} {text}")
            data = await resp.json()

        return Decimal(str(data.get("price", "0")))

    # =========================================================================
    # ИНФОРМАЦИЯ О СИМВОЛЕ
    # =========================================================================

    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Получить информацию о символе."""
        if symbol not in self._symbol_info:
            await self._load_exchange_info()

        return self._symbol_info.get(symbol, {})

    def round_quantity(self, symbol: str, quantity: Decimal) -> Decimal:
        """
        Округлить количество по правилам биржи.

        ВАЖНО: Требует предварительного вызова connect() для загрузки exchange_info.
        """
        info = self._symbol_info.get(symbol)
        if not info:
            # FIX: Логируем предупреждение если exchange_info не загружен
            logger.warning(
                f"round_quantity({symbol}): symbol_info not loaded, returning unrounded value. "
                f"Make sure connect() was called before trading."
            )
            return quantity

        step_size = info["step_size"]
        # Округляем вниз до step_size
        return (quantity // step_size) * step_size

    def get_step_size(self, symbol: str) -> Decimal:
        """
        Получить step_size для символа (минимальный шаг количества).

        ВАЖНО: Требует предварительного вызова connect() для загрузки exchange_info.
        """
        info = self._symbol_info.get(symbol)
        if not info:
            # FIX: Логируем предупреждение если exchange_info не загружен
            logger.warning(
                f"get_step_size({symbol}): symbol_info not loaded, using fallback 0.001. "
                f"Make sure connect() was called before trading."
            )
            return Decimal("0.001")  # Default fallback
        return info["step_size"]

    def get_tick_size(self, symbol: str) -> Decimal:
        """
        Получить tick_size для символа (минимальный шаг цены).

        ВАЖНО: Требует предварительного вызова connect() для загрузки exchange_info.
        """
        info = self._symbol_info.get(symbol)
        if not info:
            # FIX: Логируем предупреждение если exchange_info не загружен
            logger.warning(
                f"get_tick_size({symbol}): symbol_info not loaded, using fallback 0.01. "
                f"Make sure connect() was called before trading."
            )
            return Decimal("0.01")  # Default fallback
        return info["tick_size"]

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        """
        Округлить цену по правилам биржи (tick_size).

        Используем Decimal.quantize() для точного округления без floating point ошибок.
        ROUND_DOWN гарантирует что SL/TP цены будут в допустимом диапазоне.

        ВАЖНО: Требует предварительного вызова connect() для загрузки exchange_info.
        """
        info = self._symbol_info.get(symbol)
        if not info:
            # FIX: Логируем предупреждение если exchange_info не загружен
            logger.warning(
                f"round_price({symbol}): symbol_info not loaded, returning unrounded price. "
                f"Make sure connect() was called before trading."
            )
            return price

        tick_size = info["tick_size"]
        if tick_size <= 0:
            return price

        # Определяем количество знаков после запятой из tick_size
        # Например: tick_size=0.01 -> 2 знака, tick_size=0.0001 -> 4 знака
        # quantize требует Decimal с нужным количеством знаков
        return (price / tick_size).quantize(Decimal("1"), rounding=ROUND_DOWN) * tick_size

    # =========================================================================
    # LEVERAGE
    # =========================================================================

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Установить плечо."""
        params = {
            "symbol": symbol,
            "leverage": leverage,
        }

        try:
            await self._signed_request("POST", "/fapi/v1/leverage", params)
            logger.info(f"Set leverage {leverage}x for {symbol}")
            return True
        except Exception as e:
            # Ошибка может быть если плечо уже такое - игнорируем
            logger.warning(f"Set leverage warning: {e}")
            return True

    # =========================================================================
    # PRIVATE API
    # =========================================================================

    def _sign(self, params: Dict[str, Any]) -> str:
        """Подписать запрос."""
        query_string = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    async def _signed_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Выполнить подписанный запрос с автоматическим retry при -1021."""
        original_params = params.copy() if params else {}

        for attempt in range(2):  # Максимум 1 retry после resync
            params = original_params.copy()

            # Добавляем timestamp с коррекцией на server offset
            params["timestamp"] = int(time.time() * 1000) + self._time_offset

            # Подписываем
            params["signature"] = self._sign(params)

            url = f"{self._base_url}{endpoint}"
            headers = {"X-MBX-APIKEY": self._api_key}

            try:
                if method == "GET":
                    async with self._session.get(
                        url, params=params, headers=headers
                    ) as resp:
                        return await self._handle_response(resp)

                elif method == "POST":
                    async with self._session.post(
                        url, params=params, headers=headers
                    ) as resp:
                        return await self._handle_response(resp)

                elif method == "DELETE":
                    async with self._session.delete(
                        url, params=params, headers=headers
                    ) as resp:
                        return await self._handle_response(resp)

                else:
                    raise ValueError(f"Unknown method: {method}")

            except AuthError as e:
                # -1021: Timestamp error - retry после resync (уже выполнен в _handle_response)
                if e.code == -1021 and attempt == 0:
                    logger.info("Retrying request after time resync...")
                    continue
                raise

    async def _handle_response(self, resp: aiohttp.ClientResponse) -> Any:
        """
        Обработать ответ API.

        Raises:
            BinanceError: Соответствующее исключение по типу ошибки
        """
        text = await resp.text()

        if resp.status >= 400:
            # Парсим ошибку через систему исключений
            error = parse_binance_error(resp.status, text)

            # Обрабатываем IP Ban
            if isinstance(error, IPBanError):
                self._handle_ip_ban(error)

            # Обрабатываем ошибку timestamp (-1021) - немедленный ресинк
            if error.code == -1021:
                logger.warning("Timestamp error -1021 detected, resyncing time...")
                await self._sync_server_time()

            # Обрабатываем критические ошибки
            if error.is_critical:
                self._handle_critical_error(error)

            logger.error(f"API error [{error.code}]: {error.message}")
            raise error

        return json.loads(text)

    def _handle_ip_ban(self, error: IPBanError) -> None:
        """Обработать IP бан."""
        # Увеличиваем время бана экспоненциально
        # 2 мин → 4 мин → 8 мин → 16 мин → 32 мин → 1 час → 2 часа → ...
        base_delay = 120  # 2 минуты
        delay = base_delay * (2 ** self._ip_ban_retry_count)
        delay = min(delay, 3 * 24 * 3600)  # Максимум 3 дня

        self._ip_banned = True
        self._ip_ban_until = time.time() + delay
        self._ip_ban_retry_count += 1

        logger.critical(
            f"IP BANNED! Retry #{self._ip_ban_retry_count}, "
            f"waiting {delay} seconds ({delay/60:.1f} min)"
        )

        # Вызываем callback
        if self.on_ip_ban:
            try:
                self.on_ip_ban(delay)
            except Exception as e:
                logger.error(f"IP ban callback error: {e}")

    def _handle_critical_error(self, error: BinanceError) -> None:
        """Обработать критическую ошибку."""
        self._critical_error = error

        logger.critical(f"CRITICAL ERROR: [{error.code}] {error.message}")

        # Вызываем callback
        if self.on_critical_error:
            try:
                self.on_critical_error(error)
            except Exception as e:
                logger.error(f"Critical error callback error: {e}")

    async def _check_ip_ban(self) -> None:
        """Проверить и дождаться окончания IP бана."""
        if not self._ip_banned:
            return

        now = time.time()
        if now < self._ip_ban_until:
            wait_time = self._ip_ban_until - now
            logger.warning(f"IP banned, waiting {wait_time:.0f} seconds...")
            await asyncio.sleep(wait_time)

        # Сбрасываем флаг бана (проверим при следующем запросе)
        self._ip_banned = False

    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        signed: bool = True,
    ) -> Any:
        """
        Выполнить запрос с retry логикой.

        Args:
            method: HTTP метод
            endpoint: API endpoint
            params: Параметры запроса
            max_retries: Максимум попыток
            signed: Нужна ли подпись

        Returns:
            Ответ API

        Raises:
            BinanceError: Если все попытки неудачны
        """
        # Проверяем IP бан
        await self._check_ip_ban()

        # Проверяем критическую ошибку
        if self._critical_error and self._critical_error.is_critical:
            raise self._critical_error

        last_error: Optional[BinanceError] = None
        retry_delay = DEFAULT_RETRY_DELAY

        for attempt in range(max_retries + 1):
            try:
                if signed:
                    return await self._signed_request(method, endpoint, params)
                else:
                    return await self._unsigned_request(method, endpoint, params)

            except (NetworkError, RateLimitError) as e:
                last_error = e

                if attempt < max_retries:
                    # Exponential backoff
                    if isinstance(e, RateLimitError) and e.retry_after:
                        retry_delay = e.retry_after
                    else:
                        retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)

                    logger.warning(
                        f"Retryable error [{e.code}], attempt {attempt + 1}/{max_retries}, "
                        f"retry in {retry_delay}s: {e.message}"
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(
                        f"Max retries exceeded for {endpoint}: [{e.code}] {e.message}"
                    )
                    raise

            except IPBanError as e:
                # При IP бане ждём и пробуем снова
                last_error = e
                await self._check_ip_ban()

                if attempt < max_retries:
                    continue
                else:
                    raise

            except BinanceError:
                # Другие ошибки не retry
                raise

        # Не должны сюда попасть, но на всякий случай
        if last_error:
            raise last_error
        raise Exception("Unknown error in retry loop")

    async def _unsigned_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Выполнить неподписанный запрос."""
        url = f"{self._base_url}{endpoint}"

        if method == "GET":
            async with self._session.get(url, params=params) as resp:
                return await self._handle_response(resp)
        else:
            raise ValueError(f"Unsigned request only supports GET, got {method}")

    # =========================================================================
    # USER DATA STREAM (WebSocket)
    # =========================================================================

    async def create_listen_key(self) -> str:
        """
        Создать listenKey для User Data Stream.

        POST /fapi/v1/listenKey
        Если listenKey уже существует - вернёт существующий и продлит на 60 минут.
        """
        endpoint = "/fapi/v1/listenKey"
        url = f"{self._base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self._api_key}

        async with self._session.post(url, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Create listenKey failed: {resp.status} {text}")
            data = await resp.json()

        self._listen_key = data["listenKey"]
        logger.info(f"Created listenKey: {self._listen_key[:20]}...")
        return self._listen_key

    async def keep_alive_listen_key(self) -> bool:
        """
        Продлить listenKey на 60 минут.

        PUT /fapi/v1/listenKey
        Рекомендуется вызывать каждые 30 минут.
        """
        if not self._listen_key:
            return False

        endpoint = "/fapi/v1/listenKey"
        url = f"{self._base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self._api_key}

        try:
            async with self._session.put(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Keep alive listenKey failed: {resp.status} {text}")
                    return False

            logger.debug("ListenKey kept alive")
            return True

        except Exception as e:
            logger.error(f"Keep alive listenKey error: {e}")
            return False

    async def close_listen_key(self) -> bool:
        """
        Закрыть listenKey.

        DELETE /fapi/v1/listenKey
        """
        if not self._listen_key:
            return True

        endpoint = "/fapi/v1/listenKey"
        url = f"{self._base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self._api_key}

        try:
            async with self._session.delete(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"Close listenKey failed: {resp.status} {text}")

            self._listen_key = None
            logger.info("ListenKey closed")
            return True

        except Exception as e:
            logger.error(f"Close listenKey error: {e}")
            return False

    async def start_user_data_stream(
        self,
        on_order_update: Optional[OrderUpdateCallback] = None,
        on_account_update: Optional[AccountUpdateCallback] = None,
    ) -> bool:
        """
        Запустить User Data Stream WebSocket.

        Args:
            on_order_update: Callback для ORDER_TRADE_UPDATE событий
            on_account_update: Callback для ACCOUNT_UPDATE событий

        Returns:
            True если успешно запущен
        """
        if websockets is None:
            raise ImportError("websockets package required: pip install websockets")

        if self._ws_running:
            logger.warning("User Data Stream already running")
            return True

        self._order_update_callback = on_order_update
        self._account_update_callback = on_account_update

        try:
            # Создаём или получаем существующий listenKey
            await self.create_listen_key()

            # Подключаемся к WebSocket
            ws_url = f"{self._ws_base_url}/{self._listen_key}"
            logger.info(f"Connecting to User Data Stream: {ws_url[:50]}...")

            # FIX #14: Увеличиваем ping timeout для медленных сетей
            # 30s interval, 20s timeout - минимальные разумные значения
            # FIX: Добавляем timeouts для connect чтобы избежать зависания
            self._ws = await websockets.connect(
                ws_url,
                ping_interval=30,
                ping_timeout=20,
                open_timeout=30,   # Timeout для установки соединения
                close_timeout=10,  # Timeout для закрытия соединения
            )

            self._ws_running = True

            # FIX: Используем helper с exception handling
            # Запускаем обработчик сообщений
            self._ws_task = self._create_task_with_handler(
                self._ws_message_loop(), name="ws_message_loop"
            )

            # Запускаем keepalive (каждые 30 минут)
            self._keepalive_task = self._create_task_with_handler(
                self._keepalive_loop(), name="ws_keepalive"
            )

            logger.info("User Data Stream started")
            return True

        except Exception as e:
            logger.error(f"Failed to start User Data Stream: {e}")
            self._ws_running = False
            return False

    async def stop_user_data_stream(self) -> None:
        """Остановить User Data Stream WebSocket."""
        self._ws_running = False

        # Останавливаем keepalive
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass

        # Останавливаем обработчик сообщений
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        # Закрываем WebSocket
        if self._ws:
            await self._ws.close()
            self._ws = None

        # Закрываем listenKey
        await self.close_listen_key()

        logger.info("User Data Stream stopped")

    async def _ws_message_loop(self) -> None:
        """Цикл обработки WebSocket сообщений."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    await self._handle_ws_message(data)
                except json.JSONDecodeError as e:
                    logger.warning(f"WS JSON decode error: {e}")
                except Exception as e:
                    logger.error(f"WS message handling error: {e}")

        except ConnectionClosed as e:
            logger.warning(f"User Data Stream closed: {e.code}")
            if self._ws_running:
                # Пытаемся переподключиться
                await self._reconnect_ws()

        except asyncio.CancelledError:
            logger.debug("WS message loop cancelled")

        except Exception as e:
            logger.error(f"WS message loop error: {e}")
            if self._ws_running:
                await self._reconnect_ws()

    async def _handle_ws_message(self, data: Dict[str, Any]) -> None:
        """Обработать входящее WebSocket сообщение."""
        event_type = data.get("e")

        if event_type == "ORDER_TRADE_UPDATE":
            # Обычный ордер обновлён (entry, TP LIMIT)
            logger.debug(f"ORDER_TRADE_UPDATE: {data}")
            await self._handle_order_trade_update(data)

        elif event_type == "ALGO_UPDATE":
            # Algo ордер обновлён (SL STOP_MARKET, Trailing Stop)
            logger.debug(f"ALGO_UPDATE: {data}")
            await self._handle_algo_update(data)

        elif event_type == "ACCOUNT_UPDATE":
            # Баланс или позиция изменились
            logger.debug(f"ACCOUNT_UPDATE: {data}")
            if self._account_update_callback:
                try:
                    self._account_update_callback(data)
                except Exception as e:
                    logger.error(f"Account update callback error: {e}")

        elif event_type == "listenKeyExpired":
            # ListenKey истёк - нужно переподключиться
            logger.warning("ListenKey expired, reconnecting...")
            await self._reconnect_ws()

        else:
            logger.debug(f"Unknown WS event: {event_type}")

    async def _handle_order_trade_update(self, data: Dict[str, Any]) -> None:
        """
        Обработать ORDER_TRADE_UPDATE событие.

        Содержит данные об обычных ордерах (MARKET, LIMIT).
        Ключевые поля в data["o"]:
        - i: orderId
        - c: clientOrderId
        - s: symbol
        - S: side (BUY/SELL)
        - o: orderType (MARKET/LIMIT)
        - ps: positionSide (LONG/SHORT/BOTH)
        - X: status (NEW/FILLED/CANCELED/EXPIRED)
        - q: quantity
        - z: executedQty (исполненное количество)
        - ap: avgPrice (средняя цена исполнения)
        - L: lastFilledPrice
        """
        if not self._order_update_callback:
            return

        order_data = data.get("o", {})

        # Парсим ключевые поля
        order_info = {
            "orderId": order_data.get("i"),
            "clientOrderId": order_data.get("c", ""),
            "symbol": order_data.get("s", ""),
            "side": order_data.get("S", ""),
            "type": order_data.get("o", ""),
            "positionSide": order_data.get("ps", "BOTH"),
            "status": order_data.get("X", ""),
            "origQty": order_data.get("q", "0"),
            "executedQty": order_data.get("z", "0"),
            "avgPrice": order_data.get("ap", "0"),
            "lastFilledPrice": order_data.get("L", "0"),
            "realizedPnl": order_data.get("rp", "0"),  # Realized profit
            "eventType": "ORDER_TRADE_UPDATE",
        }

        logger.info(
            f"Order update: {order_info['symbol']} {order_info['side']} "
            f"status={order_info['status']} executed={order_info['executedQty']}/{order_info['origQty']} "
            f"avgPrice={order_info['avgPrice']}"
        )

        try:
            self._order_update_callback(order_info)
        except Exception as e:
            logger.error(f"Order update callback error: {e}")

    async def _handle_algo_update(self, data: Dict[str, Any]) -> None:
        """
        Обработать ALGO_UPDATE событие.

        Содержит данные об Algo ордерах (SL STOP_MARKET, Trailing Stop).
        Ключевые поля в data["o"]:
        - aid: algoId
        - caid: clientAlgoId
        - s: symbol
        - S: side (BUY/SELL)
        - o: orderType (STOP_MARKET/TRAILING_STOP_MARKET)
        - ps: positionSide (LONG/SHORT/BOTH)
        - X: algoStatus (NEW/TRIGGERING/TRIGGERED/FINISHED/CANCELED/REJECTED/EXPIRED)
        - q: quantity
        - tp: triggerPrice
        - aq: executedQty (исполненное количество)
        - rm: rejectReason (причина отклонения)
        """
        if not self._order_update_callback:
            return

        order_data = data.get("o", {})

        if not isinstance(order_data, dict):
            logger.error(f"ALGO_UPDATE: 'o' is not a dict: {type(order_data)}")
            return

        algo_status = order_data.get("X", "")

        # =====================================================================
        # ALGO_UPDATE статусы (по документации Binance):
        # - NEW: Ордер создан, ещё не сработал
        # - TRIGGERING: Условие сработало, передаётся в matching engine
        # - TRIGGERED: Передан в matching engine (ещё НЕ исполнен!)
        # - FINISHED: Исполнен ИЛИ отменён в matching engine (ФИНАЛЬНЫЙ!)
        # - CANCELED: Отменён вручную
        # - REJECTED: Отклонён matching engine
        # - EXPIRED: Отменён системой
        # =====================================================================

        # Игнорируем промежуточные статусы (ордер ещё не завершён)
        if algo_status in ("NEW", "TRIGGERING", "TRIGGERED"):
            logger.debug(f"Ignoring intermediate algo status: {algo_status}")
            return

        # Определяем финальный статус
        executed_qty = float(order_data.get("aq", 0) or 0)

        if algo_status == "FINISHED":
            # FINISHED = исполнен ИЛИ отменён
            # Проверяем executedQty чтобы понять что произошло
            if executed_qty > 0:
                mapped_status = "FILLED"
            else:
                mapped_status = "CANCELED"
        elif algo_status in ("CANCELED", "REJECTED", "EXPIRED"):
            mapped_status = "CANCELED"
        else:
            # Неизвестный статус - логируем и пропускаем
            logger.warning(f"Unknown algo status: {algo_status}")
            return

        algo_id = order_data.get("aid")
        if algo_id is None:
            logger.error(f"ALGO_UPDATE missing aid field: {order_data}")
            return

        # Парсим информацию
        order_info = {
            "orderId": algo_id,  # Используем algoId как orderId для совместимости
            "algoId": algo_id,
            "clientOrderId": order_data.get("caid", ""),
            "clientAlgoId": order_data.get("caid", ""),
            "symbol": order_data.get("s", ""),
            "side": order_data.get("S", ""),
            "type": order_data.get("o", "STOP_MARKET"),
            "positionSide": order_data.get("ps", "BOTH"),
            "status": mapped_status,
            "algoStatus": algo_status,
            "origQty": order_data.get("q", "0"),
            "executedQty": order_data.get("aq", "0"),
            "avgPrice": order_data.get("ap", "0"),  # Average fill price
            "triggerPrice": order_data.get("tp", "0"),
            "rejectReason": order_data.get("rm", ""),
            "eventType": "ALGO_UPDATE",
        }

        if algo_status == "REJECTED":
            logger.warning(
                f"Algo order REJECTED: algoId={algo_id}, reason={order_info['rejectReason']}"
            )

        # Логируем с деталями исполнения
        if mapped_status == "FILLED":
            logger.info(
                f"Algo FILLED: {order_info['symbol']} {order_info['type']} "
                f"algoId={algo_id} qty={order_info['executedQty']} @ {order_info['avgPrice']}"
            )
        else:
            logger.info(
                f"Algo update: {order_info['symbol']} {order_info['type']} "
                f"algoStatus={algo_status} → {mapped_status}"
            )

        try:
            self._order_update_callback(order_info)
        except Exception as e:
            logger.error(f"Algo update callback error: {e}")

    async def _keepalive_loop(self) -> None:
        """Цикл продления listenKey каждые 30 минут."""
        while self._ws_running:
            try:
                await asyncio.sleep(30 * 60)  # 30 минут
                if self._ws_running:
                    await self.keep_alive_listen_key()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Keepalive error: {e}")

    async def _reconnect_ws(self, max_retries: int = 10) -> None:
        """
        Переподключиться к WebSocket с retry и exponential backoff.

        КРИТИЧНО: Если reconnect не удастся, все WebSocket события потеряны.
        Поэтому используем агрессивную стратегию retry.
        """
        if not self._ws_running:
            return

        # Закрываем текущее соединение
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        # Retry с exponential backoff
        for attempt in range(max_retries):
            if not self._ws_running:
                return

            delay = min(5 * (2 ** attempt), 300)  # 5s, 10s, 20s, ... max 5min
            logger.info(f"Reconnecting User Data Stream (attempt {attempt + 1}/{max_retries}, delay {delay}s)...")

            await asyncio.sleep(delay)

            try:
                # Создаём новый listenKey и подключаемся
                await self.create_listen_key()
                ws_url = f"{self._ws_base_url}/{self._listen_key}"
                # FIX #14: Увеличиваем ping timeout для медленных сетей
                # FIX: Добавляем timeouts для connect чтобы избежать зависания
                self._ws = await websockets.connect(
                    ws_url,
                    ping_interval=30,
                    ping_timeout=20,
                    open_timeout=30,   # Timeout для установки соединения
                    close_timeout=10,  # Timeout для закрытия соединения
                )
                logger.info("User Data Stream reconnected successfully")

                # FIX: Используем helper с exception handling
                # Перезапускаем обработчик сообщений
                self._ws_task = self._create_task_with_handler(
                    self._ws_message_loop(), name="ws_message_loop"
                )

                # FIX #8: Вызываем callback для REST sync после reconnect
                # Во время disconnect могли пропасть события - восстанавливаем состояние
                if self.on_ws_reconnected:
                    try:
                        result = self.on_ws_reconnected()
                        # Если callback вернул coroutine - запускаем его
                        if asyncio.iscoroutine(result):
                            self._create_task_with_handler(result, name="ws_reconnect_callback")
                        logger.info("WebSocket reconnect callback triggered")
                    except Exception as e:
                        logger.error(f"WebSocket reconnect callback error: {e}")

                return  # Успешно переподключились

            except Exception as e:
                logger.error(f"Reconnect attempt {attempt + 1} failed: {e}")

        # Все попытки исчерпаны
        logger.critical(
            f"CRITICAL: Failed to reconnect WebSocket after {max_retries} attempts. "
            f"All position updates will be missed! Manual intervention required."
        )
