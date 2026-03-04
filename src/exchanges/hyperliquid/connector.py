# -*- coding: utf-8 -*-
"""
Hyperliquid DEX connector.

WebSocket: wss://api.hyperliquid.xyz/ws
REST: https://api.hyperliquid.xyz/info
Rate limits: 1200/min (generous)

Hyperliquid is a decentralized perpetual exchange built on its own L1.
Key features:
- No orderbook manipulation (on-chain)
- Transparent funding rates
- Lower latency than most CEXs
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


class HyperliquidConnector(BaseExchange):
    """Hyperliquid DEX connector."""

    EXCHANGE_NAME = "hyperliquid"
    EXCHANGE_TYPE = ExchangeType.DEX
    CAPABILITIES = {
        ExchangeCapability.FUTURES,
        ExchangeCapability.WEBSOCKET,
        ExchangeCapability.TRADES,
        ExchangeCapability.ORDERBOOK,
        ExchangeCapability.FUNDING_RATE,
        ExchangeCapability.OPEN_INTEREST,
    }

    DEFAULT_WS_URL = "wss://api.hyperliquid.xyz/ws"
    DEFAULT_REST_URL = "https://api.hyperliquid.xyz/info"
    TESTNET_WS_URL = "wss://api.hyperliquid-testnet.xyz/ws"
    TESTNET_REST_URL = "https://api.hyperliquid-testnet.xyz/info"

    def __init__(self, config: Optional[ExchangeConfig] = None, testnet: bool = False):
        """Initialize Hyperliquid connector."""
        if config is None:
            if testnet:
                config = ExchangeConfig(
                    name="hyperliquid",
                    ws_url=self.TESTNET_WS_URL,
                    rest_url=self.TESTNET_REST_URL,
                )
            else:
                config = ExchangeConfig(
                    name="hyperliquid",
                    ws_url=self.DEFAULT_WS_URL,
                    rest_url=self.DEFAULT_REST_URL,
                )
        super().__init__(config)

        # Hyperliquid URLs
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

        # Symbol to index mapping (Hyperliquid uses numeric indices)
        self._symbol_to_idx: Dict[str, int] = {}
        self._idx_to_symbol: Dict[int, str] = {}

    # ============ Symbol Conversion ============

    def to_exchange_symbol(self, unified_symbol: str) -> str:
        """Convert unified symbol to Hyperliquid format: BTC/USDT -> BTC"""
        base = unified_symbol.split("/")[0]
        return base

    def to_unified_symbol(self, exchange_symbol: str) -> str:
        """Convert Hyperliquid symbol to unified format: BTC -> BTC/USDT"""
        return f"{exchange_symbol}/USDT"

    def normalize_symbol(self, raw_symbol: str) -> str:
        """Normalize raw symbol to unified format."""
        return self.to_unified_symbol(raw_symbol)

    async def load_symbols(self) -> List[SymbolInfo]:
        """Load all trading symbols."""
        await self._load_symbols()
        return list(self._symbols.values())

    # ============ Connection Management ============

    async def connect(self) -> None:
        """Connect to Hyperliquid."""
        if self._state == ConnectionState.CONNECTED:
            return

        self._state = ConnectionState.CONNECTING
        logger.info(f"[{self.EXCHANGE_NAME}] Connecting...")

        try:
            # Create session
            if not self._session:
                self._session = aiohttp.ClientSession()

            # Load symbols (meta info)
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
        """Disconnect from Hyperliquid."""
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
        """Load available symbols from Hyperliquid."""
        # Hyperliquid uses POST for info endpoint
        payload = {"type": "meta"}

        async with self._session.post(self._rest_url, json=payload) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to load symbols: {resp.status}")

            data = await resp.json()
            universe = data.get("universe", [])

            for idx, asset in enumerate(universe):
                name = asset.get("name", "")  # BTC, ETH, etc.
                unified = self.to_unified_symbol(name)

                self._symbol_to_idx[unified] = idx
                self._idx_to_symbol[idx] = unified

                # Size decimals determines min qty
                sz_decimals = asset.get("szDecimals", 3)

                self._symbols[unified] = SymbolInfo(
                    exchange=self.EXCHANGE_NAME,
                    symbol_unified=unified,
                    symbol_exchange=name,
                    base_asset=name,
                    quote_asset="USDT",
                    market_type=MarketType.FUTURES_PERPETUAL,
                    price_precision=4,
                    quantity_precision=sz_decimals,
                    min_quantity=Decimal(str(10 ** -sz_decimals)),
                    min_notional=Decimal("10"),
                    tick_size=Decimal("0.0001"),
                    step_size=Decimal(str(10 ** -sz_decimals)),
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
                await asyncio.sleep(30)
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
        payload = data.get("data", {})

        # Handle subscription confirmation
        if data.get("method") == "subscribed":
            logger.debug(f"[{self.EXCHANGE_NAME}] Subscribed to: {data.get('subscription')}")
            return

        # Handle trades
        if channel == "trades":
            trades = payload if isinstance(payload, list) else [payload]
            for trade_data in trades:
                trade = self.normalize_trade(trade_data)
                for callback in self._trade_callbacks:
                    try:
                        callback(trade)
                    except Exception as e:
                        logger.error(f"Trade callback error: {e}")

        # Handle orderbook (l2Book)
        elif channel == "l2Book":
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
                "method": "subscribe",
                "subscription": {
                    "type": "trades",
                    "coin": exchange_symbol,
                },
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
            msg = {
                "method": "subscribe",
                "subscription": {
                    "type": "l2Book",
                    "coin": exchange_symbol,
                },
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
                sub_type = "trades"
            elif stream_type == "orderbook":
                sub_type = "l2Book"
            else:
                continue

            msg = {
                "method": "unsubscribe",
                "subscription": {
                    "type": sub_type,
                    "coin": exchange_symbol,
                },
            }
            await self._ws.send_json(msg)
            logger.info(f"[{self.EXCHANGE_NAME}] Unsubscribed from {stream_type}: {symbol}")

    # ============ REST API ============

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get ticker for symbol."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        payload = {"type": "allMids"}

        async with self._session.post(self._rest_url, json=payload) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get ticker: {resp.status}")

            data = await resp.json()
            mid_price = Decimal(str(data.get(exchange_symbol, 0)))

            return UnifiedTicker(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                last_price=mid_price,
                bid_price=mid_price,  # Approximate
                ask_price=mid_price,
                volume_24h=Decimal(0),  # Need separate call
            )

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        payload = {"type": "l2Book", "coin": exchange_symbol}

        async with self._session.post(self._rest_url, json=payload) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get orderbook: {resp.status}")

            data = await resp.json()
            data["coin"] = exchange_symbol
            return self.normalize_orderbook(data, symbol)

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        payload = {"type": "metaAndAssetCtxs"}

        async with self._session.post(self._rest_url, json=payload) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get funding rate: {resp.status}")

            result = await resp.json()
            meta = result[0] if len(result) > 0 else {}
            asset_ctxs = result[1] if len(result) > 1 else []

            # Find asset index
            universe = meta.get("universe", [])
            idx = None
            for i, asset in enumerate(universe):
                if asset.get("name") == exchange_symbol:
                    idx = i
                    break

            if idx is None or idx >= len(asset_ctxs):
                raise Exception(f"Symbol not found: {symbol}")

            ctx = asset_ctxs[idx]

            funding_rate = Decimal(str(ctx.get("funding", 0)))
            mark_price = Decimal(str(ctx.get("markPx", 0)))
            oracle_price = Decimal(str(ctx.get("oraclePx", 0)))

            return UnifiedFunding(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                rate=funding_rate,
                mark_price=mark_price,
                index_price=oracle_price,
                next_funding_time=None,  # Hyperliquid has continuous funding
            )

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """Get open interest."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        payload = {"type": "metaAndAssetCtxs"}

        async with self._session.post(self._rest_url, json=payload) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get open interest: {resp.status}")

            result = await resp.json()
            meta = result[0] if len(result) > 0 else {}
            asset_ctxs = result[1] if len(result) > 1 else []

            # Find asset index
            universe = meta.get("universe", [])
            idx = None
            for i, asset in enumerate(universe):
                if asset.get("name") == exchange_symbol:
                    idx = i
                    break

            if idx is None or idx >= len(asset_ctxs):
                raise Exception(f"Symbol not found: {symbol}")

            ctx = asset_ctxs[idx]

            oi = Decimal(str(ctx.get("openInterest", 0)))
            mark_price = Decimal(str(ctx.get("markPx", 0)))
            oi_usd = oi * mark_price if mark_price else None

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
        # Hyperliquid trade format:
        # {"coin": "BTC", "side": "B", "px": "50000.5", "sz": "0.1", "time": 1234567890000}
        coin = raw.get("coin", "")
        symbol = self.to_unified_symbol(coin)

        side_str = raw.get("side", "B")
        side = Side.BUY if side_str == "B" else Side.SELL

        price = Decimal(str(raw.get("px", 0)))
        quantity = Decimal(str(raw.get("sz", 0)))

        ts = raw.get("time", 0)
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
            trade_id=str(raw.get("tid", "")),
            quote_quantity=price * quantity,
            raw=raw,
        )

    def normalize_orderbook(
        self, raw: Dict[str, Any], symbol: Optional[str] = None
    ) -> UnifiedOrderBook:
        """Normalize orderbook data."""
        if not symbol:
            coin = raw.get("coin", "")
            symbol = self.to_unified_symbol(coin)

        bids = []
        asks = []

        levels = raw.get("levels", [[], []])
        bid_levels = levels[0] if len(levels) > 0 else []
        ask_levels = levels[1] if len(levels) > 1 else []

        for bid in bid_levels:
            price = Decimal(str(bid.get("px", 0)))
            size = Decimal(str(bid.get("sz", 0)))
            bids.append(OrderBookLevel(price=price, quantity=size))

        for ask in ask_levels:
            price = Decimal(str(ask.get("px", 0)))
            size = Decimal(str(ask.get("sz", 0)))
            asks.append(OrderBookLevel(price=price, quantity=size))

        # Sort
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)

        ts = raw.get("time", 0)
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
