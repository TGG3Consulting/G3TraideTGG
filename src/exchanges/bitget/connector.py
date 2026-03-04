# -*- coding: utf-8 -*-
"""
Bitget Exchange Connector.

Implements BaseExchange interface for Bitget V2 API.
Supports USDT-M perpetual futures.

WebSocket:
- wss://ws.bitget.com/v2/ws/public

REST:
- https://api.bitget.com/api/v2/mix/market/tickers
- https://api.bitget.com/api/v2/mix/market/depth
- https://api.bitget.com/api/v2/mix/market/open-interest
- https://api.bitget.com/api/v2/mix/market/current-fund-rate
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


class BitgetConnector(BaseExchange):
    """
    Bitget V2 API connector.

    Supports:
    - USDT-M perpetual futures
    - WebSocket streaming for trades and orderbook
    - REST for funding rate, open interest

    Usage:
        connector = BitgetConnector()
        await connector.connect()
        await connector.subscribe_trades(["BTC/USDT"])
        funding = await connector.get_funding_rate("BTC/USDT")
    """

    EXCHANGE_NAME = "bitget"
    EXCHANGE_TYPE = ExchangeType.CEX
    CAPABILITIES = {
        ExchangeCapability.FUTURES_PERPETUAL,
        ExchangeCapability.TRADES_STREAM,
        ExchangeCapability.ORDERBOOK_STREAM,
        ExchangeCapability.FUNDING_RATE,
        ExchangeCapability.OPEN_INTEREST,
    }

    # Default URLs
    DEFAULT_WS_URL = "wss://ws.bitget.com/v2/ws/public"
    DEFAULT_REST_URL = "https://api.bitget.com"

    # Rate limiting for subscriptions (Bitget limit: ~10 per second)
    SUBSCRIBE_BATCH_SIZE = 5          # Symbols per subscription message
    SUBSCRIBE_BATCH_DELAY = 1.0       # Seconds between batches

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize Bitget connector."""
        if config is None:
            config = ExchangeConfig(
                name="bitget",
                ws_url=self.DEFAULT_WS_URL,
                rest_url=self.DEFAULT_REST_URL,
            )

        super().__init__(config)

        self._ws_url = config.ws_url or self.DEFAULT_WS_URL
        self._rest_url = config.rest_url or self.DEFAULT_REST_URL

        # Rate limiter
        self._rate_limiter = RateLimiter(
            ExchangeRateLimits.BITGET,
            name="bitget"
        )

        # WebSocket
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._reconnect_count = 0

        # HTTP session
        self._http_session: Optional[aiohttp.ClientSession] = None

        # Subscriptions
        self._active_channels: Set[str] = set()

    # -------------------------------------------------------------------------
    # Connection Management
    # -------------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to Bitget."""
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
        """Disconnect from Bitget."""
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

        self._active_channels.clear()
        self._state = ConnectionState.DISCONNECTED
        self.logger.info("disconnected")

    async def _load_exchange_info(self) -> None:
        """Load exchange info from Bitget."""
        url = f"{self._rest_url}/api/v2/mix/market/contracts"
        params = {"productType": "USDT-FUTURES"}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Exchange info failed: {resp.status}")
                data = await resp.json()

        if data.get("code") != "00000":
            raise Exception(f"Bitget API error: {data.get('msg')}")

        for item in data.get("data", []):
            # Bitget format: BTCUSDT
            exchange_symbol = item.get("symbol", "")
            base = item.get("baseCoin", "")
            quote = item.get("quoteCoin", "USDT")
            unified_symbol = f"{base}/{quote}"

            info = SymbolInfo(
                exchange=self.EXCHANGE_NAME,
                symbol_unified=unified_symbol,
                symbol_exchange=exchange_symbol,
                base_asset=base,
                quote_asset=quote,
                market_type=MarketType.FUTURES_PERPETUAL,
                price_precision=int(item.get("pricePlace", 2)),
                quantity_precision=int(item.get("volumePlace", 4)),
                min_quantity=Decimal(item.get("minTradeNum", "0.001")),
                min_notional=Decimal("5"),
                tick_size=Decimal(str(10 ** -int(item.get("pricePlace", 2)))),
                step_size=Decimal(str(10 ** -int(item.get("volumePlace", 4)))),
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
        """Connect to Bitget WebSocket."""
        self.logger.debug("ws_connecting")

        try:
            self._ws = await websockets.connect(
                self._ws_url,
                ping_interval=None,
            )
            self._reconnect_count = 0

            self.logger.info("ws_connected")

            self._ws_task = asyncio.create_task(self._ws_message_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())

        except Exception as e:
            self.logger.error("ws_connect_failed", error=str(e))
            raise

    async def _ping_loop(self) -> None:
        """Send periodic pings."""
        try:
            while self._ws and not self._ws.closed:
                await asyncio.sleep(25)
                if self._ws and not self._ws.closed:
                    await self._ws.send("ping")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.warning("ping_error", error=str(e))

    async def _ws_message_loop(self) -> None:
        """WebSocket message processing loop."""
        try:
            async for message in self._ws:
                try:
                    # Bitget sends "pong" as text
                    if message == "pong":
                        continue

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
        # Subscription response
        event = data.get("event")
        if event == "subscribe":
            return
        if event == "error":
            self.logger.warning("ws_error", data=data)
            return

        arg = data.get("arg", {})
        channel = arg.get("channel", "")
        action = data.get("action", "")
        msg_data = data.get("data", [])

        if not channel or not msg_data:
            return

        if channel == "trade":
            for trade_data in msg_data:
                trade = self._normalize_trade(trade_data, arg.get("instId", ""))
                self._emit_trade(trade)

        elif channel.startswith("books"):
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
            if self._active_channels:
                # Group channels by type for batch resubscription
                channels_by_type: Dict[str, List[str]] = {}
                for channel_info in list(self._active_channels):
                    parts = channel_info.split(":")
                    if len(parts) == 2:
                        channel_type, inst_id = parts
                        if channel_type not in channels_by_type:
                            channels_by_type[channel_type] = []
                        channels_by_type[channel_type].append(inst_id)

                self._active_channels.clear()

                # Resubscribe in batches with delay
                for channel_type, inst_ids in channels_by_type.items():
                    self.logger.info(
                        "resubscribing_channel",
                        channel=channel_type,
                        count=len(inst_ids),
                    )
                    for i in range(0, len(inst_ids), self.SUBSCRIBE_BATCH_SIZE):
                        batch = inst_ids[i:i + self.SUBSCRIBE_BATCH_SIZE]
                        args = [
                            {"instType": "USDT-FUTURES", "channel": channel_type, "instId": iid}
                            for iid in batch
                        ]
                        msg = {"op": "subscribe", "args": args}
                        await self._ws.send(json.dumps(msg))

                        for iid in batch:
                            self._active_channels.add(f"{channel_type}:{iid}")

                        if i + self.SUBSCRIBE_BATCH_SIZE < len(inst_ids):
                            await asyncio.sleep(self.SUBSCRIBE_BATCH_DELAY)

        except Exception as e:
            self.logger.error("reconnect_failed", error=str(e))
            await self._handle_reconnect()

    async def _subscribe_channel(self, channel_info: str) -> None:
        """Subscribe to a channel."""
        if not self._ws:
            await self._connect_websocket()

        # Parse channel info: "trade:BTCUSDT"
        parts = channel_info.split(":")
        channel = parts[0]
        inst_id = parts[1] if len(parts) > 1 else ""

        msg = {
            "op": "subscribe",
            "args": [{
                "instType": "USDT-FUTURES",
                "channel": channel,
                "instId": inst_id,
            }]
        }
        await self._ws.send(json.dumps(msg))
        self._active_channels.add(channel_info)

    # -------------------------------------------------------------------------
    # Subscriptions
    # -------------------------------------------------------------------------

    async def _subscribe_batch(self, channel: str, symbols: List[str]) -> None:
        """
        Subscribe to multiple symbols in a single message with rate limiting.

        Bitget supports multiple args in one subscribe message.
        """
        if not self._ws:
            await self._connect_websocket()

        args = []
        for symbol in symbols:
            inst_id = self.to_exchange_symbol(symbol)
            args.append({
                "instType": "USDT-FUTURES",
                "channel": channel,
                "instId": inst_id,
            })
            # Track channel for reconnection
            self._active_channels.add(f"{channel}:{inst_id}")

        msg = {"op": "subscribe", "args": args}
        await self._ws.send(json.dumps(msg))

        self.logger.debug(
            "subscribed_batch",
            channel=channel,
            symbols_count=len(symbols),
        )

    async def subscribe_trades(
        self,
        symbols: List[str],
        callback: Optional[TradeCallback] = None
    ) -> None:
        """Subscribe to trade streams with rate limiting."""
        if callback:
            self._trade_callbacks.append(callback)

        self.logger.info(
            "subscribing_trades",
            total_symbols=len(symbols),
            batch_size=self.SUBSCRIBE_BATCH_SIZE,
        )

        # Subscribe in batches with delay
        for i in range(0, len(symbols), self.SUBSCRIBE_BATCH_SIZE):
            batch = symbols[i:i + self.SUBSCRIBE_BATCH_SIZE]

            await self._subscribe_batch("trade", batch)

            for symbol in batch:
                self._subscriptions["trades"].add(symbol)

            # Delay between batches (not after the last one)
            if i + self.SUBSCRIBE_BATCH_SIZE < len(symbols):
                await asyncio.sleep(self.SUBSCRIBE_BATCH_DELAY)

        self.logger.info("trades_subscribed", count=len(symbols))

    async def subscribe_orderbook(
        self,
        symbols: List[str],
        callback: Optional[OrderBookCallback] = None,
        depth: int = 15
    ) -> None:
        """Subscribe to orderbook streams with rate limiting."""
        if callback:
            self._orderbook_callbacks.append(callback)

        # Bitget supports: books5, books15
        channel = "books5" if depth <= 5 else "books15"

        self.logger.info(
            "subscribing_orderbook",
            total_symbols=len(symbols),
            batch_size=self.SUBSCRIBE_BATCH_SIZE,
            channel=channel,
        )

        # Subscribe in batches with delay
        for i in range(0, len(symbols), self.SUBSCRIBE_BATCH_SIZE):
            batch = symbols[i:i + self.SUBSCRIBE_BATCH_SIZE]

            await self._subscribe_batch(channel, batch)

            for symbol in batch:
                self._subscriptions["orderbook"].add(symbol)

            # Delay between batches (not after the last one)
            if i + self.SUBSCRIBE_BATCH_SIZE < len(symbols):
                await asyncio.sleep(self.SUBSCRIBE_BATCH_DELAY)

        self.logger.info("orderbook_subscribed", count=len(symbols))

    async def unsubscribe(self, stream_type: str, symbols: List[str]) -> None:
        """Unsubscribe from streams."""
        if not self._ws:
            return

        for symbol in symbols:
            self._subscriptions.get(stream_type, set()).discard(symbol)
            inst_id = self.to_exchange_symbol(symbol)

            channels_to_remove = []
            for ch in self._active_channels:
                if inst_id in ch:
                    channels_to_remove.append(ch)

            for ch in channels_to_remove:
                parts = ch.split(":")
                channel = parts[0]
                msg = {
                    "op": "unsubscribe",
                    "args": [{
                        "instType": "USDT-FUTURES",
                        "channel": channel,
                        "instId": inst_id,
                    }]
                }
                await self._ws.send(json.dumps(msg))
                self._active_channels.discard(ch)

    # -------------------------------------------------------------------------
    # REST API
    # -------------------------------------------------------------------------

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get 24h ticker."""
        inst_id = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/api/v2/mix/market/ticker"
        params = {"productType": "USDT-FUTURES", "symbol": inst_id}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Ticker request failed: {resp.status}")
                data = await resp.json()

        if data.get("code") != "00000":
            raise Exception(f"Bitget API error: {data.get('msg')}")

        items = data.get("data", [])
        if not items:
            raise Exception(f"No ticker data for {symbol}")

        item = items[0] if isinstance(items, list) else items

        return UnifiedTicker(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            last_price=self.to_decimal(item.get("lastPr", "0")),
            bid_price=self.to_decimal(item.get("bidPr", "0")),
            ask_price=self.to_decimal(item.get("askPr", "0")),
            high_24h=self.to_decimal(item.get("high24h", "0")),
            low_24h=self.to_decimal(item.get("low24h", "0")),
            volume_24h=self.to_decimal(item.get("baseVolume", "0")),
            quote_volume_24h=self.to_decimal(item.get("quoteVolume", "0")),
            price_change_pct_24h=self.to_decimal(item.get("change24h", "0")) * 100,
            raw=item,
        )

    async def get_orderbook(self, symbol: str, limit: int = 15) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        inst_id = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/api/v2/mix/market/depth"
        params = {"productType": "USDT-FUTURES", "symbol": inst_id, "limit": str(limit)}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Orderbook request failed: {resp.status}")
                data = await resp.json()

        if data.get("code") != "00000":
            raise Exception(f"Bitget API error: {data.get('msg')}")

        result = data.get("data", {})
        # Bitget format: [price, size]
        bids = [(self.to_decimal(b[0]), self.to_decimal(b[1])) for b in result.get("bids", [])]
        asks = [(self.to_decimal(a[0]), self.to_decimal(a[1])) for a in result.get("asks", [])]

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(int(result.get("ts", 0))),
            bids=bids,
            asks=asks,
            raw=result,
        )

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        inst_id = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/api/v2/mix/market/current-fund-rate"
        params = {"productType": "USDT-FUTURES", "symbol": inst_id}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"Funding request failed: {resp.status}")
                data = await resp.json()

        if data.get("code") != "00000":
            raise Exception(f"Bitget API error: {data.get('msg')}")

        items = data.get("data", [])
        if not items:
            raise Exception(f"No funding data for {symbol}")

        item = items[0] if isinstance(items, list) else items

        # Parse funding time
        funding_time_str = item.get("fundingTime", "")
        try:
            next_funding_time = datetime.fromisoformat(funding_time_str.replace("Z", "+00:00"))
        except Exception:
            next_funding_time = datetime.now(timezone.utc)

        return UnifiedFunding(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            rate=self.to_decimal(item.get("fundingRate", "0")),
            next_funding_time=next_funding_time,
            interval_hours=8,
            raw=item,
        )

    async def get_open_interest(self, symbol: str) -> UnifiedOpenInterest:
        """Get current open interest."""
        inst_id = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/api/v2/mix/market/open-interest"
        params = {"productType": "USDT-FUTURES", "symbol": inst_id}

        session = await self._get_http_session()
        async with self._rate_limiter:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise Exception(f"OI request failed: {resp.status}")
                data = await resp.json()

        if data.get("code") != "00000":
            raise Exception(f"Bitget API error: {data.get('msg')}")

        result = data.get("data", {})

        return UnifiedOpenInterest(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            open_interest=self.to_decimal(result.get("openInterest", "0")),
            open_interest_usd=self.to_decimal(result.get("openInterestUsd", "0")),
            market_type=MarketType.FUTURES_PERPETUAL,
            raw=result,
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

        for quote in ["USDT", "USDC"]:
            if raw_symbol.endswith(quote):
                base = raw_symbol[:-len(quote)]
                return f"{base}/{quote}"

        return raw_symbol

    def normalize_trade(self, raw: dict) -> UnifiedTrade:
        """Normalize trade for abstract method."""
        return self._normalize_trade(raw, "")

    def _normalize_trade(self, raw: dict, inst_id: str) -> UnifiedTrade:
        """Normalize Bitget trade data."""
        symbol = self.normalize_symbol(inst_id or raw.get("instId", ""))

        side_str = raw.get("side", "buy")
        side = Side.BUY if side_str == "buy" else Side.SELL

        return UnifiedTrade(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(int(raw.get("ts", 0))),
            price=self.to_decimal(raw.get("price", "0")),
            quantity=self.to_decimal(raw.get("size", "0")),
            side=side,
            trade_id=str(raw.get("tradeId", "")),
            raw=raw,
        )

    def normalize_orderbook(self, raw: dict) -> UnifiedOrderBook:
        """Normalize orderbook for abstract method."""
        return self._normalize_orderbook_ws(raw)

    def _normalize_orderbook_ws(self, data: dict) -> Optional[UnifiedOrderBook]:
        """Normalize WebSocket orderbook data."""
        arg = data.get("arg", {})
        inst_id = arg.get("instId", "")
        symbol = self.normalize_symbol(inst_id)

        items = data.get("data", [])
        if not items:
            return None

        result = items[0]
        bids = [(self.to_decimal(b[0]), self.to_decimal(b[1])) for b in result.get("bids", [])]
        asks = [(self.to_decimal(a[0]), self.to_decimal(a[1])) for a in result.get("asks", [])]

        return UnifiedOrderBook(
            exchange=self.EXCHANGE_NAME,
            symbol=symbol,
            timestamp=self.parse_timestamp(int(result.get("ts", 0))),
            bids=bids,
            asks=asks,
            raw=data,
        )
