# -*- coding: utf-8 -*-
"""
HTX (ex-Huobi) exchange connector.

WebSocket: wss://api.huobi.pro/ws or wss://api.hbdm.com/linear-swap-ws
REST: https://api.huobi.pro or https://api.hbdm.com
Rate limits: 800/min REST, 100 channels WS
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


class HTXConnector(BaseExchange):
    """HTX (Huobi) Linear Swap connector."""

    EXCHANGE_NAME = "htx"
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

    DEFAULT_WS_URL = "wss://api.hbdm.com/linear-swap-ws"
    DEFAULT_REST_URL = "https://api.hbdm.com"

    def __init__(self, config: Optional[ExchangeConfig] = None):
        """Initialize HTX connector."""
        if config is None:
            config = ExchangeConfig(
                name="htx",
                ws_url=self.DEFAULT_WS_URL,
                rest_url=self.DEFAULT_REST_URL,
            )
        super().__init__(config)

        # HTX URLs (Linear Swap = USDT-margined perpetuals)
        self._ws_url = config.ws_url or self.DEFAULT_WS_URL
        self._rest_url = config.rest_url or self.DEFAULT_REST_URL

        # Rate limiter: 800/min = 13/sec
        self._rate_limiter = RateLimiter(
            RateLimitConfig(
                requests_per_second=13,
                requests_per_minute=800,
            )
        )

        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # WebSocket state
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._subscriptions: Dict[str, Set[str]] = {
            "trades": set(),
            "orderbook": set(),
        }

        # Callbacks
        self._trade_callbacks: List[Callable] = []
        self._orderbook_callbacks: List[Callable] = []

        # Request ID counter
        self._req_id = 0

    def _next_req_id(self) -> str:
        """Get next request ID."""
        self._req_id += 1
        return f"req_{self._req_id}"

    # ============ Symbol Conversion ============

    def to_exchange_symbol(self, unified_symbol: str) -> str:
        """Convert unified symbol to HTX format: BTC/USDT -> BTC-USDT"""
        return unified_symbol.replace("/", "-")

    def to_unified_symbol(self, exchange_symbol: str) -> str:
        """Convert HTX symbol to unified format: BTC-USDT -> BTC/USDT"""
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
        """Connect to HTX."""
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
                heartbeat=None,  # HTX uses custom ping/pong
            )

            # Start message handler
            self._ws_task = asyncio.create_task(self._ws_handler())

            self._state = ConnectionState.CONNECTED
            self._reconnect_count = 0
            logger.info(f"[{self.EXCHANGE_NAME}] Connected successfully")

        except Exception as e:
            self._state = ConnectionState.DISCONNECTED
            logger.error(f"[{self.EXCHANGE_NAME}] Connection failed: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from HTX."""
        logger.info(f"[{self.EXCHANGE_NAME}] Disconnecting...")

        self._state = ConnectionState.DISCONNECTED

        # Cancel tasks
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
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
        """Load available symbols from HTX."""
        url = f"{self._rest_url}/linear-swap-api/v1/swap_contract_info"

        async with self._session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to load symbols: {resp.status}")

            result = await resp.json(content_type=None)
            contracts = result.get("data", [])

            for contract in contracts:
                # contract_code: BTC-USDT
                exchange_symbol = contract.get("contract_code", "")
                unified = self.to_unified_symbol(exchange_symbol)

                base = contract.get("symbol", "")
                self._symbols[unified] = SymbolInfo(
                    exchange=self.EXCHANGE_NAME,
                    symbol_unified=unified,
                    symbol_exchange=exchange_symbol,
                    base_asset=base,
                    quote_asset="USDT",
                    market_type=MarketType.FUTURES_PERPETUAL,
                    price_precision=int(contract.get("price_precision", 2)),
                    quantity_precision=0,
                    min_quantity=Decimal("1"),
                    min_notional=Decimal("5"),
                    tick_size=Decimal(str(contract.get("price_tick", "0.01"))),
                    step_size=Decimal("1"),
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
                    # HTX sends gzip-compressed messages
                    try:
                        decompressed = gzip.decompress(msg.data)
                        data = json.loads(decompressed)
                        await self._process_message(data)
                    except Exception as e:
                        logger.error(f"[{self.EXCHANGE_NAME}] Decompress error: {e}")
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

    async def _process_message(self, data: Dict[str, Any]) -> None:
        """Process WebSocket message."""
        # Handle ping
        if "ping" in data:
            pong = {"pong": data["ping"]}
            await self._ws.send_json(pong)
            return

        # Handle subscription response
        if "subbed" in data:
            logger.debug(f"[{self.EXCHANGE_NAME}] Subscribed: {data.get('subbed')}")
            return

        # Handle channel data
        ch = data.get("ch", "")
        tick = data.get("tick", {})

        if not ch or not tick:
            return

        # Parse channel: market.BTC-USDT.trade.detail
        parts = ch.split(".")

        if len(parts) >= 3:
            exchange_symbol = parts[1]
            channel_type = parts[2]

            if channel_type == "trade":
                # Trade data
                trades = tick.get("data", [])
                for trade_data in trades:
                    trade_data["symbol"] = exchange_symbol
                    trade = self.normalize_trade(trade_data)
                    for callback in self._trade_callbacks:
                        try:
                            callback(trade)
                        except Exception as e:
                            logger.error(f"Trade callback error: {e}")

            elif channel_type == "depth":
                # Orderbook data
                tick["symbol"] = exchange_symbol
                orderbook = self.normalize_orderbook(tick)
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

            # Restart handler
            self._ws_task = asyncio.create_task(self._ws_handler())

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
                "sub": f"market.{exchange_symbol}.trade.detail",
                "id": self._next_req_id(),
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
            # HTX supports step0-step5 for different precision, depth.step0 is best precision
            msg = {
                "sub": f"market.{exchange_symbol}.depth.step0",
                "id": self._next_req_id(),
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
                topic = f"market.{exchange_symbol}.trade.detail"
            elif stream_type == "orderbook":
                topic = f"market.{exchange_symbol}.depth.step0"
            else:
                continue

            msg = {
                "unsub": topic,
                "id": self._next_req_id(),
            }
            await self._ws.send_json(msg)
            logger.info(f"[{self.EXCHANGE_NAME}] Unsubscribed from {stream_type}: {symbol}")

    # ============ REST API ============

    async def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get ticker for symbol."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/linear-swap-ex/market/detail/merged"
        params = {"contract_code": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get ticker: {resp.status}")

            result = await resp.json()
            tick = result.get("tick", {})

            # CONN-4 FIX: Безопасное извлечение bid/ask
            bid_list = tick.get("bid", [])
            ask_list = tick.get("ask", [])
            bid_price = Decimal(str(bid_list[0])) if bid_list else Decimal(0)
            ask_price = Decimal(str(ask_list[0])) if ask_list else Decimal(0)

            return UnifiedTicker(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                last_price=Decimal(str(tick.get("close", 0))),
                bid_price=bid_price,
                ask_price=ask_price,
                volume_24h=Decimal(str(tick.get("vol", 0))),
                high_24h=Decimal(str(tick.get("high", 0))),
                low_24h=Decimal(str(tick.get("low", 0))),
            )

    async def get_orderbook(self, symbol: str, limit: int = 20) -> UnifiedOrderBook:
        """Get orderbook snapshot."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)
        url = f"{self._rest_url}/linear-swap-ex/market/depth"
        params = {"contract_code": exchange_symbol, "type": "step0"}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get orderbook: {resp.status}")

            result = await resp.json()
            tick = result.get("tick", {})
            tick["symbol"] = exchange_symbol
            return self.normalize_orderbook(tick, symbol)

    async def get_funding_rate(self, symbol: str) -> UnifiedFunding:
        """Get current funding rate."""
        await self._rate_limiter.acquire()

        exchange_symbol = self.to_exchange_symbol(symbol)

        # Get funding rate
        url = f"{self._rest_url}/linear-swap-api/v1/swap_funding_rate"
        params = {"contract_code": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get funding rate: {resp.status}")

            result = await resp.json()
            data = result.get("data", [{}])[0] if result.get("data") else {}

            funding_rate = Decimal(str(data.get("funding_rate", 0)))

            # Next funding time
            next_funding_ts = data.get("next_funding_time")
            next_funding_time = None
            if next_funding_ts:
                next_funding_time = datetime.fromtimestamp(
                    next_funding_ts / 1000, tz=timezone.utc
                )

        # Get mark price
        mark_url = f"{self._rest_url}/linear-swap-api/v1/swap_mark_price_kline"
        mark_params = {"contract_code": exchange_symbol, "period": "1min", "size": 1}

        mark_price = Decimal(0)
        async with self._session.get(mark_url, params=mark_params) as resp:
            if resp.status == 200:
                mark_result = await resp.json()
                klines = mark_result.get("data", [])
                if klines:
                    mark_price = Decimal(str(klines[0].get("close", 0)))

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
        url = f"{self._rest_url}/linear-swap-api/v1/swap_open_interest"
        params = {"contract_code": exchange_symbol}

        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get open interest: {resp.status}")

            result = await resp.json()
            data = result.get("data", [{}])[0] if result.get("data") else {}

            # OI is in contracts
            oi_contracts = Decimal(str(data.get("volume", 0)))
            contract_size = Decimal(str(data.get("contract_size", 1) or 1))

            # Convert to base currency
            oi_base = oi_contracts * contract_size

            # Get value in USDT
            oi_usd = Decimal(str(data.get("value", 0) or 0))

            return UnifiedOpenInterest(
                exchange=self.EXCHANGE_NAME,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                open_interest=oi_base,
                open_interest_usd=oi_usd if oi_usd else None,
            )

    # ============ Normalization ============

    def normalize_trade(self, raw: Dict[str, Any]) -> UnifiedTrade:
        """Normalize trade data."""
        # HTX trade format:
        # {"id": 123, "ts": 1234567890000, "amount": 0.1, "price": 50000.5,
        #  "direction": "buy", "symbol": "BTC-USDT"}
        exchange_symbol = raw.get("symbol", "")
        symbol = self.to_unified_symbol(exchange_symbol)

        direction = raw.get("direction", "buy").lower()
        side = Side.BUY if direction == "buy" else Side.SELL

        price = Decimal(str(raw.get("price", 0)))
        quantity = Decimal(str(raw.get("amount", 0)))

        ts = raw.get("ts", 0)
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
        # HTX orderbook format:
        # {"bids": [[price, qty], ...], "asks": [[price, qty], ...], "ts": 123}
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
            sequence=raw.get("version"),
        )
