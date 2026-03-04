# -*- coding: utf-8 -*-
"""
BingX exchange connector.

WebSocket: wss://open-api-swap.bingx.com/swap-market
REST: https://open-api.bingx.com/openApi
Rate limits: 1200/min REST
"""

import asyncio
import gzip
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


class BingXConnector(BaseExchange):
    """BingX Perpetual Swap connector."""

    EXCHANGE_NAME = "bingx"
    EXCHANGE_TYPE = ExchangeType.CEX
    CAPABILITIES = {
        ExchangeCapability.FUTURES,
        ExchangeCapability.WEBSOCKET,
        ExchangeCapability.TRADES,
        ExchangeCapability.ORDERBOOK,
        ExchangeCapability.FUNDING_RATE,
        ExchangeCapability.OPEN_INTEREST,
    }

    DEFAULT_WS_URL = "wss://open-api-swap.bingx.com/swap-market"
    DEFAULT_REST_URL = "https://open-api.bingx.com/openApi/swap/v2"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize BingX connector."""
        if config is None:
            config = ExchangeConfig(
                name="bingx",
                ws_url=self.DEFAULT_WS_URL,
                rest_url=self.DEFAULT_REST_URL,
            )
        super().__init__(config)

        # BingX URLs
        self._ws_url = config.ws_url or self.DEFAULT_WS_URL
        self._rest_url = config.rest_url or self.DEFAULT_REST_URL

        # Rate limiter: 1200/min = 20/sec
        self._rate_limiter = RateLimiter(
            RateLimitConfig(
                requests_per_second=20,
                requests_per_minute=1200,
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

        # Request ID
        self._req_id = 0

    def _next_req_id(self) -> str:
        self._req_id += 1
        return str(self._req_id)

    # ============ Symbol Conversion ============

    def to_exchange_symbol(self, unified_symbol: str) -> str:
        """Convert unified symbol to BingX format: BTC/USDT -> BTC-USDT"""
        return unified_symbol.replace("/", "-")

    def to_unified_symbol(self, exchange_symbol: str) -> str:
        """Convert BingX symbol to unified format: BTC-USDT -> BTC/USDT"""
        return exchange_symbol.replace("-", "/")

    def normalize_symbol(self, raw_symbol: str) -> str:
        """Normalize raw symbol to unified format."""
        return self.to_unified_symbol(raw_symbol)

    async def load_symbols(self) -> List[SymbolInfo]:
        """Load all trading symbols."""
        await self._load_symbols()
        return list(self._symbols.values())

    # ============ Connection Management ============

    async def connect(self) -> None:
        """Connect to BingX."""
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
                heartbeat=None,  # BingX uses custom ping/pong
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
        """Disconnect from BingX."""
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
        """Load available symbols from BingX."""
        url = f"{self._rest_url}/quote/contracts"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to load symbols: {resp.status}")

            result = await resp.json()
            contracts = result.get("data", [])

            for contract in contracts:
                # symbol: BTC-USDT
                exchange_symbol = contract.get("symbol", "")
                unified = self.to_unified_symbol(exchange_symbol)

                base = contract.get("asset", "")
                quote = contract.get("currency", "USDT")
                price_prec = int(contract.get("pricePrecision", 2))
                qty_prec = int(contract.get("quantityPrecision", 3))
                self._symbols[unified] = SymbolInfo(
                    exchange=self.EXCHANGE_NAME,
                    symbol_unified=unified,
                    symbol_exchange=exchange_symbol,
                    base_asset=base,
                    quote_asset=quote,
                    market_type=MarketType.FUTURES_PERPETUAL,
                    price_precision=price_prec,
                    quantity_precision=qty_prec,
                    min_quantity=Decimal(str(contract.get("tradeMinQuantity", "0.001"))),
                    min_notional=Decimal("5"),
                    tick_size=Decimal(str(10 ** -price_prec)),
                    step_size=Decimal(str(10 ** -qty_prec)),
                )
                self._symbol_map[exchange_symbol] = unified
                self._reverse_symbol_map[unified] = exchange_symbol

            logger.info(f"[{self.EXCHANGE_NAME}] Loaded {len(self._symbols)} symbols")

    # ============ WebSocket Handler ============

    async def _ws_handler(self) -> None:
        """Handle WebSocket messages."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    # BingX sends gzip-compressed messages (usually)
                    try:
                        decompressed = gzip.decompress(msg.data)
                        data = json.loads(decompressed)
                        await self._process_message(data)
                    except gzip.BadGzipFile:
                        # Not gzip, try raw
                        try:
                            data = json.loads(msg.data)
                            await self._process_message(data)
                        except Exception:
                            pass  # Skip invalid message
                    except Exception as e:
                        logger.debug(f"[{self.EXCHANGE_NAME}] Decompress error: {e}")
                elif msg.type == aiohttp.WSMsgType.TEXT:
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
                await asyncio.sleep(20)
                if self._ws and not self._ws.closed:
                    await self._ws.send_str("Ping")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.EXCHANGE_NAME}] Ping error: {e}")

    async def _process_message(self, data: Dict[str, Any]) -> None:
        """Process WebSocket message."""
        # Handle pong
        if data == "Pong" or data.get("ping"):
            return

        # Handle subscription response
        code = data.get("code")
        if code is not None:
            if code == 0:
                logger.debug(f"[{self.EXCHANGE_NAME}] Subscription success")
            else:
                logger.warning(f"[{self.EXCHANGE_NAME}] Subscription error: {data}")
            return

        # Handle data
        datatype = data.get("dataType", "")
        payload = data.get("data", {})

        if not datatype or not payload:
            return

        # Trade updates: BTC-USDT@trade
        if "@trade" in datatype:
            symbol = datatype.split("@")[0]
            payload["symbol"] = symbol
            trade = self.normalize_trade(payload)
            for callback in self._trade_callbacks:
                try:
                    callback(trade)
                except Exception as e:
                    logger.error(f"Trade callback error: {e}")

        # Orderbook updates: BTC-USDT@depth
        elif "@depth" in datatype:
            symbol = datatype.split("@")[0]
            payload["symbol"] = symbol
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

            # Reconnect
            self._ws = await self._session.ws_connect(
                self._ws_url,
                heartbeat=None,
            )

            # Restart handlers
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

        subscribed = 0
        for symbol in symbols:
            if symbol in self._subscriptions["trades"]:
                continue

            # Check symbol exists on exchange
            if symbol not in self._symbols:
                continue

            exchange_symbol = self.to_exchange_symbol(symbol)
            # BingX Perpetual Swap V2 format
            msg = {
                "id": self._next_req_id(),
                "reqType": "sub",
                "dataType": f"{exchange_symbol}@trade",
            }

            await self._ws.send_json(msg)
            self._subscriptions["trades"].add(symbol)
            subscribed += 1

        if subscribed > 0:
            logger.info(f"[{self.EXCHANGE_NAME}] Subscribed to trades: {subscribed} symbols")

    async def subscribe_orderbook(
        self,
        symbols: List[str],
        callback: Optional[Callable[[UnifiedOrderBook], None]] = None,
        depth: int = 20,
    ) -> None:
        """Subscribe to orderbook updates."""
        if callback and callback not in self._orderbook_callbacks:
            self._orderbook_callbacks.append(callback)

        subscribed = 0
        for symbol in symbols:
            if symbol in self._subscriptions["orderbook"]:
                continue

            # Check symbol exists on exchange
            if symbol not in self._symbols:
                continue

            exchange_symbol = self.to_exchange_symbol(symbol)
            # BingX supports depth5, depth10, depth20, depth50, depth100
            depth_level = min([d for d in [5, 10, 20, 50, 100] if d >= depth], default=20)

            msg = {
                "id": self._next_req_id(),
                "reqType": "sub",
                "dataType": f"{exchange_symbol}@depth{depth_level}",
            }

            await self._ws.send_json(msg)
            self._subscriptions["orderbook"].add(symbol)
            subscribed += 1

        if subscribed > 0:
            logger.info(f"[{self.EXCHANGE_NAME}] Subscribed to orderbook: {subscribed} symbols")

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        if not self._ws or self._ws.closed:
            return

        for symbol in symbols:
            self._subscriptions.get(stream_type, set()).discard(symbol)
            exchange_symbol = self.to_exchange_symbol(symbol)

            if stream_type == "trades":
                data_type = f"{exchange_symbol}@trade"
            elif stream_type == "orderbook":
                data_type = f"{exchange_symbol}@depth20"
            else:
                continue

            msg = {
                "id": self._next_req_id(),
                "reqType": "unsub",
                "dataType": data_type,
            }
            await self._ws.send_json(msg)
            logger.info(f"[{self.EXCHANGE_NAME}] Unsubscribed from {stream_type}: {symbol}")

    # ============ REST API ============

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get ticker for symbol."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/quote/ticker"
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
                last_price=Decimal(str(data.get("lastPrice", 0))),
                bid_price=Decimal(str(data.get("bidPrice", 0))),
                ask_price=Decimal(str(data.get("askPrice", 0))),
                volume_24h=Decimal(str(data.get("volume", 0))),
                high_24h=Decimal(str(data.get("highPrice", 0))),
                low_24h=Decimal(str(data.get("lowPrice", 0))),
                change_24h=Decimal(str(data.get("priceChangePercent", 0))) / 100,
            )

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/quote/depth"
        params = {"symbol": exchange_symbol, "limit": limit}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get orderbook: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})
            data["symbol"] = exchange_symbol
            return self.normalize_orderbook(data, symbol)

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/quote/premiumIndex"
        params = {"symbol": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get funding rate: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})

            funding_rate = Decimal(str(data.get("lastFundingRate", 0)))
            mark_price = Decimal(str(data.get("markPrice", 0)))
            index_price = Decimal(str(data.get("indexPrice", 0)))

            # Next funding time
            next_funding_ts = data.get("nextFundingTime")
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
                index_price=index_price,
                next_funding_time=next_funding_time,
            )

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """Get open interest."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/quote/openInterest"
        params = {"symbol": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get open interest: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})

            oi = Decimal(str(data.get("openInterest", 0)))

            # Get current price for USD value
            ticker = await self.get_ticker(symbol)
            oi_usd = oi * ticker.last_price if ticker.last_price else None

            return UnifiedOpenInterest(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                open_interest=oi,
                open_interest_usd=oi_usd,
            )

    # ============ Normalization ============

    def normalize_trade(self, raw: Dict[str, Any]) -> UnifiedTrade:
        """Normalize trade data."""
        exchange_symbol = raw.get("symbol", raw.get("s", ""))
        symbol = self.to_unified_symbol(exchange_symbol)

        # BingX: m=true means buyer is maker (so it's a sell)
        is_buyer_maker = raw.get("m", False)
        side = Side.SELL if is_buyer_maker else Side.BUY

        price = Decimal(str(raw.get("p", 0)))
        quantity = Decimal(str(raw.get("q", 0)))

        ts = raw.get("T", raw.get("t", 0))
        if ts > 1e12:
            ts = ts / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)

        return UnifiedTrade(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=dt,
            price=price,
            quantity=quantity,
            side=side,
            trade_id=str(raw.get("id", raw.get("t", ""))),
            quote_quantity=price * quantity,
            is_maker=is_buyer_maker,
            raw=raw,
        )

    def normalize_orderbook(
        self, raw: Dict[str, Any], symbol: Optional[str] = None
    ) -> UnifiedOrderBook:
        """Normalize orderbook data."""
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

        ts = raw.get("T", raw.get("timestamp", 0))
        if ts > 1e12:
            ts = ts / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=dt,
            bids=bids,
            asks=asks,
        )
