# -*- coding: utf-8 -*-
"""
Binance Futures Exchange Connector.

Implements BaseExchange interface for Binance USDT-M Perpetual Futures.
Provides Open Interest, Funding Rate, and Long/Short Ratio data.

WebSocket streams:
- wss://fstream.binance.com/stream
- Streams: symbol@trade, symbol@depth@100ms, symbol@markPrice

REST endpoints:
- https://fapi.binance.com/fapi/v1/openInterest
- https://fapi.binance.com/fapi/v1/premiumIndex
- https://fapi.binance.com/futures/data/globalLongShortAccountRatio
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Set

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
    FundingCallback,
)
from src.exchanges.models import (
    UnifiedTrade,
    UnifiedOrderBook,
    UnifiedTicker,
    UnifiedFunding,
    UnifiedOpenInterest,
    UnifiedLiquidation,
    Side,
    MarketType,
    LiquidationType,
)
from src.exchanges.rate_limiter import RateLimiter, ExchangeRateLimits

logger = structlog.get_logger(__name__)


class BinanceFuturesConnector(BaseExchange):
    """
    Binance USDT-M Perpetual Futures connector.

    Provides:
    - Real-time trades and orderbook via WebSocket
    - Open Interest data via REST
    - Funding Rate data via REST
    - Long/Short Ratio via REST

    Usage:
        config = ExchangeConfig(
            name="binance_futures",
            ws_url="wss://fstream.binance.com/stream",
            rest_url="https://fapi.binance.com",
        )
        connector = BinanceFuturesConnector(config)
        await connector.connect()

        # Get futures-specific data
        funding = await connector.get_funding_rate("BTC/USDT")
        oi = await connector.get_open_interest("BTC/USDT")
    """

    EXCHANGE_NAME = "binance_futures"
    EXCHANGE_TYPE = ExchangeType.CEX
    CAPABILITIES = {
        ExchangeCapability.FUTURES_PERPETUAL,
        ExchangeCapability.TRADES_STREAM,
        ExchangeCapability.ORDERBOOK_STREAM,
        ExchangeCapability.LIQUIDATION_STREAM,
        ExchangeCapability.FUNDING_RATE,
        ExchangeCapability.OPEN_INTEREST,
        ExchangeCapability.LONG_SHORT_RATIO,
        ExchangeCapability.HISTORICAL_FUNDING,
        ExchangeCapability.HISTORICAL_OI,
    }

    # Default URLs
    DEFAULT_WS_URL = "wss://fstream.binance.com/stream"
    DEFAULT_REST_URL = "https://fapi.binance.com"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize Binance Futures connector."""
        if config is None:
            config = ExchangeConfig(
                name="binance_futures",
                ws_url=self.DEFAULT_WS_URL,
                rest_url=self.DEFAULT_REST_URL,
            )

        super().__init__(config)

        self._ws_url = config.ws_futures_url or config.ws_url or self.DEFAULT_WS_URL
        self._rest_url = config.rest_futures_url or config.rest_url or self.DEFAULT_REST_URL

        # Rate limiter
        self._rate_limiter = RateLimiter(
            ExchangeRateLimits.BINANCE_FUTURES,
            name="binance_futures"
        )

        # WebSocket
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._reconnect_count = 0

        # HTTP session
        self._http_session: Optional[aiohttp.ClientSession] = None

        # Streams
        self._active_streams: Set[str] = set()
        self._max_streams = 200

        # Funding callback
        self._funding_callbacks: List[Callable] = []

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to Binance Futures."""
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
        """Disconnect from Binance Futures."""
        self._state = ConnectionState.CLOSING
        self.logger.info("disconnecting")

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

        self._active_streams.clear()
        self._state = ConnectionState.DISCONNECTED
        self.logger.info("disconnected")

    async def _load_exchange_info(self) -> None:
        """Load futures exchange info."""
        url = f"{self._rest_url}/fapi/v1/exchangeInfo"

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"Exchange info failed: {resp.status}")
                data = await resp.json()

        for s in data.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if s.get("contractType") != "PERPETUAL":
                continue

            base = s["baseAsset"]
            quote = s["quoteAsset"]
            exchange_symbol = s["symbol"]
            unified_symbol = f"{base}/{quote}"

            # Parse precision
            price_precision = int(s.get("pricePrecision", 8))
            qty_precision = int(s.get("quantityPrecision", 8))

            tick_size = Decimal("0.00000001")
            step_size = Decimal("0.00000001")
            min_notional = Decimal("5")

            for f in s.get("filters", []):
                if f["filterType"] == "PRICE_FILTER":
                    tick_size = Decimal(f["tickSize"])
                elif f["filterType"] == "LOT_SIZE":
                    step_size = Decimal(f["stepSize"])
                elif f["filterType"] == "MIN_NOTIONAL":
                    min_notional = Decimal(f.get("notional", "5"))

            info = SymbolInfo(
                exchange=self.EXCHANGE_NAME,
                symbol_unified=unified_symbol,
                symbol_exchange=exchange_symbol,
                base_asset=base,
                quote_asset=quote,
                market_type=MarketType.FUTURES_PERPETUAL,
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

    async def _connect_websocket(self, streams: List[str]) -> None:
        """Connect WebSocket with specified streams."""
        if not streams:
            return

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
            symbol_lower = stream.split("@")[0]
            orderbook = self._normalize_depth_update(payload, symbol_lower)
            if orderbook:
                self._emit_orderbook(orderbook)

        elif "@forceOrder" in stream:
            liquidation = self._normalize_liquidation(payload)
            if liquidation:
                for callback in self._liquidation_callbacks:
                    try:
                        callback(liquidation)
                    except Exception as e:
                        self.logger.error("liquidation_callback_error", error=str(e))

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

    async def subscribe_liquidations(
        self,
        symbols: List[str],
        callback=None
    ) -> None:
        """Subscribe to liquidation streams."""
        if callback:
            self._liquidation_callbacks.append(callback)

        streams = []
        for symbol in symbols:
            exchange_symbol = self.to_exchange_symbol(symbol)
            stream = f"{exchange_symbol.lower()}@forceOrder"
            streams.append(stream)
            self._subscriptions["liquidation"].add(symbol)

        if self._ws is None:
            await self._connect_websocket(streams)
        else:
            # CONN-1 FIX: Только если есть НОВЫЕ streams
            new_streams = [s for s in streams if s not in self._active_streams]
            if new_streams:
                all_streams = list(self._active_streams) + new_streams
                await self._ws.close()
                await self._connect_websocket(all_streams)

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        for symbol in symbols:
            self._subscriptions.get(stream_type, set()).discard(symbol)

        streams = []
        for stype, suffix in [("trades", "@trade"), ("orderbook", "@depth@100ms"), ("liquidation", "@forceOrder")]:
            for symbol in self._subscriptions.get(stype, set()):
                exchange_symbol = self.to_exchange_symbol(symbol)
                streams.append(f"{exchange_symbol.lower()}{suffix}")

        if self._ws:
            await self._ws.close()

        if streams:
            await self._connect_websocket(streams)

    # -------------------------------------------------------------------------
    # REST API - Futures Specific
    # -------------------------------------------------------------------------

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get 24h ticker for symbol."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/fapi/v1/ticker/24hr"
        params = {"symbol": exchange_symbol}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Ticker request failed: {resp.status}")
                data = await resp.json()

        return UnifiedTicker(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            last_price=self.to_decimal(data.get("lastPrice", "0")),
            bid_price=self.to_decimal(data.get("bidPrice", "0")),
            ask_price=self.to_decimal(data.get("askPrice", "0")),
            high_24h=self.to_decimal(data.get("highPrice", "0")),
            low_24h=self.to_decimal(data.get("lowPrice", "0")),
            volume_24h=self.to_decimal(data.get("volume", "0")),
            quote_volume_24h=self.to_decimal(data.get("quoteVolume", "0")),
            price_change_24h=self.to_decimal(data.get("priceChange", "0")),
            price_change_pct_24h=self.to_decimal(data.get("priceChangePercent", "0")),
            trades_24h=int(data.get("count", 0)),
            raw=data,
        )

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/fapi/v1/depth"
        params = {"symbol": exchange_symbol, "limit": limit}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Orderbook request failed: {resp.status}")
                data = await resp.json()

        bids = [(self.to_decimal(p), self.to_decimal(q)) for p, q in data.get("bids", [])]
        asks = [(self.to_decimal(p), self.to_decimal(q)) for p, q in data.get("asks", [])]

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            bids=bids,
            asks=asks,
            sequence=data.get("lastUpdateId"),
            raw=data,
        )

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/fapi/v1/premiumIndex"
        params = {"symbol": exchange_symbol}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Funding request failed: {resp.status}")
                data = await resp.json()

        next_funding_ts = int(data.get("nextFundingTime", 0))
        next_funding_time = self.parse_timestamp(next_funding_ts) if next_funding_ts else datetime.now(timezone.utc)

        return UnifiedFunding(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            rate=self.to_decimal(data.get("lastFundingRate", "0")),
            next_funding_time=next_funding_time,
            mark_price=self.to_decimal(data.get("markPrice", "0")),
            index_price=self.to_decimal(data.get("indexPrice", "0")),
            interval_hours=8,
            raw=data,
        )

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """Get current open interest."""
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/fapi/v1/openInterest"
        params = {"symbol": exchange_symbol}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"OI request failed: {resp.status}")
                data = await resp.json()

        oi = self.to_decimal(data.get("openInterest", "0"))

        # Get mark price for USD calculation
        mark_price = Decimal("0")
        try:
            funding = await self.get_funding_rate(symbol)
            mark_price = funding.mark_price or Decimal("0")
        except Exception:
            pass

        oi_usd = oi * mark_price if mark_price > 0 else None

        return UnifiedOpenInterest(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            open_interest=oi,
            open_interest_usd=oi_usd,
            market_type=MarketType.FUTURES_PERPETUAL,
            raw=data,
        )

    async def get_long_short_ratio(self, symbol: str, period: str = "5m") -> dict:
        """
        Get Long/Short account ratio.

        Returns dict with:
        - long_short_ratio: ratio value
        - long_account_pct: % of accounts long
        - short_account_pct: % of accounts short
        """
        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/futures/data/globalLongShortAccountRatio"
        params = {"symbol": exchange_symbol, "period": period, "limit": 1}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"L/S ratio request failed: {resp.status}")
                data = await resp.json()

        if not data:
            return {"error": "no_data"}

        item = data[0]
        long_raw = Decimal(str(item.get("longAccount", "0.5")))
        short_raw = Decimal(str(item.get("shortAccount", "0.5")))

        return {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "long_short_ratio": float(item.get("longShortRatio", 1)),
            "long_account_pct": float(long_raw * 100),
            "short_account_pct": float(short_raw * 100),
        }

    async def get_all_funding_rates(self) -> Dict[str, UnifiedFunding]:
        """Get funding rates for all symbols (batch endpoint)."""
        url = f"{self._rest_url}/fapi/v1/premiumIndex"

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"All funding request failed: {resp.status}")
                data = await resp.json()

        result = {}
        for item in data:
            exchange_symbol = item.get("symbol", "")
            if exchange_symbol not in self._symbol_map:
                continue

            symbol = self._symbol_map[exchange_symbol]
            next_funding_ts = int(item.get("nextFundingTime", 0))
            next_funding_time = self.parse_timestamp(next_funding_ts) if next_funding_ts else datetime.now(timezone.utc)

            result[symbol] = UnifiedFunding(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                rate=self.to_decimal(item.get("lastFundingRate", "0")),
                next_funding_time=next_funding_time,
                mark_price=self.to_decimal(item.get("markPrice", "0")),
                index_price=self.to_decimal(item.get("indexPrice", "0")),
                interval_hours=8,
                raw=item,
            )

        return result

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

        for quote in ["USDT", "USDC", "BUSD"]:
            if raw_symbol.endswith(quote):
                base = raw_symbol[:-len(quote)]
                return f"{base}/{quote}"

        return raw_symbol

    def normalize_trade(self, raw: dict) -> UnifiedTrade:
        """Normalize Binance Futures trade."""
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
        """Normalize orderbook."""
        bids = [(self.to_decimal(p), self.to_decimal(q)) for p, q in raw.get("bids", [])]
        asks = [(self.to_decimal(p), self.to_decimal(q)) for p, q in raw.get("asks", [])]

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol="",
            timestamp=datetime.now(timezone.utc),
            bids=bids,
            asks=asks,
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

        bids = [(self.to_decimal(p), self.to_decimal(q)) for p, q in raw.get("b", [])]
        asks = [(self.to_decimal(p), self.to_decimal(q)) for p, q in raw.get("a", [])]

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(raw.get("E", 0)),
            bids=bids,
            asks=asks,
            sequence=raw.get("u"),
            raw=raw,
        )

    def _normalize_liquidation(self, raw: dict) -> Optional[UnifiedLiquidation]:
        """Normalize liquidation event."""
        o = raw.get("o", {})
        if not o:
            return None

        exchange_symbol = o.get("s", "")
        symbol = self.normalize_symbol(exchange_symbol)

        side_str = o.get("S", "")
        # Liquidation side is opposite of position
        liq_side = LiquidationType.LONG if side_str == "SELL" else LiquidationType.SHORT

        price = self.to_decimal(o.get("p", "0"))
        qty = self.to_decimal(o.get("q", "0"))

        return UnifiedLiquidation(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(o.get("T", 0)),
            side=liq_side,
            price=price,
            quantity=qty,
            value_usd=price * qty,
            order_type=o.get("o", ""),
            raw=raw,
        )
