# -*- coding: utf-8 -*-
"""
Bybit Exchange Connector.

Implements BaseExchange interface for Bybit V5 API.
Supports both Spot and USDT Perpetual futures.

WebSocket:
- wss://stream.bybit.com/v5/public/linear (futures)
- wss://stream.bybit.com/v5/public/spot

REST:
- https://api.bybit.com/v5/market/tickers
- https://api.bybit.com/v5/market/orderbook
- https://api.bybit.com/v5/market/open-interest
- https://api.bybit.com/v5/market/funding/history
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Set

import structlog

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:
    websockets = None
    ConnectionClosed = Exception

try:
    import aiohttp
except ImportError:
    aiohttp = None

from src.exchanges.base import (
    BaseExchange,
    ExchangeConfig,
    ExchangeType,
    ExchangeCapability,
    ConnectionState,
    SymbolInfo,
    TradeCallback,
    OrderBookCallback,
)
from src.exchanges.models import (
    UnifiedTrade,
    UnifiedOrderBook,
    UnifiedTicker,
    UnifiedFunding,
    UnifiedOpenInterest,
    Side,
    MarketType,
)
from src.exchanges.rate_limiter import RateLimiter, ExchangeRateLimits

logger = structlog.get_logger(__name__)


class BybitConnector(BaseExchange):
    """
    Bybit V5 API connector.

    Supports:
    - Linear perpetual futures (USDT-M)
    - Spot market
    - WebSocket streaming for trades and orderbook
    - REST for funding rate, open interest

    Usage:
        connector = BybitConnector()
        await connector.connect()
        await connector.subscribe_trades(["BTC/USDT"])
        funding = await connector.get_funding_rate("BTC/USDT")
    """

    EXCHANGE_NAME = "bybit"
    EXCHANGE_TYPE = ExchangeType.CEX
    CAPABILITIES = {
        ExchangeCapability.SPOT_TRADING,
        ExchangeCapability.FUTURES_PERPETUAL,
        ExchangeCapability.TRADES_STREAM,
        ExchangeCapability.ORDERBOOK_STREAM,
        ExchangeCapability.FUNDING_RATE,
        ExchangeCapability.OPEN_INTEREST,
        ExchangeCapability.HISTORICAL_FUNDING,
    }

    # Default URLs
    DEFAULT_WS_FUTURES = "wss://stream.bybit.com/v5/public/linear"
    DEFAULT_WS_SPOT = "wss://stream.bybit.com/v5/public/spot"
    DEFAULT_REST_URL = "https://api.bybit.com"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize Bybit connector."""
        if config is None:
            config = ExchangeConfig(
                name="bybit",
                ws_url=self.DEFAULT_WS_FUTURES,
                rest_url=self.DEFAULT_REST_URL,
            )

        super().__init__(config)

        self._ws_url = config.ws_futures_url or config.ws_url or self.DEFAULT_WS_FUTURES
        self._rest_url = config.rest_url or self.DEFAULT_REST_URL

        # Rate limiter
        self._rate_limiter = RateLimiter(
            ExchangeRateLimits.BYBIT,
            name="bybit"
        )

        # WebSocket
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._reconnect_count = 0

        # HTTP session
        self._http_session: Optional[aiohttp.ClientSession] = None

        # Subscriptions
        self._active_topics: Set[str] = set()

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to Bybit."""
        if websockets is None:
            raise ImportError("websockets package required: pip install websockets")

        self._state = ConnectionState.CONNECTING
        self.logger.info("connecting")

        try:
            await self._load_exchange_info()
            self._state = ConnectionState.CONNECTED
            self.logger.info("connected", symbols=len(self._symbols))

        except Exception as e:
            self._state = ConnectionState.ERROR
            self.logger.error("connect_failed", error=str(e))
            raise

    async def disconnect(self) -> None:
        """Disconnect from Bybit."""
        self._state = ConnectionState.CLOSING
        self.logger.info("disconnecting")

        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

        self._active_topics.clear()
        self._state = ConnectionState.DISCONNECTED
        self.logger.info("disconnected")

    async def _load_exchange_info(self) -> None:
        """Load exchange info from Bybit."""
        # Load linear perpetuals
        url = f"{self._rest_url}/v5/market/instruments-info"
        params = {"category": "linear"}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Exchange info failed: {resp.status}")
                data = await resp.json()

        if data.get("retCode") != 0:
            raise Exception(f"Bybit API error: {data.get('retMsg')}")

        for item in data.get("result", {}).get("list", []):
            if item.get("status") != "Trading":
                continue
            if item.get("contractType") != "LinearPerpetual":
                continue

            exchange_symbol = item["symbol"]
            base = item.get("baseCoin", "")
            quote = item.get("quoteCoin", "USDT")
            unified_symbol = f"{base}/{quote}"

            lot_filter = item.get("lotSizeFilter", {})
            price_filter = item.get("priceFilter", {})

            # CONN-2 FIX: Правильный расчёт precision через Decimal exponent
            tick_size_str = price_filter.get("tickSize", "0.01")
            qty_step_str = lot_filter.get("qtyStep", "0.001")
            price_prec = abs(Decimal(tick_size_str).as_tuple().exponent)
            qty_prec = abs(Decimal(qty_step_str).as_tuple().exponent)

            info = SymbolInfo(
                exchange=self.EXCHANGE_NAME,
                symbol_unified=unified_symbol,
                symbol_exchange=exchange_symbol,
                base_asset=base,
                quote_asset=quote,
                market_type=MarketType.FUTURES_PERPETUAL,
                price_precision=price_prec,
                quantity_precision=qty_prec,
                min_quantity=Decimal(lot_filter.get("minOrderQty", "0.001")),
                min_notional=Decimal(lot_filter.get("minNotionalValue", "5")),
                tick_size=Decimal(price_filter.get("tickSize", "0.01")),
                step_size=Decimal(lot_filter.get("qtyStep", "0.001")),
            )

            self._symbols[unified_symbol] = info
            self._symbol_map[exchange_symbol] = unified_symbol
            self._reverse_symbol_map[unified_symbol] = exchange_symbol

    async def _get_http_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if aiohttp is None:
            raise ImportError("aiohttp package required: pip install aiohttp")

        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._http_session = aiohttp.ClientSession(timeout=timeout)
        return self._http_session

    # -------------------------------------------------------------------------
    # WebSocket
    # -------------------------------------------------------------------------

    async def _connect_websocket(self) -> None:
        """Connect to Bybit WebSocket."""
        self.logger.debug("ws_connecting")

        try:
            self._ws = await websockets.connect(
                self._ws_url,
                ping_interval=None,  # We handle ping manually
            )
            self._reconnect_count = 0

            self.logger.info("ws_connected")

            # Start message handler and ping
            self._ws_task = asyncio.create_task(self._ws_message_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())

        except Exception as e:
            self.logger.error("ws_connect_failed", error=str(e))
            raise

    async def _ping_loop(self) -> None:
        """Send periodic pings to keep connection alive."""
        try:
            while self._ws and not self._ws.closed:
                await asyncio.sleep(20)
                if self._ws and not self._ws.closed:
                    await self._ws.send(json.dumps({"op": "ping"}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.warning("ping_error", error=str(e))

    async def _ws_message_loop(self) -> None:
        """WebSocket message processing loop."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    await self._handle_ws_message(data)
                except json.JSONDecodeError as e:
                    self.logger.warning("ws_json_error", error=str(e))
                except Exception as e:
                    self.logger.warning("ws_message_error", error=str(e))

        except ConnectionClosed as e:
            self.logger.warning("ws_closed", code=e.code)
            await self._handle_reconnect()

        except asyncio.CancelledError:
            self.logger.debug("ws_task_cancelled")

        except Exception as e:
            self.logger.error("ws_loop_error", error=str(e))
            await self._handle_reconnect()

    async def _handle_ws_message(self, data: dict) -> None:
        """Handle incoming WebSocket message."""
        # Pong response
        if data.get("op") == "pong":
            return

        # Subscription confirmation
        if data.get("op") == "subscribe":
            success = data.get("success", False)
            if not success:
                # Silently ignore "handler not found" - means symbol doesn't exist
                ret_msg = data.get("ret_msg", "")
                if "handler not found" not in ret_msg.lower():
                    self.logger.warning("subscribe_failed", data=data)
            return

        topic = data.get("topic", "")
        msg_data = data.get("data", [])

        if not topic or not msg_data:
            return

        if topic.startswith("publicTrade."):
            for trade_data in msg_data:
                trade = self._normalize_trade(trade_data)
                self._emit_trade(trade)

        elif topic.startswith("orderbook."):
            orderbook = self._normalize_orderbook_ws(data)
            if orderbook:
                self._emit_orderbook(orderbook)

    async def _handle_reconnect(self) -> None:
        """Handle WebSocket reconnection."""
        if self._state == ConnectionState.CLOSING:
            return

        self._reconnect_count += 1

        if self._reconnect_count > self.config.max_reconnect_attempts:
            self.logger.error("max_reconnects_exceeded")
            self._state = ConnectionState.ERROR
            return

        delay = min(
            self.config.reconnect_delay * (1.5 ** self._reconnect_count),
            self.config.max_reconnect_delay
        )

        self.logger.info("ws_reconnecting", attempt=self._reconnect_count, delay=delay)
        await asyncio.sleep(delay)

        try:
            await self._connect_websocket()
            # Re-subscribe
            if self._active_topics:
                topics = list(self._active_topics)
                self._active_topics.clear()
                await self._subscribe_topics(topics)
        except Exception as e:
            self.logger.error("reconnect_failed", error=str(e))
            await self._handle_reconnect()

    async def _subscribe_topics(self, topics: List[str]) -> None:
        """Subscribe to topics."""
        if not self._ws:
            await self._connect_websocket()

        msg = {
            "op": "subscribe",
            "args": topics,
        }
        await self._ws.send(json.dumps(msg))
        self._active_topics.update(topics)

    # -------------------------------------------------------------------------
    # Subscriptions
    # -------------------------------------------------------------------------

    async def subscribe_trades(
        self,
        symbols: List[str],
        callback: Optional[TradeCallback] = None
    ) -> None:
        """Subscribe to trade streams."""
        if callback:
            self._trade_callbacks.append(callback)

        topics = []
        skipped = 0
        for symbol in symbols:
            # Check if symbol exists on Bybit
            if symbol not in self._symbols:
                skipped += 1
                continue

            exchange_symbol = self.to_exchange_symbol(symbol)
            topic = f"publicTrade.{exchange_symbol}"
            topics.append(topic)
            self._subscriptions["trades"].add(symbol)

        if topics:
            await self._subscribe_topics(topics)
            self.logger.info("subscribed_trades", count=len(topics), skipped=skipped)

    async def subscribe_orderbook(
        self,
        symbols: List[str],
        callback: Optional[OrderBookCallback] = None,
        depth: int = 25
    ) -> None:
        """Subscribe to orderbook streams."""
        if callback:
            self._orderbook_callbacks.append(callback)

        # Bybit supports depths: 1, 50, 200, 500
        depth_level = 50 if depth <= 50 else 200

        topics = []
        skipped = 0
        for symbol in symbols:
            # Check if symbol exists on Bybit
            if symbol not in self._symbols:
                skipped += 1
                continue

            exchange_symbol = self.to_exchange_symbol(symbol)
            topic = f"orderbook.{depth_level}.{exchange_symbol}"
            topics.append(topic)
            self._subscriptions["orderbook"].add(symbol)

        if topics:
            await self._subscribe_topics(topics)
            self.logger.info("subscribed_orderbook", count=len(topics), skipped=skipped)

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        if not self._ws:
            return

        for symbol in symbols:
            self._subscriptions.get(stream_type, set()).discard(symbol)

        topics_to_remove = []
        for symbol in symbols:
            exchange_symbol = self.to_exchange_symbol(symbol)
            if stream_type == "trades":
                topics_to_remove.append(f"publicTrade.{exchange_symbol}")
            elif stream_type == "orderbook":
                for depth in [50, 200]:
                    topics_to_remove.append(f"orderbook.{depth}.{exchange_symbol}")

        if topics_to_remove:
            msg = {"op": "unsubscribe", "args": topics_to_remove}
            await self._ws.send(json.dumps(msg))
            for topic in topics_to_remove:
                self._active_topics.discard(topic)

    # -------------------------------------------------------------------------
    # REST API
    # -------------------------------------------------------------------------

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get 24h ticker."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/v5/market/tickers"
        params = {"category": "linear", "symbol": exchange_symbol}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Ticker request failed: {resp.status}")
                data = await resp.json()

        if data.get("retCode") != 0:
            raise Exception(f"Bybit API error: {data.get('retMsg')}")

        items = data.get("result", {}).get("list", [])
        if not items:
            raise Exception(f"No ticker data for {symbol}")

        item = items[0]

        return UnifiedTicker(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            last_price=self.to_decimal(item.get("lastPrice", "0")),
            bid_price=self.to_decimal(item.get("bid1Price", "0")),
            ask_price=self.to_decimal(item.get("ask1Price", "0")),
            high_24h=self.to_decimal(item.get("highPrice24h", "0")),
            low_24h=self.to_decimal(item.get("lowPrice24h", "0")),
            volume_24h=self.to_decimal(item.get("volume24h", "0")),
            quote_volume_24h=self.to_decimal(item.get("turnover24h", "0")),
            price_change_pct_24h=self.to_decimal(item.get("price24hPcnt", "0")) * 100,
            raw=item,
        )

    async def get_orderbook(self, symbol: str, limit: int = 25) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/v5/market/orderbook"
        params = {"category": "linear", "symbol": exchange_symbol, "limit": limit}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Orderbook request failed: {resp.status}")
                data = await resp.json()

        if data.get("retCode") != 0:
            raise Exception(f"Bybit API error: {data.get('retMsg')}")

        result = data.get("result", {})
        bids = [(self.to_decimal(p), self.to_decimal(q)) for p, q in result.get("b", [])]
        asks = [(self.to_decimal(p), self.to_decimal(q)) for p, q in result.get("a", [])]

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bids=bids,
            asks=asks,
            sequence=int(result.get("u", 0)),
            raw=result,
        )

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/v5/market/tickers"
        params = {"category": "linear", "symbol": exchange_symbol}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Funding request failed: {resp.status}")
                data = await resp.json()

        if data.get("retCode") != 0:
            raise Exception(f"Bybit API error: {data.get('retMsg')}")

        items = data.get("result", {}).get("list", [])
        if not items:
            raise Exception(f"No funding data for {symbol}")

        item = items[0]
        next_funding_ts = int(item.get("nextFundingTime", "0"))
        next_funding_time = self.parse_timestamp(next_funding_ts) if next_funding_ts else datetime.now(timezone.utc)

        return UnifiedFunding(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            rate=self.to_decimal(item.get("fundingRate", "0")),
            next_funding_time=next_funding_time,
            mark_price=self.to_decimal(item.get("markPrice", "0")),
            index_price=self.to_decimal(item.get("indexPrice", "0")),
            interval_hours=8,
            raw=item,
        )

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """Get current open interest."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/v5/market/open-interest"
        params = {"category": "linear", "symbol": exchange_symbol, "intervalTime": "5min", "limit": 1}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"OI request failed: {resp.status}")
                data = await resp.json()

        if data.get("retCode") != 0:
            raise Exception(f"Bybit API error: {data.get('retMsg')}")

        items = data.get("result", {}).get("list", [])
        if not items:
            raise Exception(f"No OI data for {symbol}")

        item = items[0]
        oi = self.to_decimal(item.get("openInterest", "0"))

        return UnifiedOpenInterest(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            open_interest=oi,
            open_interest_usd=None,  # Bybit returns in base currency
            market_type=MarketType.FUTURES_PERPETUAL,
            raw=item,
        )

    async def load_symbols(self) -> List[SymbolInfo]:
        """Load all trading symbols."""
        await self._load_exchange_info()
        return list(self._symbols.values())

    # -------------------------------------------------------------------------
    # Normalization
    # -------------------------------------------------------------------------

    def normalize_symbol(self, raw_symbol: str) -> str:
        """Normalize BTCUSDT -> BTC/USDT."""
        if raw_symbol in self._symbol_map:
            return self._symbol_map[raw_symbol]

        for quote in ["USDT", "USDC", "USD"]:
            if raw_symbol.endswith(quote):
                base = raw_symbol[:-len(quote)]
                return f"{base}/{quote}"

        return raw_symbol

    def to_exchange_symbol(self, unified_symbol: str) -> str:
        """Convert BTC/USDT -> BTCUSDT for Bybit."""
        # First check reverse map
        if unified_symbol in self._reverse_symbol_map:
            return self._reverse_symbol_map[unified_symbol]

        # Fallback: remove slash
        return unified_symbol.replace("/", "")

    def normalize_trade(self, raw: dict) -> UnifiedTrade:
        """Normalize trade for abstract method."""
        return self._normalize_trade(raw)

    def _normalize_trade(self, raw: dict) -> UnifiedTrade:
        """Normalize Bybit trade data."""
        exchange_symbol = raw.get("s", "")
        symbol = self.normalize_symbol(exchange_symbol)

        side_str = raw.get("S", "Buy")
        side = Side.BUY if side_str == "Buy" else Side.SELL

        return UnifiedTrade(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(int(raw.get("T", 0))),
            price=self.to_decimal(raw.get("p", "0")),
            quantity=self.to_decimal(raw.get("v", "0")),
            side=side,
            trade_id=str(raw.get("i", "")),
            raw=raw,
        )

    def normalize_orderbook(self, raw: dict) -> UnifiedOrderBook:
        """Normalize orderbook for abstract method."""
        return self._normalize_orderbook_ws(raw)

    def _normalize_orderbook_ws(self, data: dict) -> Optional[UnifiedOrderBook]:
        """Normalize WebSocket orderbook data."""
        topic = data.get("topic", "")
        # Extract symbol from topic: orderbook.50.BTCUSDT
        parts = topic.split(".")
        if len(parts) < 3:
            return None

        exchange_symbol = parts[-1]
        symbol = self.normalize_symbol(exchange_symbol)

        result = data.get("data", {})
        bids = [(self.to_decimal(p), self.to_decimal(q)) for p, q in result.get("b", [])]
        asks = [(self.to_decimal(p), self.to_decimal(q)) for p, q in result.get("a", [])]

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(int(data.get("ts", 0))),
            bids=bids,
            asks=asks,
            sequence=int(result.get("u", 0)),
            raw=data,
        )
