# -*- coding: utf-8 -*-
"""
Gate.io exchange connector.

WebSocket: wss://api.gateio.ws/ws/v4/
REST: https://api.gateio.ws/api/v4
Rate limits: 900/min REST, 100 symbols WS
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set

import aiohttp

from src.exchanges.base import (
    BaseExchange,
    ConnectionState,
    ExchangeCapability,
    ExchangeConfig,
    ExchangeType,
    SymbolInfo,
)
from src.exchanges.models import (
    MarketType,
    UnifiedFunding,
    UnifiedOpenInterest,
    UnifiedOrderBook,
    UnifiedTicker,
    UnifiedTrade,
    OrderBookLevel,
    Side,
)
from src.exchanges.rate_limiter import RateLimiter, RateLimitConfig

logger = logging.getLogger(__name__)


class GateConnector(BaseExchange):
    """Gate.io Futures connector."""

    EXCHANGE_NAME = "gate"
    EXCHANGE_TYPE = ExchangeType.CEX
    CAPABILITIES = {
        ExchangeCapability.SPOT,
        ExchangeCapability.FUTURES,
        ExchangeCapability.WEBSOCKET,
        ExchangeCapability.TRADES,
        ExchangeCapability.ORDERBOOK,
        ExchangeCapability.FUNDING_RATE,
        ExchangeCapability.OPEN_INTEREST,
    }

    DEFAULT_WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
    DEFAULT_REST_URL = "https://api.gateio.ws/api/v4"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize Gate.io connector."""
        if config is None:
            config = ExchangeConfig(
                name="gate",
                ws_url=self.DEFAULT_WS_URL,
                rest_url=self.DEFAULT_REST_URL,
            )
        super().__init__(config)

        # Gate.io URLs
        self._ws_url = config.ws_url or self.DEFAULT_WS_URL
        self._rest_url = config.rest_url or self.DEFAULT_REST_URL

        # Rate limiter: 900/min = 15/sec
        self._rate_limiter = RateLimiter(
            RateLimitConfig(
                requests_per_second=15,
                requests_per_minute=900,
            )
        )

        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # WebSocket state
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._subscriptions: Dict[str, Set[str]] = {
            "trades": set(),
            "orderbook": set(),
        }

        # Callbacks
        self._trade_callbacks: List[Callable] = []
        self._orderbook_callbacks: List[Callable] = []

        # Message ID counter
        self._msg_id = 0

    def _next_msg_id(self) -> int:
        """Get next message ID."""
        self._msg_id += 1
        return self._msg_id

    # ============ Symbol Conversion ============

    def to_exchange_symbol(self, unified_symbol: str) -> str:
        """Convert unified symbol to Gate.io format: BTC/USDT -> BTC_USDT"""
        return unified_symbol.replace("/", "_")

    def to_unified_symbol(self, exchange_symbol: str) -> str:
        """Convert Gate.io symbol to unified format: BTC_USDT -> BTC/USDT"""
        return exchange_symbol.replace("_", "/")

    def normalize_symbol(self, raw_symbol: str) -> str:
        """Normalize raw symbol to unified format."""
        return self.to_unified_symbol(raw_symbol)

    async def load_symbols(self) -> List[SymbolInfo]:
        """Load all trading symbols."""
        await self._load_symbols()
        return list(self._symbols.values())

    # ============ Connection Management ============

    async def connect(self) -> None:
        """Connect to Gate.io."""
        if self._state == ConnectionState.CONNECTED:
            return

        self._state = ConnectionState.CONNECTING
        logger.info(f"[{self.EXCHANGE_NAME}] Connecting...")

        try:
            # Create session
            if not self._session:
                self._session = aiohttp.ClientSession()

            # Load symbols
            await self._load_symbols()

            # Connect WebSocket
            self._ws = await self._session.ws_connect(
                self._ws_url,
                heartbeat=30,
            )

            # Start message handler
            self._ws_task = asyncio.create_task(self._ws_handler())
            self._ping_task = asyncio.create_task(self._ping_loop())

            self._state = ConnectionState.CONNECTED
            self._reconnect_count = 0
            logger.info(f"[{self.EXCHANGE_NAME}] Connected successfully")

        except Exception as e:
            self._state = ConnectionState.DISCONNECTED
            logger.error(f"[{self.EXCHANGE_NAME}] Connection failed: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Gate.io."""
        logger.info(f"[{self.EXCHANGE_NAME}] Disconnecting...")

        self._state = ConnectionState.DISCONNECTED

        # Cancel tasks
        for task in [self._ws_task, self._ping_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close WebSocket
        if self._ws and not self._ws.closed:
            await self._ws.close()

        # Close session
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        logger.info(f"[{self.EXCHANGE_NAME}] Disconnected")

    async def _load_symbols(self) -> None:
        """Load available symbols from Gate.io."""
        url = f"{self._rest_url}/futures/usdt/contracts"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to load symbols: {resp.status}")

            data = await resp.json()

            for contract in data:
                name = contract.get("name", "")  # BTC_USDT
                unified = self.to_unified_symbol(name)

                base = contract.get("underlying", "").split("_")[0]
                self._symbols[unified] = SymbolInfo(
                    exchange=self.EXCHANGE_NAME,
                    symbol_unified=unified,
                    symbol_exchange=name,
                    base_asset=base,
                    quote_asset="USDT",
                    market_type=MarketType.FUTURES_PERPETUAL,
                    price_precision=2,
                    quantity_precision=0,
                    min_quantity=Decimal(str(contract.get("order_size_min", 1))),
                    min_notional=Decimal("5"),
                    tick_size=Decimal(str(contract.get("mark_price_round", "0.01"))),
                    step_size=Decimal(str(contract.get("order_size_min", 1))),
                )
                self._symbol_map[name] = unified
                self._reverse_symbol_map[unified] = name

            logger.info(f"[{self.EXCHANGE_NAME}] Loaded {len(self._symbols)} symbols")

    # ============ WebSocket Handler ============

    async def _ws_handler(self) -> None:
        """Handle WebSocket messages."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._process_message(json.loads(msg.data))
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"[{self.EXCHANGE_NAME}] WS error: {msg.data}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.EXCHANGE_NAME}] WS handler error: {e}")

        # Reconnect if not intentionally disconnected
        if self._state != ConnectionState.DISCONNECTED:
            asyncio.create_task(self._reconnect())

    async def _ping_loop(self) -> None:
        """Send periodic pings."""
        try:
            while self._state == ConnectionState.CONNECTED:
                await asyncio.sleep(15)
                if self._ws and not self._ws.closed:
                    ping_msg = {
                        "time": int(time.time()),
                        "channel": "futures.ping",
                    }
                    await self._ws.send_json(ping_msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.EXCHANGE_NAME}] Ping error: {e}")

    async def _process_message(self, data: Dict[str, Any]) -> None:
        """Process WebSocket message."""
        channel = data.get("channel", "")
        event = data.get("event", "")

        # Handle pong
        if channel == "futures.pong":
            return

        # Handle subscription response
        if event == "subscribe":
            logger.debug(f"[{self.EXCHANGE_NAME}] Subscribed: {channel}")
            return

        # Handle trade updates
        if channel == "futures.trades":
            result = data.get("result", [])
            for trade_data in result:
                trade = self.normalize_trade(trade_data)
                for callback in self._trade_callbacks:
                    try:
                        callback(trade)
                    except Exception as e:
                        logger.error(f"Trade callback error: {e}")

        # Handle orderbook updates
        elif channel == "futures.order_book":
            result = data.get("result", {})
            if result:
                orderbook = self.normalize_orderbook(result)
                for callback in self._orderbook_callbacks:
                    try:
                        callback(orderbook)
                    except Exception as e:
                        logger.error(f"Orderbook callback error: {e}")

    async def _reconnect(self) -> None:
        """Reconnect to WebSocket."""
        if self._state == ConnectionState.DISCONNECTED:
            return

        self._state = ConnectionState.RECONNECTING
        self._reconnect_count += 1

        wait_time = min(30, 2 ** self._reconnect_count)
        logger.info(f"[{self.EXCHANGE_NAME}] Reconnecting in {wait_time}s...")
        await asyncio.sleep(wait_time)

        try:
            # Close old connection
            if self._ws and not self._ws.closed:
                await self._ws.close()

            # Reconnect
            self._ws = await self._session.ws_connect(
                self._ws_url,
                heartbeat=30,
            )

            # Restart handler
            self._ws_task = asyncio.create_task(self._ws_handler())
            self._ping_task = asyncio.create_task(self._ping_loop())

            # Resubscribe
            await self._resubscribe()

            self._state = ConnectionState.CONNECTED
            logger.info(f"[{self.EXCHANGE_NAME}] Reconnected successfully")

        except Exception as e:
            logger.error(f"[{self.EXCHANGE_NAME}] Reconnection failed: {e}")
            asyncio.create_task(self._reconnect())

    async def _resubscribe(self) -> None:
        """Resubscribe to all channels."""
        if self._subscriptions["trades"]:
            symbols = list(self._subscriptions["trades"])
            self._subscriptions["trades"].clear()
            await self.subscribe_trades(symbols)

        if self._subscriptions["orderbook"]:
            symbols = list(self._subscriptions["orderbook"])
            self._subscriptions["orderbook"].clear()
            await self.subscribe_orderbook(symbols)

    # ============ Subscriptions ============

    async def subscribe_trades(
        self,
        symbols: List[str],
        callback: Optional[Callable[[UnifiedTrade], None]] = None,
    ) -> None:
        """Subscribe to trade updates."""
        if callback and callback not in self._trade_callbacks:
            self._trade_callbacks.append(callback)

        for symbol in symbols:
            if symbol in self._subscriptions["trades"]:
                continue

            exchange_symbol = self.to_exchange_symbol(symbol)
            msg = {
                "time": int(time.time()),
                "channel": "futures.trades",
                "event": "subscribe",
                "payload": [exchange_symbol],
            }

            await self._ws.send_json(msg)
            self._subscriptions["trades"].add(symbol)
            logger.info(f"[{self.EXCHANGE_NAME}] Subscribed to trades: {symbol}")

    async def subscribe_orderbook(
        self,
        symbols: List[str],
        callback: Optional[Callable[[UnifiedOrderBook], None]] = None,
        depth: int = 20,
    ) -> None:
        """Subscribe to orderbook updates."""
        if callback and callback not in self._orderbook_callbacks:
            self._orderbook_callbacks.append(callback)

        for symbol in symbols:
            if symbol in self._subscriptions["orderbook"]:
                continue

            exchange_symbol = self.to_exchange_symbol(symbol)
            # Gate uses specific depth levels: 5, 10, 20, 50, 100
            depth_level = min([d for d in [5, 10, 20, 50, 100] if d >= depth], default=20)

            msg = {
                "time": int(time.time()),
                "channel": "futures.order_book",
                "event": "subscribe",
                "payload": [exchange_symbol, str(depth_level), "0"],  # "0" = full snapshot
            }

            await self._ws.send_json(msg)
            self._subscriptions["orderbook"].add(symbol)
            logger.info(f"[{self.EXCHANGE_NAME}] Subscribed to orderbook: {symbol}")

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        if not self._ws or self._ws.closed:
            return

        channel_map = {
            "trades": "futures.trades",
            "orderbook": "futures.order_book",
        }
        channel = channel_map.get(stream_type)
        if not channel:
            return

        for symbol in symbols:
            self._subscriptions.get(stream_type, set()).discard(symbol)
            exchange_symbol = self.to_exchange_symbol(symbol)

            msg = {
                "time": int(time.time()),
                "channel": channel,
                "event": "unsubscribe",
                "payload": [exchange_symbol],
            }
            await self._ws.send_json(msg)
            logger.info(f"[{self.EXCHANGE_NAME}] Unsubscribed from {stream_type}: {symbol}")

    # ============ REST API ============

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get ticker for symbol."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/futures/usdt/contracts/{exchange_symbol}"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get ticker: {resp.status}")

            data = await resp.json()

            return UnifiedTicker(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                last_price=Decimal(str(data.get("last_price", 0))),
                bid_price=Decimal(str(data.get("last_price", 0))),  # Approximation
                ask_price=Decimal(str(data.get("last_price", 0))),
                volume_24h=Decimal(str(data.get("volume_24h", 0))),
                high_24h=Decimal(str(data.get("high_24h_price", 0) or 0)),
                low_24h=Decimal(str(data.get("low_24h_price", 0) or 0)),
                change_24h=Decimal(str(data.get("change_24h", 0) or 0)),
            )

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/futures/usdt/order_book"
        params = {"contract": exchange_symbol, "limit": limit}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get orderbook: {resp.status}")

            data = await resp.json()
            return self.normalize_orderbook(data, symbol)

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/futures/usdt/contracts/{exchange_symbol}"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get funding rate: {resp.status}")

            data = await resp.json()

            # Gate returns funding rate as decimal
            funding_rate = Decimal(str(data.get("funding_rate", 0)))
            mark_price = Decimal(str(data.get("mark_price", 0)))
            index_price = Decimal(str(data.get("index_price", 0)))

            # Next funding time
            next_funding = data.get("funding_next_apply")
            next_funding_time = None
            if next_funding:
                next_funding_time = datetime.fromtimestamp(next_funding, tz=timezone.utc)

            return UnifiedFunding(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                rate=funding_rate,
                mark_price=mark_price,
                index_price=index_price,
                next_funding_time=next_funding_time,
            )

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """Get open interest."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/futures/usdt/contracts/{exchange_symbol}"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get open interest: {resp.status}")

            data = await resp.json()

            # Gate returns OI in contracts
            oi_contracts = Decimal(str(data.get("position_size", 0)))
            mark_price = Decimal(str(data.get("mark_price", 0)))
            quanto_multiplier = Decimal(str(data.get("quanto_multiplier", 1)))

            # Convert to base currency
            oi_base = oi_contracts * quanto_multiplier
            oi_usd = oi_base * mark_price if mark_price else None

            return UnifiedOpenInterest(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                open_interest=oi_base,
                open_interest_usd=oi_usd,
            )

    # ============ Normalization ============

    def normalize_trade(self, raw: Dict[str, Any]) -> UnifiedTrade:
        """Normalize trade data."""
        # Gate trade format:
        # {"id": 123, "create_time": 1234567890, "contract": "BTC_USDT",
        #  "size": 100, "price": "50000.5"}
        contract = raw.get("contract", "")
        symbol = self.to_unified_symbol(contract)

        # Size is negative for sells
        size = int(raw.get("size", 0))
        side = Side.BUY if size > 0 else Side.SELL
        quantity = abs(Decimal(str(size)))

        price = Decimal(str(raw.get("price", 0)))

        # Adjust quantity by contract size if available
        if symbol in self._symbols:
            contract_size = self._symbols[symbol].contract_size or Decimal(1)
            quantity = quantity * contract_size

        timestamp = raw.get("create_time", 0)
        if isinstance(timestamp, (int, float)):
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            dt = datetime.now(timezone.utc)

        return UnifiedTrade(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=dt,
            price=price,
            quantity=quantity,
            side=side,
            trade_id=str(raw.get("id", "")),
            quote_quantity=price * quantity,
            raw=raw,
        )

    def normalize_orderbook(
        self, raw: Dict[str, Any], symbol: Optional[str] = None
    ) -> UnifiedOrderBook:
        """Normalize orderbook data."""
        # Gate orderbook format:
        # {"asks": [{"p": "price", "s": size}], "bids": [...], "contract": "BTC_USDT"}
        contract = raw.get("contract", "") or raw.get("c", "")
        if not symbol:
            symbol = self.to_unified_symbol(contract)

        bids = []
        asks = []

        for bid in raw.get("bids", []):
            price = Decimal(str(bid.get("p", 0)))
            size = abs(Decimal(str(bid.get("s", 0))))
            bids.append(OrderBookLevel(price=price, quantity=size))

        for ask in raw.get("asks", []):
            price = Decimal(str(ask.get("p", 0)))
            size = abs(Decimal(str(ask.get("s", 0))))
            asks.append(OrderBookLevel(price=price, quantity=size))

        # Sort: bids descending, asks ascending
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        timestamp = raw.get("t", raw.get("update", 0))
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            if timestamp > 1e12:  # milliseconds
                timestamp = timestamp / 1000
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        else:
            dt = datetime.now(timezone.utc)

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=dt,
            bids=bids,
            asks=asks,
            sequence=raw.get("id"),
        )
