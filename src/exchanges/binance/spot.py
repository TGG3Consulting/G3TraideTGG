# -*- coding: utf-8 -*-
"""
Binance Spot Exchange Connector.

Implements BaseExchange interface for Binance Spot market.
Supports WebSocket streams for trades, orderbook, and klines.

WebSocket streams:
- wss://stream.binance.com:9443/stream
- Combined streams: symbol@trade, symbol@depth@100ms, symbol@kline_1m

REST endpoints:
- https://api.binance.com/api/v3/ticker/24hr
- https://api.binance.com/api/v3/depth
- https://api.binance.com/api/v3/klines
"""

from __future__ import annotations

import asyncio
import json
import re
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
    UnifiedKline,
    Side,
    MarketType,
    KlineInterval,
)
from src.exchanges.rate_limiter import RateLimiter, ExchangeRateLimits

logger = structlog.get_logger(__name__)


class BinanceSpotConnector(BaseExchange):
    """
    Binance Spot market connector.

    Implements real-time trade and orderbook streaming via WebSocket,
    plus REST endpoints for ticker and historical data.

    Usage:
        config = ExchangeConfig(
            name="binance",
            ws_url="wss://stream.binance.com:9443/stream",
            rest_url="https://api.binance.com",
        )
        connector = BinanceSpotConnector(config)
        await connector.connect()
        await connector.subscribe_trades(["BTC/USDT", "ETH/USDT"])
    """

    EXCHANGE_NAME = "binance"
    EXCHANGE_TYPE = ExchangeType.CEX
    CAPABILITIES = {
        ExchangeCapability.SPOT_TRADING,
        ExchangeCapability.TRADES_STREAM,
        ExchangeCapability.ORDERBOOK_STREAM,
        ExchangeCapability.KLINE_STREAM,
        ExchangeCapability.TICKER_STREAM,
        ExchangeCapability.HISTORICAL_KLINES,
        ExchangeCapability.HISTORICAL_TRADES,
    }

    # Default URLs
    DEFAULT_WS_URL = "wss://stream.binance.com:9443/stream"
    DEFAULT_REST_URL = "https://api.binance.com"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize Binance Spot connector."""
        if config is None:
            config = ExchangeConfig(
                name="binance",
                ws_url=self.DEFAULT_WS_URL,
                rest_url=self.DEFAULT_REST_URL,
            )

        super().__init__(config)

        self._ws_url = config.ws_url or self.DEFAULT_WS_URL
        self._rest_url = config.rest_url or self.DEFAULT_REST_URL

        # Rate limiter
        self._rate_limiter = RateLimiter(
            ExchangeRateLimits.BINANCE,
            name="binance_spot"
        )

        # WebSocket state
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._reconnect_count = 0

        # HTTP session
        self._http_session: Optional[aiohttp.ClientSession] = None

        # Stream management
        self._active_streams: Set[str] = set()
        self._max_streams = 200  # Binance limit

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to Binance Spot."""
        if websockets is None:
            raise ImportError("websockets package required: pip install websockets")

        self._state = ConnectionState.CONNECTING
        self.logger.info("connecting")

        try:
            # Load exchange info and symbols
            await self._load_exchange_info()

            self._state = ConnectionState.CONNECTED
            self.logger.info("connected", symbols=len(self._symbols))

        except Exception as e:
            self._state = ConnectionState.ERROR
            self.logger.error("connect_failed", error=str(e))
            raise

    async def disconnect(self) -> None:
        """Disconnect from Binance Spot."""
        self._state = ConnectionState.CLOSING
        self.logger.info("disconnecting")

        # Cancel WebSocket task
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        # Close WebSocket
        if self._ws:
            await self._ws.close()
            self._ws = None

        # Close HTTP session
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

        self._active_streams.clear()
        self._state = ConnectionState.DISCONNECTED
        self.logger.info("disconnected")

    async def _load_exchange_info(self) -> None:
        """Load exchange info and build symbol mappings."""
        url = f"{self._rest_url}/api/v3/exchangeInfo"

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"Exchange info failed: {resp.status}")
                data = await resp.json()

        for s in data.get("symbols", []):
            if s.get("status") != "TRADING":
                continue

            base = s["baseAsset"]
            quote = s["quoteAsset"]
            exchange_symbol = s["symbol"]
            unified_symbol = f"{base}/{quote}"

            # Parse precision
            price_precision = 8
            qty_precision = 8
            tick_size = Decimal("0.00000001")
            step_size = Decimal("0.00000001")
            min_notional = Decimal("10")

            for f in s.get("filters", []):
                if f["filterType"] == "PRICE_FILTER":
                    tick_size = Decimal(f["tickSize"])
                    price_precision = abs(tick_size.as_tuple().exponent)
                elif f["filterType"] == "LOT_SIZE":
                    step_size = Decimal(f["stepSize"])
                    qty_precision = abs(step_size.as_tuple().exponent)
                elif f["filterType"] == "NOTIONAL":
                    min_notional = Decimal(f.get("minNotional", "10"))

            info = SymbolInfo(
                exchange=self.EXCHANGE_NAME,
                symbol_unified=unified_symbol,
                symbol_exchange=exchange_symbol,
                base_asset=base,
                quote_asset=quote,
                market_type=MarketType.SPOT,
                price_precision=price_precision,
                quantity_precision=qty_precision,
                min_quantity=step_size,
                min_notional=min_notional,
                tick_size=tick_size,
                step_size=step_size,
            )

            self._symbols[unified_symbol] = info
            self._symbol_map[exchange_symbol] = unified_symbol
            self._reverse_symbol_map[unified_symbol] = exchange_symbol

    # -------------------------------------------------------------------------
    # HTTP Session
    # -------------------------------------------------------------------------

    async def _get_http_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if aiohttp is None:
            raise ImportError("aiohttp package required: pip install aiohttp")

        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._http_session = aiohttp.ClientSession(timeout=timeout)
        return self._http_session

    # -------------------------------------------------------------------------
    # WebSocket Management
    # -------------------------------------------------------------------------

    async def _connect_websocket(self, streams: List[str]) -> None:
        """Connect WebSocket with specified streams."""
        if not streams:
            return

        # Build URL
        streams_str = "/".join(streams)
        url = f"{self._ws_url}?streams={streams_str}"

        self.logger.debug("ws_connecting", streams=len(streams))

        try:
            self._ws = await websockets.connect(
                url,
                ping_interval=self.config.ping_interval,
                ping_timeout=self.config.ping_timeout,
            )
            self._active_streams.update(streams)
            self._reconnect_count = 0

            self.logger.info("ws_connected", streams=len(streams))

            # Start message handler
            self._ws_task = asyncio.create_task(self._ws_message_loop())

        except Exception as e:
            self.logger.error("ws_connect_failed", error=str(e))
            raise

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
        stream = data.get("stream", "")
        payload = data.get("data", {})

        if not stream or not payload:
            return

        if "@trade" in stream:
            trade = self.normalize_trade(payload)
            self._emit_trade(trade)

        elif "@depth" in stream:
            # Extract symbol from stream name
            symbol_lower = stream.split("@")[0]
            orderbook = self._normalize_depth_update(payload, symbol_lower)
            if orderbook:
                self._emit_orderbook(orderbook)

        elif "@kline" in stream:
            kline = self._normalize_kline(payload)
            if kline:
                for callback in self._kline_callbacks:
                    try:
                        callback(kline)
                    except Exception as e:
                        self.logger.error("kline_callback_error", error=str(e))

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

        # Reconnect with same streams
        if self._active_streams:
            streams = list(self._active_streams)
            self._active_streams.clear()
            try:
                await self._connect_websocket(streams)
            except Exception as e:
                self.logger.error("reconnect_failed", error=str(e))
                await self._handle_reconnect()

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

        streams = []
        for symbol in symbols:
            exchange_symbol = self.to_exchange_symbol(symbol)
            stream = f"{exchange_symbol.lower()}@trade"
            streams.append(stream)
            self._subscriptions["trades"].add(symbol)

        # Connect or add to existing connection
        if self._ws is None:
            await self._connect_websocket(streams)
        else:
            # CONN-1 FIX: Только если есть НОВЫЕ streams
            new_streams = [s for s in streams if s not in self._active_streams]
            if new_streams:
                all_streams = list(self._active_streams) + new_streams
                await self._ws.close()
                await self._connect_websocket(all_streams)

    async def subscribe_orderbook(
        self,
        symbols: List[str],
        callback: Optional[OrderBookCallback] = None,
        depth: int = 20
    ) -> None:
        """Subscribe to orderbook depth streams."""
        if callback:
            self._orderbook_callbacks.append(callback)

        streams = []
        for symbol in symbols:
            exchange_symbol = self.to_exchange_symbol(symbol)
            stream = f"{exchange_symbol.lower()}@depth@100ms"
            streams.append(stream)
            self._subscriptions["orderbook"].add(symbol)

        if self._ws is None:
            await self._connect_websocket(streams)
        else:
            # CONN-1 FIX: Только если есть НОВЫЕ streams
            new_streams = [s for s in streams if s not in self._active_streams]
            if new_streams:
                all_streams = list(self._active_streams) + new_streams
                await self._ws.close()
                await self._connect_websocket(all_streams)

    async def subscribe_klines(
        self,
        symbols: List[str],
        interval: KlineInterval = KlineInterval.M1,
        callback=None
    ) -> None:
        """Subscribe to kline streams."""
        if callback:
            self._kline_callbacks.append(callback)

        streams = []
        for symbol in symbols:
            exchange_symbol = self.to_exchange_symbol(symbol)
            stream = f"{exchange_symbol.lower()}@kline_{interval.value}"
            streams.append(stream)
            self._subscriptions["kline"].add(symbol)

        if self._ws is None:
            await self._connect_websocket(streams)
        else:
            all_streams = list(self._active_streams) + streams
            await self._ws.close()
            await self._connect_websocket(all_streams)

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        for symbol in symbols:
            self._subscriptions.get(stream_type, set()).discard(symbol)

        # Rebuild streams and reconnect
        streams = []
        for symbol in self._subscriptions.get("trades", set()):
            exchange_symbol = self.to_exchange_symbol(symbol)
            streams.append(f"{exchange_symbol.lower()}@trade")

        for symbol in self._subscriptions.get("orderbook", set()):
            exchange_symbol = self.to_exchange_symbol(symbol)
            streams.append(f"{exchange_symbol.lower()}@depth@100ms")

        if self._ws:
            await self._ws.close()

        if streams:
            await self._connect_websocket(streams)

    # -------------------------------------------------------------------------
    # REST API Methods
    # -------------------------------------------------------------------------

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get 24h ticker for symbol."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/api/v3/ticker/24hr"
        params = {"symbol": exchange_symbol}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Ticker request failed: {resp.status}")
                data = await resp.json()

        return self._normalize_ticker(data, symbol)

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/api/v3/depth"
        params = {"symbol": exchange_symbol, "limit": limit}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Orderbook request failed: {resp.status}")
                data = await resp.json()

        return self._normalize_orderbook_snapshot(data, symbol)

    async def get_historical_klines(
        self,
        symbol: str,
        interval: KlineInterval,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 500
    ) -> List[UnifiedKline]:
        """Get historical klines."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/api/v3/klines"
        params = {
            "symbol": exchange_symbol,
            "interval": interval.value,
            "limit": limit,
        }

        if start_time:
            params["startTime"] = int(start_time.timestamp() * 1000)
        if end_time:
            params["endTime"] = int(end_time.timestamp() * 1000)

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Klines request failed: {resp.status}")
                data = await resp.json()

        klines = []
        for k in data:
            klines.append(UnifiedKline(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                interval=interval,
                open_time=self.parse_timestamp(k[0]),
                close_time=self.parse_timestamp(k[6]),
                open=self.to_decimal(k[1]),
                high=self.to_decimal(k[2]),
                low=self.to_decimal(k[3]),
                close=self.to_decimal(k[4]),
                volume=self.to_decimal(k[5]),
                quote_volume=self.to_decimal(k[7]),
                trades=int(k[8]),
                is_closed=True,
            ))

        return klines

    async def load_symbols(self) -> List[SymbolInfo]:
        """Load all trading symbols."""
        await self._load_exchange_info()
        return list(self._symbols.values())

    # -------------------------------------------------------------------------
    # Normalization
    # -------------------------------------------------------------------------

    def normalize_symbol(self, raw_symbol: str) -> str:
        """Normalize BTCUSDT -> BTC/USDT."""
        # Check cache first
        if raw_symbol in self._symbol_map:
            return self._symbol_map[raw_symbol]

        # Try to split known quote assets
        for quote in ["USDT", "USDC", "BUSD", "BTC", "ETH", "BNB"]:
            if raw_symbol.endswith(quote):
                base = raw_symbol[:-len(quote)]
                return f"{base}/{quote}"

        return raw_symbol

    def normalize_trade(self, raw: dict) -> UnifiedTrade:
        """Normalize Binance trade to UnifiedTrade."""
        exchange_symbol = raw.get("s", "")
        symbol = self.normalize_symbol(exchange_symbol)

        return UnifiedTrade(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(raw["T"]),
            price=self.to_decimal(raw["p"]),
            quantity=self.to_decimal(raw["q"]),
            side=Side.SELL if raw.get("m", False) else Side.BUY,
            trade_id=str(raw.get("t", "")),
            is_maker=raw.get("m", False),
            raw=raw,
        )

    def normalize_orderbook(self, raw: dict) -> UnifiedOrderBook:
        """Normalize orderbook from REST API."""
        return self._normalize_orderbook_snapshot(raw, "")

    def _normalize_orderbook_snapshot(
        self,
        raw: dict,
        symbol: str
    ) -> UnifiedOrderBook:
        """Normalize REST orderbook snapshot."""
        bids = [
            (self.to_decimal(p), self.to_decimal(q))
            for p, q in raw.get("bids", [])
        ]
        asks = [
            (self.to_decimal(p), self.to_decimal(q))
            for p, q in raw.get("asks", [])
        ]

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bids=bids,
            asks=asks,
            sequence=raw.get("lastUpdateId"),
            raw=raw,
        )

    def _normalize_depth_update(
        self,
        raw: dict,
        symbol_lower: str
    ) -> Optional[UnifiedOrderBook]:
        """Normalize WebSocket depth update."""
        exchange_symbol = raw.get("s", symbol_lower.upper())
        symbol = self.normalize_symbol(exchange_symbol)

        bids = [
            (self.to_decimal(p), self.to_decimal(q))
            for p, q in raw.get("b", [])
        ]
        asks = [
            (self.to_decimal(p), self.to_decimal(q))
            for p, q in raw.get("a", [])
        ]

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(raw.get("E", 0)),
            bids=bids,
            asks=asks,
            sequence=raw.get("u"),
            raw=raw,
        )

    def _normalize_ticker(self, raw: dict, symbol: str) -> UnifiedTicker:
        """Normalize ticker response."""
        return UnifiedTicker(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            last_price=self.to_decimal(raw.get("lastPrice", "0")),
            bid_price=self.to_decimal(raw.get("bidPrice", "0")),
            ask_price=self.to_decimal(raw.get("askPrice", "0")),
            high_24h=self.to_decimal(raw.get("highPrice", "0")),
            low_24h=self.to_decimal(raw.get("lowPrice", "0")),
            volume_24h=self.to_decimal(raw.get("volume", "0")),
            quote_volume_24h=self.to_decimal(raw.get("quoteVolume", "0")),
            price_change_24h=self.to_decimal(raw.get("priceChange", "0")),
            price_change_pct_24h=self.to_decimal(raw.get("priceChangePercent", "0")),
            trades_24h=int(raw.get("count", 0)),
            raw=raw,
        )

    def _normalize_kline(self, raw: dict) -> Optional[UnifiedKline]:
        """Normalize WebSocket kline."""
        k = raw.get("k", {})
        if not k:
            return None

        exchange_symbol = raw.get("s", "")
        symbol = self.normalize_symbol(exchange_symbol)

        interval_str = k.get("i", "1m")
        try:
            interval = KlineInterval(interval_str)
        except ValueError:
            interval = KlineInterval.M1

        return UnifiedKline(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            interval=interval,
            open_time=self.parse_timestamp(k["t"]),
            close_time=self.parse_timestamp(k["T"]),
            open=self.to_decimal(k["o"]),
            high=self.to_decimal(k["h"]),
            low=self.to_decimal(k["l"]),
            close=self.to_decimal(k["c"]),
            volume=self.to_decimal(k["v"]),
            quote_volume=self.to_decimal(k["q"]),
            trades=int(k.get("n", 0)),
            is_closed=k.get("x", False),
            raw=raw,
        )
