# -*- coding: utf-8 -*-
"""
Universe Scanner - сканирует FUTURES пары Binance.

ВАЖНО: Скринер работает ТОЛЬКО с фьючерсами!
Символы получаются динамически с fapi.binance.com

КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!
"""

import asyncio
import time
from decimal import Decimal
from typing import Optional
import aiohttp
import structlog

from config.settings import settings
from .models import SymbolStats
from .binance_api import (
    BinanceApiClient,
    BinanceBannedError,
    BinanceRateLimitError,
    get_futures_client,
)


logger = structlog.get_logger(__name__)


class UniverseScanner:
    """
    Сканирует FUTURES пары Binance и собирает базовую статистику.

    ТОЛЬКО PERPETUAL USDT фьючерсы!

    КРИТИЧНО: Кэширует exchangeInfo на 24 часа чтобы не получить бан!

    Использование:
        scanner = UniverseScanner()
        symbols = await scanner.scan()
        print(f"Found {len(symbols)} FUTURES pairs")
    """

    # FUTURES API (не SPOT!)
    FUTURES_URL = "https://fapi.binance.com"

    # Кэш exchangeInfo на 24 часа (символы редко меняются)
    EXCHANGE_INFO_CACHE_TTL = 24 * 60 * 60  # 24 часа в секундах

    def __init__(self):
        self._client = get_futures_client()
        self._session: Optional[aiohttp.ClientSession] = None
        self._exchange_info: dict[str, dict] = {}
        self._exchange_info_cache_time: float = 0  # Время кэширования
        self._all_symbols: list[SymbolStats] = []
        self._last_scan_time: float = 0

    async def scan(self) -> list[SymbolStats]:
        """
        Сканирование FUTURES пар.

        Returns:
            Список SymbolStats для PERPETUAL USDT фьючерсов
        """
        logger.info("scanning_futures_universe")

        # 1. Получить exchange info с FUTURES API (с кэшированием!)
        cache_age = time.time() - self._exchange_info_cache_time
        cache_valid = self._exchange_info and cache_age < self.EXCHANGE_INFO_CACHE_TTL

        if not cache_valid:
            try:
                await self._fetch_futures_exchange_info()
                logger.info("futures_exchange_info_loaded", symbols=len(self._exchange_info))
            except BinanceBannedError as e:
                logger.error("scan_blocked_ip_banned", retry_after=e.retry_after)
                if self._exchange_info:
                    logger.warning("using_cached_exchange_info", cached_symbols=len(self._exchange_info))
                else:
                    raise
            except BinanceRateLimitError as e:
                logger.warning("scan_rate_limited", retry_after=e.retry_after)
                if self._exchange_info:
                    logger.warning("using_cached_exchange_info", cached_symbols=len(self._exchange_info))
                else:
                    raise
        else:
            logger.info(
                "using_cached_exchange_info",
                cached_symbols=len(self._exchange_info),
                cache_age_hours=round(cache_age / 3600, 1),
            )

        # 2. Получить 24h тикеры для FUTURES
        tickers = await self._fetch_futures_tickers()
        logger.info("futures_tickers_fetched", count=len(tickers))

        # 3. Преобразовать в SymbolStats
        symbols = []
        for ticker in tickers:
            symbol = ticker.get("symbol", "")

            # Пропускаем если нет в exchange info (не PERPETUAL или не USDT)
            if symbol not in self._exchange_info:
                continue

            info = self._exchange_info[symbol]

            try:
                stats = SymbolStats(
                    symbol=symbol,
                    price=Decimal(ticker.get("lastPrice", "0")),
                    volume_24h_usd=Decimal(ticker.get("quoteVolume", "0")),
                    price_change_24h=Decimal(ticker.get("priceChangePercent", "0")),
                    trade_count_24h=int(ticker.get("count", 0)),
                    quote_asset=info["quoteAsset"],
                    base_asset=info["baseAsset"],
                )
                symbols.append(stats)
            except Exception as e:
                logger.warning("failed_to_parse_futures_ticker", symbol=symbol, error=str(e))
                continue

        self._all_symbols = symbols
        logger.info("futures_universe_scan_complete", total_symbols=len(symbols))

        return symbols

    async def get_symbol_info(self, symbol: str) -> Optional[dict]:
        """Получить информацию о конкретной паре."""
        if not self._exchange_info:
            await self._fetch_futures_exchange_info()
        return self._exchange_info.get(symbol)

    async def _fetch_futures_exchange_info(self):
        """
        Загрузить информацию о FUTURES парах.

        Фильтруем:
        - status == "TRADING"
        - contractType == "PERPETUAL"
        - quoteAsset == "USDT"

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!
        """
        # Используем безопасный клиент вместо прямого запроса
        data = await self._client.get("/fapi/v1/exchangeInfo")

        perpetual_count = 0
        new_exchange_info = {}

        for s in data.get("symbols", []):
            # Только активные PERPETUAL USDT фьючерсы
            if (s.get("status") == "TRADING" and
                s.get("contractType") == "PERPETUAL" and
                s.get("quoteAsset") == "USDT"):

                new_exchange_info[s["symbol"]] = {
                    "baseAsset": s["baseAsset"],
                    "quoteAsset": s["quoteAsset"],
                    "status": s["status"],
                    "contractType": s["contractType"],
                    "filters": {f["filterType"]: f for f in s.get("filters", [])},
                }
                perpetual_count += 1

        # Обновляем кэш только если успешно получили данные
        self._exchange_info = new_exchange_info
        self._exchange_info_cache_time = time.time()

        logger.info(
            "futures_exchange_info_cached",
            perpetual_usdt=perpetual_count,
            total_in_response=len(data.get("symbols", [])),
            cache_ttl_hours=self.EXCHANGE_INFO_CACHE_TTL / 3600,
        )

    async def _fetch_futures_tickers(self) -> list[dict]:
        """
        Получить 24h статистику FUTURES пар.

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!
        """
        # Используем безопасный клиент
        return await self._client.get("/fapi/v1/ticker/24hr")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Получить HTTP сессию (legacy, использует клиент)."""
        return await self._client.get_session()

    async def close(self):
        """Закрыть HTTP сессию."""
        await self._client.stop()
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def __del__(self):
        if self._session and not self._session.closed:
            logger.warning("session_not_closed_properly")
