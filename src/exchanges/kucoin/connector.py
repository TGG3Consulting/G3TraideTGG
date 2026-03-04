# -*- coding: utf-8 -*-
"""
KuCoin exchange connector.

WebSocket: Dynamic (obtained via REST API)
REST: https://api.kucoin.com/api/v1
Rate limits: 1800/min REST, 300 topics WS
"""

import asyncio
import json
import logging
import time
import uuid
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


class KuCoinConnector(BaseExchange):
    """KuCoin Futures connector."""

    EXCHANGE_NAME = "kucoin"
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

    DEFAULT_REST_URL = "https://api-futures.kucoin.com"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize KuCoin connector."""
        if config is None:
            config = ExchangeConfig(
                name="kucoin",
                rest_url=self.DEFAULT_REST_URL,
            )
        super().__init__(config)

        # KuCoin URLs
        self._rest_url = config.rest_url or self.DEFAULT_REST_URL
        self._ws_url: Optional[str] = None  # Obtained dynamically

        # Rate limiter: 1800/min = 30/sec
        self._rate_limiter = RateLimiter(
            RateLimitConfig(
                requests_per_second=30,
                requests_per_minute=1800,
            )
        )

        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # WebSocket state
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._ws_token: Optional[str] = None
        self._ping_interval: int = 30000  # ms
        self._subscriptions: Dict[str, Set[str]] = {
            "trades": set(),
            "orderbook": set(),
        }

        # Callbacks
        self._trade_callbacks: List[Callable] = []
        self._orderbook_callbacks: List[Callable] = []

        # Connection ID
        self._connect_id = str(uuid.uuid4())

    # ============ Symbol Conversion ============

    def to_exchange_symbol(self, unified_symbol: str) -> str:
        """Convert unified symbol to KuCoin format: BTC/USDT -> XBTUSDTM"""
        base, quote = unified_symbol.split("/")
        # KuCoin uses XBT for BTC
        if base == "BTC":
            base = "XBT"
        return f"{base}{quote}M"

    def to_unified_symbol(self, exchange_symbol: str) -> str:
        """Convert KuCoin symbol to unified format: XBTUSDTM -> BTC/USDT"""
        # Remove trailing M
        symbol = exchange_symbol.rstrip("M")
        # Find quote currency
        for quote in ["USDT", "USD", "USDC"]:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                if base == "XBT":
                    base = "BTC"
                return f"{base}/{quote}"
        return exchange_symbol

    def normalize_symbol(self, raw_symbol: str) -> str:
        """Normalize raw symbol to unified format."""
        return self.to_unified_symbol(raw_symbol)

    async def load_symbols(self) -> List[SymbolInfo]:
        """Load all trading symbols."""
        await self._load_symbols()
        return list(self._symbols.values())

    # ============ Connection Management ============

    async def connect(self) -> None:
        """Connect to KuCoin."""
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

            # Get WebSocket token and endpoint
            await self._get_ws_endpoint()

            # Connect WebSocket
            self._ws = await self._session.ws_connect(
                f"{self._ws_url}?token={self._ws_token}&connectId={self._connect_id}",
                heartbeat=None,  # We handle ping/pong manually
            )

            # Wait for welcome message
            msg = await self._ws.receive()
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "welcome":
                    logger.debug(f"[{self.EXCHANGE_NAME}] Received welcome")

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
        """Disconnect from KuCoin."""
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

    async def _get_ws_endpoint(self) -> None:
        """Get WebSocket endpoint and token."""
        url = f"{self._rest_url}/api/v1/bullet-public"

        async with self._session.post(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get WS endpoint: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})

            self._ws_token = data.get("token")
            instances = data.get("instanceServers", [])

            if instances:
                self._ws_url = instances[0].get("endpoint")
                self._ping_interval = instances[0].get("pingInterval", 30000)

            logger.debug(f"[{self.EXCHANGE_NAME}] WS endpoint: {self._ws_url}")

    async def _load_symbols(self) -> None:
        """Load available symbols from KuCoin."""
        url = f"{self._rest_url}/api/v1/contracts/active"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to load symbols: {resp.status}")

            result = await resp.json()
            contracts = result.get("data", [])

            for contract in contracts:
                exchange_symbol = contract.get("symbol", "")
                unified = self.to_unified_symbol(exchange_symbol)

                base = contract.get("baseCurrency", "")
                quote = contract.get("quoteCurrency", "USDT")
                self._symbols[unified] = SymbolInfo(
                    exchange=self.EXCHANGE_NAME,
                    symbol_unified=unified,
                    symbol_exchange=exchange_symbol,
                    base_asset=base,
                    quote_asset=quote,
                    market_type=MarketType.FUTURES_PERPETUAL,
                    price_precision=2,
                    quantity_precision=0,
                    min_quantity=Decimal(str(contract.get("lotSize", 1))),
                    min_notional=Decimal("5"),
                    tick_size=Decimal(str(contract.get("tickSize", "0.01"))),
                    step_size=Decimal(str(contract.get("lotSize", 1))),
                )
                self._symbol_map[exchange_symbol] = unified
                self._reverse_symbol_map[unified] = exchange_symbol

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
            interval = self._ping_interval / 1000  # Convert to seconds
            while self._state == ConnectionState.CONNECTED:
                await asyncio.sleep(interval / 2)  # Send at half interval for safety
                if self._ws and not self._ws.closed:
                    ping_msg = {
                        "id": str(uuid.uuid4()),
                        "type": "ping",
                    }
                    await self._ws.send_json(ping_msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.EXCHANGE_NAME}] Ping error: {e}")

    async def _process_message(self, data: Dict[str, Any]) -> None:
        """Process WebSocket message."""
        msg_type = data.get("type", "")

        # Handle pong
        if msg_type == "pong":
            return

        # Handle ack
        if msg_type == "ack":
            logger.debug(f"[{self.EXCHANGE_NAME}] Subscription ack: {data.get('id')}")
            return

        # Handle message
        if msg_type == "message":
            topic = data.get("topic", "")
            subject = data.get("subject", "")
            payload = data.get("data", {})

            # Trade updates
            if "trade" in subject or "/contractMarket/execution" in topic:
                trade = self.normalize_trade(payload)
                for callback in self._trade_callbacks:
                    try:
                        callback(trade)
                    except Exception as e:
                        logger.error(f"Trade callback error: {e}")

            # Orderbook updates
            elif "level2" in topic or subject == "level2":
                orderbook = self.normalize_orderbook(payload)
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

            # Get new token
            await self._get_ws_endpoint()
            self._connect_id = str(uuid.uuid4())

            # Reconnect
            self._ws = await self._session.ws_connect(
                f"{self._ws_url}?token={self._ws_token}&connectId={self._connect_id}",
                heartbeat=None,
            )

            # Wait for welcome
            msg = await self._ws.receive()
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "welcome":
                    logger.debug(f"[{self.EXCHANGE_NAME}] Received welcome")

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
                "id": str(uuid.uuid4()),
                "type": "subscribe",
                "topic": f"/contractMarket/execution:{exchange_symbol}",
                "privateChannel": False,
                "response": True,
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
            # KuCoin supports level2Depth5, level2Depth50
            depth_level = "5" if depth <= 5 else "50"

            msg = {
                "id": str(uuid.uuid4()),
                "type": "subscribe",
                "topic": f"/contractMarket/level2Depth{depth_level}:{exchange_symbol}",
                "privateChannel": False,
                "response": True,
            }

            await self._ws.send_json(msg)
            self._subscriptions["orderbook"].add(symbol)
            logger.info(f"[{self.EXCHANGE_NAME}] Subscribed to orderbook: {symbol}")

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        if not self._ws or self._ws.closed:
            return

        for symbol in symbols:
            self._subscriptions.get(stream_type, set()).discard(symbol)
            exchange_symbol = self.to_exchange_symbol(symbol)

            if stream_type == "trades":
                topic = f"/contractMarket/execution:{exchange_symbol}"
            elif stream_type == "orderbook":
                topic = f"/contractMarket/level2Depth50:{exchange_symbol}"
            else:
                continue

            msg = {
                "id": str(uuid.uuid4()),
                "type": "unsubscribe",
                "topic": topic,
                "privateChannel": False,
                "response": True,
            }
            await self._ws.send_json(msg)
            logger.info(f"[{self.EXCHANGE_NAME}] Unsubscribed from {stream_type}: {symbol}")

    # ============ REST API ============

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get ticker for symbol."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/api/v1/ticker"
        params = {"symbol": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get ticker: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})

            return UnifiedTicker(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                last_price=Decimal(str(data.get("price", 0))),
                bid_price=Decimal(str(data.get("bestBidPrice", 0) or 0)),
                ask_price=Decimal(str(data.get("bestAskPrice", 0) or 0)),
                volume_24h=Decimal(str(data.get("volume", 0) or 0)),
                high_24h=Decimal(str(data.get("priceHigh", 0) or 0)),
                low_24h=Decimal(str(data.get("priceLow", 0) or 0)),
                change_24h=Decimal(str(data.get("priceChangePercent", 0) or 0)) / 100,
            )

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        depth = 20 if limit <= 20 else 100
        url = f"{self._rest_url}/api/v1/level2/depth{depth}"
        params = {"symbol": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get orderbook: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})
            return self.normalize_orderbook(data, symbol)

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/api/v1/funding-rate/{exchange_symbol}/current"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get funding rate: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})

            funding_rate = Decimal(str(data.get("value", 0)))

            # Get mark price separately
            mark_url = f"{self._rest_url}/api/v1/mark-price/{exchange_symbol}/current"
            async with self._session.get(mark_url) as mark_resp:
                mark_data = (await mark_resp.json()).get("data", {})
                mark_price = Decimal(str(mark_data.get("value", 0)))

            # Next funding time
            next_funding_ts = data.get("timePoint")
            next_funding_time = None
            if next_funding_ts:
                next_funding_time = datetime.fromtimestamp(
                    next_funding_ts / 1000, tz=timezone.utc
                )

            return UnifiedFunding(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                rate=funding_rate,
                mark_price=mark_price,
                next_funding_time=next_funding_time,
            )

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """Get open interest."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/api/v1/contracts/active"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get open interest: {resp.status}")

            result = await resp.json()
            contracts = result.get("data", [])

            # Find our contract
            for contract in contracts:
                if contract.get("symbol") == exchange_symbol:
                    oi = Decimal(str(contract.get("openInterest", 0)))
                    mark_price = Decimal(str(contract.get("markPrice", 0)))
                    multiplier = Decimal(str(contract.get("multiplier", 1)))

                    # Convert to base currency
                    oi_base = oi * multiplier
                    oi_usd = oi_base * mark_price if mark_price else None

                    return UnifiedOpenInterest(
                        exchange=self.EXCHANGE_NAME,
                        symbol=symbol,
                        timestamp=datetime.now(timezone.utc),
                        open_interest=oi_base,
                        open_interest_usd=oi_usd,
                    )

            raise Exception(f"Symbol not found: {symbol}")

    # ============ Normalization ============

    def normalize_trade(self, raw: Dict[str, Any]) -> UnifiedTrade:
        """Normalize trade data."""
        # KuCoin trade format:
        # {"symbol": "XBTUSDTM", "tradeId": "123", "price": "50000.5",
        #  "size": 100, "side": "buy", "ts": 1234567890000000000}
        exchange_symbol = raw.get("symbol", "")
        symbol = self.to_unified_symbol(exchange_symbol)

        side_str = raw.get("side", "buy").lower()
        side = Side.BUY if side_str == "buy" else Side.SELL

        price = Decimal(str(raw.get("price", 0)))
        quantity = Decimal(str(raw.get("size", 0)))

        # Adjust by multiplier if available
        if symbol in self._symbols:
            contract_size = self._symbols[symbol].contract_size or Decimal(1)
            quantity = quantity * contract_size

        # Timestamp is in nanoseconds
        ts = raw.get("ts", 0)
        if ts > 1e18:  # nanoseconds
            ts = ts / 1e9
        elif ts > 1e15:  # microseconds
            ts = ts / 1e6
        elif ts > 1e12:  # milliseconds
            ts = ts / 1e3

        dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)

        return UnifiedTrade(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=dt,
            price=price,
            quantity=quantity,
            side=side,
            trade_id=str(raw.get("tradeId", "")),
            quote_quantity=price * quantity,
            raw=raw,
        )

    def normalize_orderbook(
        self, raw: Dict[str, Any], symbol: Optional[str] = None
    ) -> UnifiedOrderBook:
        """Normalize orderbook data."""
        # KuCoin orderbook format:
        # {"asks": [[price, size], ...], "bids": [[price, size], ...], "ts": 123}
        if not symbol:
            exchange_symbol = raw.get("symbol", "")
            symbol = self.to_unified_symbol(exchange_symbol)

        bids = []
        asks = []

        for bid in raw.get("bids", []):
            if isinstance(bid, list) and len(bid) >= 2:
                price = Decimal(str(bid[0]))
                size = Decimal(str(bid[1]))
                bids.append(OrderBookLevel(price=price, quantity=size))

        for ask in raw.get("asks", []):
            if isinstance(ask, list) and len(ask) >= 2:
                price = Decimal(str(ask[0]))
                size = Decimal(str(ask[1]))
                asks.append(OrderBookLevel(price=price, quantity=size))

        # Sort
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        ts = raw.get("ts", 0)
        if ts > 1e12:
            ts = ts / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=dt,
            bids=bids,
            asks=asks,
            sequence=raw.get("sequence"),
        )
