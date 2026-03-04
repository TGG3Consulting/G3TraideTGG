# -*- coding: utf-8 -*-
"""
AsterDEX Exchange Connector.

AsterDEX is a perpetual futures DEX on BNB Smart Chain.
API follows Binance-compatible format after APX Finance merger.

API Documentation: https://docs.asterdex.com/product/aster-perpetuals/api/api-documentation
GitHub: https://github.com/asterdex/api-docs

Endpoints:
- REST: https://fapi.asterdex.com
- WebSocket: wss://fstream.asterdex.com

Rate Limits:
- REST: 1200 requests/minute
- WebSocket: 10 messages/second
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Set

import aiohttp
import structlog

from src.exchanges.base import (
    BaseExchange,
    ConnectionState,
    ExchangeCapability,
    ExchangeConfig,
    ExchangeType,
    OrderBookCallback,
    SymbolInfo,
    TradeCallback,
)
from src.exchanges.models import (
    KlineInterval,
    MarketType,
    OrderBookLevel,
    Side,  # CONN-6 FIX
    UnifiedFunding,
    UnifiedKline,
    UnifiedOpenInterest,
    UnifiedOrderBook,
    UnifiedTicker,
    UnifiedTrade,
)

logger = structlog.get_logger(__name__)


class AsterDEXConnector(BaseExchange):
    """
    AsterDEX perpetual futures connector.

    Binance-compatible API for perpetual futures on BNB Chain.
    Supports trades, orderbook, funding rates via REST and WebSocket.
    """

    EXCHANGE_NAME = "asterdex"
    EXCHANGE_TYPE = ExchangeType.DEX
    CAPABILITIES = {
        ExchangeCapability.FUTURES_PERPETUAL,
        ExchangeCapability.TRADES_STREAM,
        ExchangeCapability.ORDERBOOK_STREAM,
        ExchangeCapability.TICKER_STREAM,
        ExchangeCapability.FUNDING_RATE,
        ExchangeCapability.OPEN_INTEREST,
        ExchangeCapability.HISTORICAL_TRADES,
        ExchangeCapability.HISTORICAL_KLINES,
    }

    # API URLs
    REST_URL = "https://fapi.asterdex.com"
    WS_URL = "wss://fstream.asterdex.com/stream"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize AsterDEX connector."""
        if config is None:
            config = ExchangeConfig(
                name="asterdex",
                ws_url=self.WS_URL,
                rest_url=self.REST_URL,
            )
        super().__init__(config)
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._subscribed_streams: Set[str] = set()
        self._stream_id: int = 1

    # =========================================================================
    # CONNECTION MANAGEMENT
    # =========================================================================

    async def connect(self) -> None:
        """Connect to AsterDEX REST and WebSocket APIs."""
        self._state = ConnectionState.CONNECTING
        self.logger.info("connecting_to_asterdex")

        try:
            # Create HTTP session
            timeout = aiohttp.ClientTimeout(total=self.config.request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)

            # Test REST API connectivity
            await self._test_connection()

            # Load symbols
            await self.load_symbols()

            # Connect WebSocket
            await self._connect_websocket()

            self._state = ConnectionState.CONNECTED
            self.logger.info(
                "asterdex_connected",
                symbols=len(self._symbols)
            )

        except Exception as e:
            self._state = ConnectionState.ERROR
            self.logger.error("asterdex_connect_failed", error=str(e))
            raise

    async def disconnect(self) -> None:
        """Disconnect from AsterDEX."""
        self._state = ConnectionState.CLOSING
        self.logger.info("disconnecting_from_asterdex")

        # Cancel tasks
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        # Close WebSocket
        if self._ws and not self._ws.closed:
            await self._ws.close()

        # Close HTTP session
        if self._session and not self._session.closed:
            await self._session.close()

        self._state = ConnectionState.DISCONNECTED
        self.logger.info("asterdex_disconnected")

    async def _test_connection(self) -> None:
        """Test REST API connectivity."""
        url = f"{self.REST_URL}/fapi/v1/ping"
        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise ConnectionError(f"AsterDEX ping failed: {resp.status}")

    async def _connect_websocket(self) -> None:
        """Connect to WebSocket stream."""
        try:
            self._ws = await self._session.ws_connect(
                self.WS_URL,
                heartbeat=self.config.ping_interval,
            )
            self._ws_task = asyncio.create_task(self._ws_handler())
            self._ping_task = asyncio.create_task(self._ws_ping_loop())
            self.logger.info("asterdex_ws_connected")
        except Exception as e:
            self.logger.error("asterdex_ws_connect_failed", error=str(e))
            raise

    async def _ws_handler(self) -> None:
        """Handle incoming WebSocket messages."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_ws_message(json.loads(msg.data))
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.logger.error("ws_error", error=str(self._ws.exception()))
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    self.logger.warning("ws_closed")
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error("ws_handler_error", error=str(e))
            if self._state == ConnectionState.CONNECTED:
                asyncio.create_task(self.reconnect())

    async def _ws_ping_loop(self) -> None:
        """Send periodic pings to keep connection alive."""
        try:
            while self._state == ConnectionState.CONNECTED:
                await asyncio.sleep(self.config.ping_interval)
                if self._ws and not self._ws.closed:
                    await self._ws.pong()
        except asyncio.CancelledError:
            pass

    async def _handle_ws_message(self, data: Dict[str, Any]) -> None:
        """Process WebSocket message."""
        if "stream" not in data or "data" not in data:
            return

        stream = data["stream"]
        payload = data["data"]

        # Aggregate trade stream
        if "@aggTrade" in stream:
            trade = self.normalize_trade(payload)
            self._emit_trade(trade)

        # Depth stream
        elif "@depth" in stream:
            orderbook = self._normalize_depth_update(payload)
            self._emit_orderbook(orderbook)

        # Ticker stream
        elif "@ticker" in stream:
            ticker = self.normalize_ticker(payload)
            self._emit_ticker(ticker)

    # =========================================================================
    # SYMBOL MANAGEMENT
    # =========================================================================

    async def load_symbols(self) -> List[SymbolInfo]:
        """Load all available trading symbols."""
        url = f"{self.REST_URL}/fapi/v1/exchangeInfo"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to load symbols: {resp.status}")
            data = await resp.json()

        symbols = []
        for s in data.get("symbols", []):
            if s.get("status") != "TRADING":
                continue

            symbol_unified = self.normalize_symbol(s["symbol"])

            # Extract filter values
            min_qty = Decimal("0.001")
            tick_size = Decimal("0.01")
            for f in s.get("filters", []):
                if f.get("filterType") == "LOT_SIZE":
                    min_qty = Decimal(str(f.get("minQty", "0.001")))
                elif f.get("filterType") == "PRICE_FILTER":
                    tick_size = Decimal(str(f.get("tickSize", "0.01")))

            info = SymbolInfo(
                exchange=self.EXCHANGE_NAME,
                symbol_unified=symbol_unified,
                symbol_exchange=s["symbol"],
                base_asset=s.get("baseAsset", ""),
                quote_asset=s.get("quoteAsset", ""),
                market_type=MarketType.FUTURES_PERPETUAL,
                price_precision=s.get("pricePrecision", 8),
                quantity_precision=s.get("quantityPrecision", 8),
                min_quantity=min_qty,
                min_notional=Decimal("10"),
                tick_size=tick_size,
                step_size=Decimal(str(10 ** -s.get("quantityPrecision", 8))),
            )
            symbols.append(info)
            self._symbols[symbol_unified] = info
            self._symbol_map[s["symbol"]] = symbol_unified
            self._reverse_symbol_map[symbol_unified] = s["symbol"]

        self.logger.info("symbols_loaded", count=len(symbols))
        return symbols

    def normalize_symbol(self, raw_symbol: str) -> str:
        """Convert BTCUSDT to BTC/USDT format."""
        for quote in ["USDT", "USDC", "BUSD", "BNB"]:
            if raw_symbol.endswith(quote):
                base = raw_symbol[:-len(quote)]
                return f"{base}/{quote}"
        return raw_symbol

    # =========================================================================
    # SUBSCRIPTIONS
    # =========================================================================

    async def subscribe_trades(
        self,
        symbols: List[str],
        callback: Optional[TradeCallback] = None
    ) -> None:
        """Subscribe to trade streams."""
        if callback:
            self.on_trade(callback)

        streams = []
        for symbol in symbols:
            exchange_symbol = self.to_exchange_symbol(symbol).lower()
            stream = f"{exchange_symbol}@aggTrade"
            streams.append(stream)
            self._subscriptions["trades"].add(symbol)

        await self._subscribe_streams(streams)

    async def subscribe_orderbook(
        self,
        symbols: List[str],
        callback: Optional[OrderBookCallback] = None,
        depth: int = 20
    ) -> None:
        """Subscribe to orderbook streams."""
        if callback:
            self.on_orderbook(callback)

        streams = []
        for symbol in symbols:
            exchange_symbol = self.to_exchange_symbol(symbol).lower()
            stream = f"{exchange_symbol}@depth@100ms"
            streams.append(stream)
            self._subscriptions["orderbook"].add(symbol)

        await self._subscribe_streams(streams)

    async def subscribe_ticker(
        self,
        symbols: List[str],
        callback: Optional[Callable] = None
    ) -> None:
        """Subscribe to ticker streams."""
        if callback:
            self.on_ticker(callback)

        streams = []
        for symbol in symbols:
            exchange_symbol = self.to_exchange_symbol(symbol).lower()
            stream = f"{exchange_symbol}@ticker"
            streams.append(stream)
            self._subscriptions["ticker"].add(symbol)

        await self._subscribe_streams(streams)

    async def _subscribe_streams(self, streams: List[str]) -> None:
        """Subscribe to WebSocket streams."""
        if not self._ws or self._ws.closed:
            self.logger.warning("ws_not_connected_for_subscribe")
            return

        msg = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": self._stream_id,
        }
        self._stream_id += 1

        await self._ws.send_json(msg)
        self._subscribed_streams.update(streams)
        self.logger.debug("subscribed_streams", streams=streams)

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        if not self._ws or self._ws.closed:
            return

        streams = []
        for symbol in symbols:
            exchange_symbol = self.to_exchange_symbol(symbol).lower()
            if stream_type == "trades":
                streams.append(f"{exchange_symbol}@aggTrade")
            elif stream_type == "orderbook":
                streams.append(f"{exchange_symbol}@depth@100ms")
            elif stream_type == "ticker":
                streams.append(f"{exchange_symbol}@ticker")

        msg = {
            "method": "UNSUBSCRIBE",
            "params": streams,
            "id": self._stream_id,
        }
        self._stream_id += 1

        await self._ws.send_json(msg)
        for stream in streams:
            self._subscribed_streams.discard(stream)

    # =========================================================================
    # REST API METHODS
    # =========================================================================

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get 24hr ticker data."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self.REST_URL}/fapi/v1/ticker/24hr"

        async with self._session.get(url, params={"symbol": exchange_symbol}) as resp:
            if resp.status != 200:
                raise Exception(f"Ticker request failed: {resp.status}")
            data = await resp.json()

        return self.normalize_ticker(data)

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self.REST_URL}/fapi/v1/depth"

        async with self._session.get(
            url,
            params={"symbol": exchange_symbol, "limit": limit}
        ) as resp:
            if resp.status != 200:
                raise Exception(f"Orderbook request failed: {resp.status}")
            data = await resp.json()

        return self.normalize_orderbook(data, symbol)

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self.REST_URL}/fapi/v1/premiumIndex"

        async with self._session.get(url, params={"symbol": exchange_symbol}) as resp:
            if resp.status != 200:
                raise Exception(f"Funding rate request failed: {resp.status}")
            data = await resp.json()

        return self.normalize_funding(data)

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """Get open interest."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self.REST_URL}/fapi/v1/openInterest"

        async with self._session.get(url, params={"symbol": exchange_symbol}) as resp:
            if resp.status != 200:
                raise Exception(f"Open interest request failed: {resp.status}")
            data = await resp.json()

        # Get current price for USD conversion
        ticker = await self.get_ticker(symbol)
        oi_value = Decimal(str(data.get("openInterest", "0")))
        oi_usd = oi_value * ticker.last_price

        return UnifiedOpenInterest(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(data.get("time", int(time.time() * 1000))),
            open_interest=oi_value,
            open_interest_usd=oi_usd,
        )

    async def get_recent_trades(self, symbol: str, limit: int = 500) -> List[UnifiedTrade]:
        """Get recent trades."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self.REST_URL}/fapi/v1/trades"

        async with self._session.get(
            url,
            params={"symbol": exchange_symbol, "limit": limit}
        ) as resp:
            if resp.status != 200:
                raise Exception(f"Trades request failed: {resp.status}")
            data = await resp.json()

        return [self._normalize_rest_trade(t, symbol) for t in data]

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
        url = f"{self.REST_URL}/fapi/v1/klines"

        params = {
            "symbol": exchange_symbol,
            "interval": interval.value,
            "limit": limit,
        }
        if start_time:
            params["startTime"] = int(start_time.timestamp() * 1000)
        if end_time:
            params["endTime"] = int(end_time.timestamp() * 1000)

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Klines request failed: {resp.status}")
            data = await resp.json()

        return [self._normalize_kline(k, symbol, interval) for k in data]

    # =========================================================================
    # DATA NORMALIZATION
    # =========================================================================

    def normalize_trade(self, raw: Dict[str, Any]) -> UnifiedTrade:
        """Normalize WebSocket aggTrade message."""
        symbol = self.to_unified_symbol(raw.get("s", ""))
        return UnifiedTrade(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            trade_id=str(raw.get("a", "")),
            timestamp=self.parse_timestamp(raw.get("T", 0)),
            price=Decimal(str(raw.get("p", "0"))),
            quantity=Decimal(str(raw.get("q", "0"))),
            side=Side.SELL if raw.get("m", False) else Side.BUY,  # CONN-6 FIX
            is_maker=raw.get("m", False),
        )

    def _normalize_rest_trade(self, raw: Dict[str, Any], symbol: str) -> UnifiedTrade:
        """Normalize REST trade response."""
        return UnifiedTrade(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            trade_id=str(raw.get("id", "")),
            timestamp=self.parse_timestamp(raw.get("time", 0)),
            price=Decimal(str(raw.get("price", "0"))),
            quantity=Decimal(str(raw.get("qty", "0"))),
            side=Side.SELL if raw.get("isBuyerMaker", False) else Side.BUY,  # CONN-6 FIX
            is_maker=raw.get("isBuyerMaker", False),
        )

    def normalize_orderbook(self, raw: Dict[str, Any], symbol: str = "") -> UnifiedOrderBook:
        """Normalize orderbook snapshot."""
        if not symbol:
            symbol = self.to_unified_symbol(raw.get("s", "UNKNOWN"))

        bids = [
            OrderBookLevel(
                price=Decimal(str(b[0])),
                quantity=Decimal(str(b[1]))
            )
            for b in raw.get("bids", [])
        ]
        asks = [
            OrderBookLevel(
                price=Decimal(str(a[0])),
                quantity=Decimal(str(a[1]))
            )
            for a in raw.get("asks", [])
        ]

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bids=bids,
            asks=asks,
            sequence=raw.get("lastUpdateId"),
        )

    def _normalize_depth_update(self, raw: Dict[str, Any]) -> UnifiedOrderBook:
        """Normalize depth stream update."""
        symbol = self.to_unified_symbol(raw.get("s", ""))
        return self.normalize_orderbook(raw, symbol)

    def normalize_ticker(self, raw: Dict[str, Any]) -> UnifiedTicker:
        """Normalize ticker data."""
        symbol = self.to_unified_symbol(raw.get("symbol", raw.get("s", "")))

        return UnifiedTicker(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(raw.get("closeTime", raw.get("E", int(time.time() * 1000)))),
            last_price=Decimal(str(raw.get("lastPrice", raw.get("c", "0")))),
            bid_price=Decimal(str(raw.get("bidPrice", raw.get("b", "0")))),
            ask_price=Decimal(str(raw.get("askPrice", raw.get("a", "0")))),
            high_24h=Decimal(str(raw.get("highPrice", raw.get("h", "0")))),
            low_24h=Decimal(str(raw.get("lowPrice", raw.get("l", "0")))),
            volume_24h=Decimal(str(raw.get("volume", raw.get("v", "0")))),
            volume_24h_quote=Decimal(str(raw.get("quoteVolume", raw.get("q", "0")))),
            price_change_24h=Decimal(str(raw.get("priceChange", raw.get("p", "0")))),
            price_change_pct_24h=Decimal(str(raw.get("priceChangePercent", raw.get("P", "0")))),
        )

    def normalize_funding(self, raw: Dict[str, Any]) -> UnifiedFunding:
        """Normalize funding rate data."""
        symbol = self.to_unified_symbol(raw.get("symbol", ""))

        next_funding = None
        if raw.get("nextFundingTime"):
            next_funding = self.parse_timestamp(raw["nextFundingTime"])

        return UnifiedFunding(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(raw.get("time", int(time.time() * 1000))),
            funding_rate=Decimal(str(raw.get("lastFundingRate", "0"))),
            next_funding_time=next_funding,
            mark_price=Decimal(str(raw.get("markPrice", "0"))),
            index_price=Decimal(str(raw.get("indexPrice", "0"))),
        )

    def _normalize_kline(
        self,
        raw: List[Any],
        symbol: str,
        interval: KlineInterval
    ) -> UnifiedKline:
        """Normalize kline data."""
        return UnifiedKline(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            interval=interval,
            open_time=self.parse_timestamp(raw[0]),
            close_time=self.parse_timestamp(raw[6]),
            open_price=Decimal(str(raw[1])),
            high_price=Decimal(str(raw[2])),
            low_price=Decimal(str(raw[3])),
            close_price=Decimal(str(raw[4])),
            volume=Decimal(str(raw[5])),
            quote_volume=Decimal(str(raw[7])),
            trades_count=int(raw[8]) if len(raw) > 8 else 0,
            is_closed=True,
        )
