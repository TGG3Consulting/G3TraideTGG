# -*- coding: utf-8 -*-
"""
Historical Data Loader for BinanceFriend.

Загружает исторические данные при старте системы для корректной работы
детекторов с первой секунды.

Загружаемые данные:
- Klines (1m свечи) - для baseline объёма
- Funding Rate History - для funding gradient
- Open Interest History - для oi_change_1h
- Cross-exchange данные - для price divergence

КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!

Design: HISTORY_LOADER_DESIGN.md
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING
import aiohttp
import structlog

from config.settings import settings, HistoryConfig
from .binance_api import (
    BinanceApiClient,
    BinanceBannedError,
    BinanceRateLimitError,
    get_futures_client,
    get_spot_client,
    get_ban_state,
)

if TYPE_CHECKING:
    from .realtime_monitor import RealtimeMonitor
    from .futures_monitor import FuturesMonitor
    from src.cross_exchange.state_store import CrossExchangeStateStore

logger = structlog.get_logger(__name__)


@dataclass
class HistoryLoadResult:
    """Результат загрузки исторических данных."""
    success: bool
    loaded_klines: int = 0
    loaded_funding: int = 0
    loaded_oi: int = 0
    loaded_trades: int = 0
    loaded_cross_exchange: int = 0
    errors: List[str] = None
    duration_sec: float = 0.0

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class HistoryLoader:
    """
    Загрузчик исторических данных.

    КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!

    Загружает данные параллельно при старте системы.

    Usage:
        loader = HistoryLoader(settings.history)
        result = await loader.load_all(
            symbols=["BTCUSDT", "ETHUSDT"],
            futures_monitor=futures_monitor,
            realtime_monitor=realtime_monitor,
            cross_state=cross_state,
        )
    """

    # API URLs
    FUTURES_URL = "https://fapi.binance.com"
    SPOT_URL = "https://api.binance.com"
    DATA_URL = "https://fapi.binance.com"  # For openInterestHist

    def __init__(self, config: HistoryConfig):
        self._config = config
        self._futures_client = get_futures_client()
        self._spot_client = get_spot_client()
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def load_all(
        self,
        symbols: List[str],
        futures_monitor: Optional["FuturesMonitor"] = None,
        realtime_monitor: Optional["RealtimeMonitor"] = None,
        cross_state: Optional["CrossExchangeStateStore"] = None,
    ) -> HistoryLoadResult:
        """
        Загрузить все исторические данные.

        Args:
            symbols: Список символов для загрузки
            futures_monitor: FuturesMonitor для OI/Funding данных
            realtime_monitor: RealtimeMonitor для кэширования klines
            cross_state: CrossExchangeStateStore для cross-exchange данных

        Returns:
            HistoryLoadResult с статистикой загрузки
        """
        if not self._config.enabled:
            logger.info("history_loader_disabled")
            return HistoryLoadResult(success=True)

        start_time = datetime.now()
        result = HistoryLoadResult(success=True)

        logger.info(
            "history_loader_starting",
            symbols_count=len(symbols),
            klines_enabled=self._config.klines.enabled,
            funding_enabled=self._config.funding.enabled,
            oi_enabled=self._config.oi.enabled,
        )

        # Create session and semaphore
        timeout = aiohttp.ClientTimeout(total=self._config.request_timeout_sec)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._semaphore = asyncio.Semaphore(self._config.parallel_requests)

        try:
            # Запускаем все загрузчики параллельно
            tasks = []

            # Klines для baseline объёма
            if self._config.klines.enabled and realtime_monitor:
                tasks.append(self._load_klines_history(symbols, realtime_monitor, result))
                tasks.append(self._load_daily_klines_history(symbols, realtime_monitor, result))  # FIX-L-3

            # Funding Rate History
            if self._config.funding.enabled and futures_monitor:
                tasks.append(self._load_funding_history(symbols, futures_monitor, result))

            # Open Interest History
            if self._config.oi.enabled and futures_monitor:
                tasks.append(self._load_oi_history(symbols, futures_monitor, result))

            # Trades History (для паттернов wash trading, coordinated buys)
            if self._config.trades.enabled and realtime_monitor:
                tasks.append(self._load_trades_history(symbols, realtime_monitor, result))

            # Cross-exchange данные
            if self._config.cross_exchange.enabled and cross_state:
                tasks.append(self._load_cross_exchange_history(symbols, cross_state, result))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error("history_loader_error", error=str(e))
            result.errors.append(str(e))
            result.success = False
        finally:
            if self._session:
                await self._session.close()
                self._session = None

        result.duration_sec = (datetime.now() - start_time).total_seconds()

        logger.info(
            "history_loader_completed",
            success=result.success,
            klines=result.loaded_klines,
            funding=result.loaded_funding,
            oi=result.loaded_oi,
            cross_exchange=result.loaded_cross_exchange,
            errors=len(result.errors),
            duration_sec=round(result.duration_sec, 2),
        )

        return result

    async def _load_klines_history(
        self,
        symbols: List[str],
        realtime_monitor: "RealtimeMonitor",
        result: HistoryLoadResult,
    ):
        """
        Загрузить историю klines для baseline объёма.

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!

        Endpoint: /api/v3/klines
        """
        logger.info("loading_klines_history", symbols_count=len(symbols))

        hours = self._config.klines.hours
        interval = self._config.klines.interval
        limit = min(hours * 60, 1500)  # Max 1500 for 1m candles

        async def load_symbol(symbol: str):
            async with self._semaphore:
                try:
                    # Проверяем бан перед запросом
                    ban_state = get_ban_state()
                    if ban_state.is_banned():
                        logger.warning("klines_skipped_banned", symbol=symbol)
                        return False

                    params = {
                        "symbol": symbol,
                        "interval": interval,
                        "limit": limit,
                    }

                    data = await self._spot_client.get("/api/v3/klines", params=params)
                    # Кэшируем в realtime_monitor
                    if hasattr(realtime_monitor, '_cache_klines_history'):
                        await realtime_monitor._cache_klines_history(symbol, data)
                    result.loaded_klines += 1
                    return True

                except BinanceBannedError:
                    logger.warning("klines_fetch_banned", symbol=symbol)
                    return False
                except BinanceRateLimitError:
                    logger.warning("klines_fetch_rate_limited", symbol=symbol)
                    return False
                except Exception as e:
                    logger.warning("klines_fetch_error", symbol=symbol, error=str(e))
                    return False
                finally:
                    # Rate limit delay
                    await asyncio.sleep(self._config.rate_limit_delay_ms / 1000)

        # Загружаем параллельно
        await asyncio.gather(*[load_symbol(s) for s in symbols], return_exceptions=True)

    async def _load_daily_klines_history(
        self,
        symbols: List[str],
        realtime_monitor: "RealtimeMonitor",
        result: HistoryLoadResult,
    ):
        """
        FIX-L-3: Загрузить дневные klines для daily ATR расчёта.

        Endpoint: /api/v3/klines?interval=1d&limit=20
        Загружает 20 дневных свечей — достаточно для ATR-14.
        """
        logger.info("loading_daily_klines_history", symbols_count=len(symbols))

        async def load_symbol(symbol: str):
            async with self._semaphore:
                try:
                    ban_state = get_ban_state()
                    if ban_state.is_banned():
                        return False

                    params = {
                        "symbol": symbol,
                        "interval": "1d",
                        "limit": 20,  # FIX-L-3: 20 дней для ATR-14
                    }

                    data = await self._spot_client.get("/api/v3/klines", params=params)

                    if hasattr(realtime_monitor, '_cache_daily_klines_history'):
                        await realtime_monitor._cache_daily_klines_history(symbol, data)

                    return True

                except BinanceBannedError:
                    logger.warning("daily_klines_fetch_banned", symbol=symbol)
                    return False
                except BinanceRateLimitError:
                    logger.warning("daily_klines_fetch_rate_limited", symbol=symbol)
                    return False
                except Exception as e:
                    logger.warning("daily_klines_fetch_error", symbol=symbol, error=str(e))
                    return False
                finally:
                    await asyncio.sleep(self._config.rate_limit_delay_ms / 1000)

        await asyncio.gather(*[load_symbol(s) for s in symbols], return_exceptions=True)

    async def _load_funding_history(
        self,
        symbols: List[str],
        futures_monitor: "FuturesMonitor",
        result: HistoryLoadResult,
    ):
        """
        Загрузить историю Funding Rate.

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!

        Endpoint: /fapi/v1/fundingRate
        Интервал: 8 часов (3 записи в сутки)

        Для gradient нужно минимум 3 записи (24 часа).
        По умолчанию загружаем 72 часа = 9 записей.
        """
        logger.info("loading_funding_history", symbols_count=len(symbols))

        hours = self._config.funding.hours
        # 1 запись = 8 часов, минимум 3 для gradient
        limit = max(3, hours // 8 + 1)

        async def load_symbol(symbol: str):
            async with self._semaphore:
                try:
                    # Проверяем бан перед запросом
                    ban_state = get_ban_state()
                    if ban_state.is_banned():
                        logger.warning("funding_skipped_banned", symbol=symbol)
                        return False

                    params = {
                        "symbol": symbol,
                        "limit": limit,
                    }

                    data = await self._futures_client.get("/fapi/v1/fundingRate", params=params)
                    # Добавляем в историю futures_monitor
                    if hasattr(futures_monitor, '_cache_funding_history'):
                        await futures_monitor._cache_funding_history(symbol, data)
                    result.loaded_funding += 1
                    return True

                except BinanceBannedError:
                    logger.warning("funding_fetch_banned", symbol=symbol)
                    return False
                except BinanceRateLimitError:
                    logger.warning("funding_fetch_rate_limited", symbol=symbol)
                    return False
                except Exception as e:
                    logger.warning("funding_fetch_error", symbol=symbol, error=str(e))
                    return False
                finally:
                    await asyncio.sleep(self._config.rate_limit_delay_ms / 1000)

        # Загружаем параллельно
        await asyncio.gather(*[load_symbol(s) for s in symbols], return_exceptions=True)

    async def _load_oi_history(
        self,
        symbols: List[str],
        futures_monitor: "FuturesMonitor",
        result: HistoryLoadResult,
    ):
        """
        Загрузить историю Open Interest.

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!

        Endpoint: /futures/data/openInterestHist
        Периоды: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
        """
        logger.info("loading_oi_history", symbols_count=len(symbols))

        hours = self._config.oi.hours
        period = self._config.oi.period

        # Рассчитываем limit на основе периода
        period_minutes = {
            "5m": 5, "15m": 15, "30m": 30, "1h": 60,
            "2h": 120, "4h": 240, "6h": 360, "12h": 720, "1d": 1440,
        }
        minutes_per_period = period_minutes.get(period, 5)
        limit = min(500, (hours * 60) // minutes_per_period + 1)

        async def load_symbol(symbol: str):
            async with self._semaphore:
                try:
                    # Проверяем бан перед запросом
                    ban_state = get_ban_state()
                    if ban_state.is_banned():
                        logger.warning("oi_skipped_banned", symbol=symbol)
                        return False

                    params = {
                        "symbol": symbol,
                        "period": period,
                        "limit": limit,
                    }

                    data = await self._futures_client.get("/futures/data/openInterestHist", params=params)
                    # Добавляем в историю futures_monitor
                    if hasattr(futures_monitor, '_cache_oi_history'):
                        await futures_monitor._cache_oi_history(symbol, data)
                    result.loaded_oi += 1
                    return True

                except BinanceBannedError:
                    logger.warning("oi_fetch_banned", symbol=symbol)
                    return False
                except BinanceRateLimitError:
                    logger.warning("oi_fetch_rate_limited", symbol=symbol)
                    return False
                except Exception as e:
                    logger.warning("oi_fetch_error", symbol=symbol, error=str(e))
                    return False
                finally:
                    await asyncio.sleep(self._config.rate_limit_delay_ms / 1000)

        await asyncio.gather(*[load_symbol(s) for s in symbols], return_exceptions=True)

    async def _load_trades_history(
        self,
        symbols: List[str],
        realtime_monitor: "RealtimeMonitor",
        result: HistoryLoadResult,
    ):
        """
        Загрузить историю сделок для паттернов.

        КРИТИЧНО: Использует BinanceApiClient для обработки 418/429!

        Endpoint: /api/v3/aggTrades
        Лимит: 1000 сделок

        ВАЖНО: Загружаем за последние N минут для:
        - wash trading детекции
        - coordinated buys/sells детекции
        - buy/sell ratio с самого старта
        """
        logger.info("loading_trades_history", symbols_count=len(symbols))

        minutes = self._config.trades.minutes
        # Вычисляем startTime для загрузки (N минут назад)
        start_time_ms = int((datetime.now().timestamp() - minutes * 60) * 1000)

        async def load_symbol(symbol: str):
            async with self._semaphore:
                try:
                    # Проверяем бан перед запросом
                    ban_state = get_ban_state()
                    if ban_state.is_banned():
                        logger.warning("trades_skipped_banned", symbol=symbol)
                        return False

                    params = {
                        "symbol": symbol,
                        "startTime": start_time_ms,
                        "limit": 1000,
                    }

                    data = await self._spot_client.get("/api/v3/aggTrades", params=params)
                    # Кэшируем в realtime_monitor
                    if hasattr(realtime_monitor, '_cache_trades_history'):
                        await realtime_monitor._cache_trades_history(symbol, data)
                    result.loaded_trades += 1
                    logger.debug(
                        "trades_loaded",
                        symbol=symbol,
                        count=len(data),
                    )
                    return True

                except BinanceBannedError:
                    logger.warning("trades_fetch_banned", symbol=symbol)
                    return False
                except BinanceRateLimitError:
                    logger.warning("trades_fetch_rate_limited", symbol=symbol)
                    return False
                except Exception as e:
                    logger.warning("trades_fetch_error", symbol=symbol, error=str(e))
                    return False
                finally:
                    await asyncio.sleep(self._config.rate_limit_delay_ms / 1000)

        # Загружаем параллельно
        await asyncio.gather(*[load_symbol(s) for s in symbols], return_exceptions=True)

    async def _load_cross_exchange_history(
        self,
        symbols: List[str],
        cross_state: "CrossExchangeStateStore",
        result: HistoryLoadResult,
    ):
        """
        Загрузить историю с других бирж для cross-exchange анализа.

        Каждая биржа имеет свои API endpoints.
        """
        logger.info(
            "loading_cross_exchange_history",
            symbols_count=len(symbols),
            exchanges=self._config.cross_exchange.exchanges,
        )

        # Пока загружаем только funding с других бирж
        # В будущем можно добавить klines и OI

        for exchange in self._config.cross_exchange.exchanges:
            if exchange == "binance":
                # Binance уже загружен выше
                continue

            try:
                if exchange == "bybit":
                    await self._load_bybit_funding(symbols, cross_state, result)
                elif exchange == "okx":
                    await self._load_okx_funding(symbols, cross_state, result)
                # Другие биржи можно добавить аналогично
            except Exception as e:
                logger.warning(
                    "cross_exchange_load_error",
                    exchange=exchange,
                    error=str(e),
                )
                result.errors.append(f"{exchange}: {str(e)}")

    async def _load_bybit_funding(
        self,
        symbols: List[str],
        cross_state: "CrossExchangeStateStore",
        result: HistoryLoadResult,
    ):
        """Загрузить funding history с Bybit."""
        url = "https://api.bybit.com/v5/market/funding/history"

        async def load_symbol(symbol: str):
            async with self._semaphore:
                try:
                    params = {
                        "category": "linear",
                        "symbol": symbol,
                        "limit": 9,  # 72 часа
                    }

                    async with self._session.get(url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("retCode") == 0:
                                records = data.get("result", {}).get("list", [])
                                if records and hasattr(cross_state, 'cache_funding_history'):
                                    await cross_state.cache_funding_history(
                                        "bybit", symbol, records
                                    )
                                result.loaded_cross_exchange += 1
                                return True
                except Exception as e:
                    logger.debug("bybit_funding_error", symbol=symbol, error=str(e))
                finally:
                    await asyncio.sleep(self._config.rate_limit_delay_ms / 1000)
                return False

        await asyncio.gather(*[load_symbol(s) for s in symbols[:20]], return_exceptions=True)

    async def _load_okx_funding(
        self,
        symbols: List[str],
        cross_state: "CrossExchangeStateStore",
        result: HistoryLoadResult,
    ):
        """Загрузить funding history с OKX."""
        url = "https://www.okx.com/api/v5/public/funding-rate-history"

        async def load_symbol(symbol: str):
            async with self._semaphore:
                try:
                    # OKX использует формат BTC-USDT-SWAP
                    okx_symbol = symbol.replace("USDT", "-USDT-SWAP")
                    params = {
                        "instId": okx_symbol,
                        "limit": "9",  # OKX принимает string
                    }

                    async with self._session.get(url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("code") == "0":
                                records = data.get("data", [])
                                if records and hasattr(cross_state, 'cache_funding_history'):
                                    await cross_state.cache_funding_history(
                                        "okx", symbol, records
                                    )
                                result.loaded_cross_exchange += 1
                                return True
                except Exception as e:
                    logger.debug("okx_funding_error", symbol=symbol, error=str(e))
                finally:
                    await asyncio.sleep(self._config.rate_limit_delay_ms / 1000)
                return False

        await asyncio.gather(*[load_symbol(s) for s in symbols[:20]], return_exceptions=True)


async def load_historical_data(
    symbols: List[str],
    futures_monitor: Optional["FuturesMonitor"] = None,
    realtime_monitor: Optional["RealtimeMonitor"] = None,
    cross_state: Optional["CrossExchangeStateStore"] = None,
) -> HistoryLoadResult:
    """
    Convenience function для загрузки исторических данных.

    Usage:
        result = await load_historical_data(
            symbols=["BTCUSDT", "ETHUSDT"],
            futures_monitor=fm,
        )
    """
    loader = HistoryLoader(settings.history)
    return await loader.load_all(
        symbols=symbols,
        futures_monitor=futures_monitor,
        realtime_monitor=realtime_monitor,
        cross_state=cross_state,
    )
