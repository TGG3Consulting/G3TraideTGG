# -*- coding: utf-8 -*-
"""
MEXC exchange connector.

WebSocket: wss://wbs.mexc.com/ws
REST: https://api.mexc.com/api/v3
Rate limits: 20/s REST (STRICT!), 30 streams WS

WARNING: MEXC has very strict rate limits. Use with caution.
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


class MEXCConnector(BaseExchange):
    """MEXC Futures connector with strict rate limiting."""

    EXCHANGE_NAME = "mexc"
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

    DEFAULT_WS_URL = "wss://contract.mexc.com/edge"
    DEFAULT_REST_URL = "https://contract.mexc.com/api/v1"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize MEXC connector."""
        if config is None:
            config = ExchangeConfig(
                name="mexc",
                ws_url=self.DEFAULT_WS_URL,
                rest_url=self.DEFAULT_REST_URL,
            )
        super().__init__(config)

        # MEXC URLs (Futures)
        self._ws_url = config.ws_url or self.DEFAULT_WS_URL
        self._rest_url = config.rest_url or self.DEFAULT_REST_URL

        # STRICT rate limiter: 20/s, be conservative
        self._rate_limiter = RateLimiter(
            RateLimitConfig(
                requests_per_second=10,  # Half the limit for safety
                requests_per_minute=500,
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

    # ============ Symbol Conversion ============

    def to_exchange_symbol(self, unified_symbol: str) -> str:
        """Convert unified symbol to MEXC format: BTC/USDT -> BTC_USDT"""
        return unified_symbol.replace("/", "_")

    def to_unified_symbol(self, exchange_symbol: str) -> str:
        """Convert MEXC symbol to unified format: BTC_USDT -> BTC/USDT"""
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
        """Connect to MEXC."""
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
        """Disconnect from MEXC."""
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
        """Load available symbols from MEXC."""
        url = f"{self._rest_url}/contract/detail"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to load symbols: {resp.status}")

            result = await resp.json()
            contracts = result.get("data", [])

            for contract in contracts:
                # symbol: BTC_USDT
                exchange_symbol = contract.get("symbol", "")
                unified = self.to_unified_symbol(exchange_symbol)

                base = contract.get("baseCoin", "")
                quote = contract.get("quoteCoin", "USDT")
                self._symbols[unified] = SymbolInfo(
                    exchange=self.EXCHANGE_NAME,
                    symbol_unified=unified,
                    symbol_exchange=exchange_symbol,
                    base_asset=base,
                    quote_asset=quote,
                    market_type=MarketType.FUTURES_PERPETUAL,
                    price_precision=int(contract.get("priceScale", 2)),
                    quantity_precision=int(contract.get("volScale", 0)),
                    min_quantity=Decimal(str(contract.get("minVol", 1))),
                    min_notional=Decimal("5"),
                    tick_size=Decimal(str(contract.get("priceUnit", "0.01"))),
                    step_size=Decimal(str(contract.get("volUnit", 1))),
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
            while self._state == ConnectionState.CONNECTED:
                await asyncio.sleep(15)
                if self._ws and not self._ws.closed:
                    ping_msg = {"method": "ping"}
                    await self._ws.send_json(ping_msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[{self.EXCHANGE_NAME}] Ping error: {e}")

    async def _process_message(self, data: Dict[str, Any]) -> None:
        """Process WebSocket message."""
        channel = data.get("channel", "")

        # Handle pong
        if data.get("data") == "pong" or channel == "pong":
            return

        # Handle subscription response
        if data.get("data") == "success":
            logger.debug(f"[{self.EXCHANGE_NAME}] Subscription success")
            return

        # Handle trade updates
        if "deal" in channel:
            trades = data.get("data", [])
            symbol = data.get("symbol", "")
            for trade_data in trades:
                trade_data["symbol"] = symbol
                trade = self.normalize_trade(trade_data)
                for callback in self._trade_callbacks:
                    try:
                        callback(trade)
                    except Exception as e:
                        logger.error(f"Trade callback error: {e}")

        # Handle orderbook updates
        elif "depth" in channel:
            ob_data = data.get("data", {})
            ob_data["symbol"] = data.get("symbol", "")
            orderbook = self.normalize_orderbook(ob_data)
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

        for symbol in symbols:
            if symbol in self._subscriptions["trades"]:
                continue

            exchange_symbol = self.to_exchange_symbol(symbol)
            msg = {
                "method": "sub.deal",
                "param": {"symbol": exchange_symbol},
            }

            await self._ws.send_json(msg)
            self._subscriptions["trades"].add(symbol)
            logger.info(f"[{self.EXCHANGE_NAME}] Subscribed to trades: {symbol}")
            # Small delay to avoid rate limits
            await asyncio.sleep(0.1)

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
            msg = {
                "method": "sub.depth",
                "param": {"symbol": exchange_symbol},
            }

            await self._ws.send_json(msg)
            self._subscriptions["orderbook"].add(symbol)
            logger.info(f"[{self.EXCHANGE_NAME}] Subscribed to orderbook: {symbol}")
            await asyncio.sleep(0.1)

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        if not self._ws or self._ws.closed:
            return

        method_map = {
            "trades": "unsub.deal",
            "orderbook": "unsub.depth",
        }
        method = method_map.get(stream_type)
        if not method:
            return

        for symbol in symbols:
            self._subscriptions.get(stream_type, set()).discard(symbol)
            exchange_symbol = self.to_exchange_symbol(symbol)

            msg = {
                "method": method,
                "param": {"symbol": exchange_symbol},
            }
            await self._ws.send_json(msg)
            logger.info(f"[{self.EXCHANGE_NAME}] Unsubscribed from {stream_type}: {symbol}")

    # ============ REST API ============

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get ticker for symbol."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/contract/ticker"
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
                bid_price=Decimal(str(data.get("bid1", 0))),
                ask_price=Decimal(str(data.get("ask1", 0))),
                volume_24h=Decimal(str(data.get("volume24", 0))),
                high_24h=Decimal(str(data.get("high24Price", 0))),
                low_24h=Decimal(str(data.get("low24Price", 0))),
                change_24h=Decimal(str(data.get("riseFallRate", 0))),
            )

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/contract/depth/{exchange_symbol}"

        async with self._session.get(url) as resp:
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
        url = f"{self._rest_url}/contract/funding_rate/{exchange_symbol}"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get funding rate: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})

            funding_rate = Decimal(str(data.get("fundingRate", 0)))
            mark_price = Decimal(str(data.get("markPrice", 0)))
            index_price = Decimal(str(data.get("indexPrice", 0)))

            # Next funding time
            next_funding_ts = data.get("nextSettleTime")
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
        url = f"{self._rest_url}/contract/ticker"
        params = {"symbol": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get open interest: {resp.status}")

            result = await resp.json()
            data = result.get("data", {})

            # MEXC returns OI in the ticker
            oi = Decimal(str(data.get("holdVol", 0)))
            last_price = Decimal(str(data.get("lastPrice", 0)))

            oi_usd = oi * last_price if last_price else None

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
        exchange_symbol = raw.get("symbol", "")
        symbol = self.to_unified_symbol(exchange_symbol)

        # MEXC: T=1 for buy, T=2 for sell
        trade_type = raw.get("T", 1)
        side = Side.BUY if trade_type == 1 else Side.SELL

        price = Decimal(str(raw.get("p", 0)))
        quantity = Decimal(str(raw.get("v", 0)))

        ts = raw.get("t", 0)
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
            trade_id=str(raw.get("id", "")),
            quote_quantity=price * quantity,
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

        ts = raw.get("timestamp", 0)
        if ts > 1e12:
            ts = ts / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=dt,
            bids=bids,
            asks=asks,
            sequence=raw.get("version"),
        )
