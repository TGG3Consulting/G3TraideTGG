# -*- coding: utf-8 -*-
"""
Binance API Client - безопасный клиент с обработкой бана и rate limit.

КРИТИЧНО: Этот модуль ОБЯЗАТЕЛЕН для всех запросов к Binance API!
Обрабатывает:
- 418: IP забанен (ждём до окончания бана)
- 429: Rate limit (exponential backoff)
- 5xx: Серверные ошибки (retry с backoff)
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any
import aiohttp
import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


class BinanceBannedError(Exception):
    """IP забанен Binance."""
    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.retry_after = retry_after


class BinanceRateLimitError(Exception):
    """Rate limit превышен."""
    def __init__(self, message: str, retry_after: int = 10):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class BanState:
    """Состояние бана для конкретного endpoint."""
    banned_until: float = 0.0  # timestamp когда бан истекает
    last_429_time: float = 0.0  # timestamp последнего 429
    backoff_seconds: float = 1.0  # текущий backoff

    def is_banned(self) -> bool:
        return time.time() < self.banned_until

    def time_until_unban(self) -> float:
        if not self.is_banned():
            return 0
        return self.banned_until - time.time()

    def reset_backoff(self):
        self.backoff_seconds = 1.0


# Глобальное состояние бана (shared между всеми инстансами)
_ban_state: BanState = BanState()


def get_ban_state() -> BanState:
    """Получить глобальное состояние бана."""
    global _ban_state
    return _ban_state


class BinanceApiClient:
    """
    Безопасный клиент для Binance API.

    Использование:
        client = BinanceApiClient()
        await client.start()

        try:
            data = await client.get("/fapi/v1/exchangeInfo")
        except BinanceBannedError as e:
            print(f"Забанены на {e.retry_after} секунд")
        finally:
            await client.stop()
    """

    # Базовые URL
    FUTURES_URL = "https://fapi.binance.com"
    SPOT_URL = "https://api.binance.com"

    # Настройки retry
    MAX_RETRIES = 3
    INITIAL_BACKOFF = 1.0
    MAX_BACKOFF = 60.0
    BACKOFF_MULTIPLIER = 2.0

    def __init__(
        self,
        base_url: str = None,
        timeout: float = None,
    ):
        """
        Args:
            base_url: Базовый URL API (по умолчанию FUTURES)
            timeout: Таймаут запроса в секундах
        """
        self._base_url = base_url or self.FUTURES_URL
        self._timeout = timeout or getattr(settings.rate_limit, 'request_timeout_sec', 30)
        self._session: Optional[aiohttp.ClientSession] = None
        self._ban_state = get_ban_state()

    async def start(self):
        """Создать HTTP сессию."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def stop(self):
        """Закрыть HTTP сессию."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def get(
        self,
        endpoint: str,
        params: dict = None,
        full_url: str = None,
    ) -> dict:
        """
        Выполнить GET запрос с обработкой ошибок.

        Args:
            endpoint: Путь API (например "/fapi/v1/exchangeInfo")
            params: Query параметры
            full_url: Полный URL (если указан, игнорирует base_url + endpoint)

        Returns:
            JSON ответ

        Raises:
            BinanceBannedError: IP забанен
            BinanceRateLimitError: Rate limit (после всех retry)
            Exception: Другие ошибки
        """
        await self.start()

        # Проверить бан ПЕРЕД запросом
        if self._ban_state.is_banned():
            wait_time = self._ban_state.time_until_unban()
            logger.warning(
                "binance_banned_waiting",
                wait_seconds=round(wait_time, 1),
                banned_until=datetime.fromtimestamp(self._ban_state.banned_until).isoformat(),
            )
            # Ждём окончания бана
            await asyncio.sleep(wait_time + 1)  # +1 секунда запас

        url = full_url or f"{self._base_url}{endpoint}"

        for attempt in range(self.MAX_RETRIES):
            try:
                async with self._session.get(url, params=params) as resp:
                    # ============================================================
                    # 418: IP ЗАБАНЕН
                    # ============================================================
                    if resp.status == 418:
                        # Парсим информацию о бане
                        try:
                            error_data = await resp.json()
                            error_msg = error_data.get("msg", "IP banned")
                        except:
                            error_msg = await resp.text()

                        # Извлекаем время бана из заголовков или сообщения
                        retry_after = int(resp.headers.get("Retry-After", 60))

                        # Парсим timestamp из сообщения если есть
                        # Формат: "banned until 1771436930068"
                        if "until" in error_msg:
                            try:
                                import re
                                match = re.search(r'until (\d+)', error_msg)
                                if match:
                                    ban_until_ms = int(match.group(1))
                                    ban_until_sec = ban_until_ms / 1000
                                    retry_after = max(1, int(ban_until_sec - time.time()))
                            except:
                                pass

                        # Установить глобальный бан
                        self._ban_state.banned_until = time.time() + retry_after

                        logger.error(
                            "BINANCE_IP_BANNED",
                            retry_after_sec=retry_after,
                            banned_until=datetime.fromtimestamp(self._ban_state.banned_until).isoformat(),
                            error=error_msg[:200],
                        )

                        raise BinanceBannedError(
                            f"IP banned: {error_msg}",
                            retry_after=retry_after
                        )

                    # ============================================================
                    # 429: RATE LIMIT
                    # ============================================================
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 10))

                        # Exponential backoff
                        backoff = min(
                            self._ban_state.backoff_seconds * self.BACKOFF_MULTIPLIER,
                            self.MAX_BACKOFF
                        )
                        self._ban_state.backoff_seconds = backoff
                        self._ban_state.last_429_time = time.time()

                        wait_time = max(retry_after, backoff)

                        logger.warning(
                            "binance_rate_limited",
                            attempt=attempt + 1,
                            retry_after=retry_after,
                            backoff=round(backoff, 1),
                            wait_time=round(wait_time, 1),
                        )

                        if attempt < self.MAX_RETRIES - 1:
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            raise BinanceRateLimitError(
                                f"Rate limit after {self.MAX_RETRIES} retries",
                                retry_after=int(wait_time)
                            )

                    # ============================================================
                    # 5xx: СЕРВЕРНЫЕ ОШИБКИ
                    # ============================================================
                    if resp.status >= 500:
                        error_text = await resp.text()
                        logger.warning(
                            "binance_server_error",
                            status=resp.status,
                            attempt=attempt + 1,
                            error=error_text[:200],
                        )

                        if attempt < self.MAX_RETRIES - 1:
                            await asyncio.sleep(self._ban_state.backoff_seconds)
                            self._ban_state.backoff_seconds = min(
                                self._ban_state.backoff_seconds * self.BACKOFF_MULTIPLIER,
                                self.MAX_BACKOFF
                            )
                            continue
                        else:
                            raise Exception(f"Server error {resp.status}: {error_text[:200]}")

                    # ============================================================
                    # ДРУГИЕ ОШИБКИ (4xx кроме 418, 429)
                    # ============================================================
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise Exception(f"API error {resp.status}: {error_text[:200]}")

                    # ============================================================
                    # УСПЕХ
                    # ============================================================
                    self._ban_state.reset_backoff()
                    return await resp.json()

            except (BinanceBannedError, BinanceRateLimitError):
                raise
            except aiohttp.ClientError as e:
                logger.warning(
                    "binance_client_error",
                    attempt=attempt + 1,
                    error=str(e),
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self._ban_state.backoff_seconds)
                    continue
                raise
            except asyncio.TimeoutError:
                logger.warning(
                    "binance_timeout",
                    attempt=attempt + 1,
                    timeout=self._timeout,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self._ban_state.backoff_seconds)
                    continue
                raise

        raise Exception(f"Failed after {self.MAX_RETRIES} attempts")

    async def get_session(self) -> aiohttp.ClientSession:
        """Получить HTTP сессию (для совместимости)."""
        await self.start()
        return self._session


# Синглтон клиенты для разных API
_futures_client: Optional[BinanceApiClient] = None
_spot_client: Optional[BinanceApiClient] = None


def get_futures_client() -> BinanceApiClient:
    """Получить клиент для Futures API."""
    global _futures_client
    if _futures_client is None:
        _futures_client = BinanceApiClient(base_url=BinanceApiClient.FUTURES_URL)
    return _futures_client


def get_spot_client() -> BinanceApiClient:
    """Получить клиент для Spot API."""
    global _spot_client
    if _spot_client is None:
        _spot_client = BinanceApiClient(base_url=BinanceApiClient.SPOT_URL)
    return _spot_client


async def safe_binance_request(
    endpoint: str,
    params: dict = None,
    api_type: str = "futures",
) -> Optional[dict]:
    """
    Удобная функция для безопасного запроса.

    Args:
        endpoint: Путь API
        params: Query параметры
        api_type: "futures" или "spot"

    Returns:
        JSON ответ или None при ошибке
    """
    client = get_futures_client() if api_type == "futures" else get_spot_client()

    try:
        return await client.get(endpoint, params=params)
    except BinanceBannedError as e:
        logger.error("request_blocked_ip_banned", endpoint=endpoint, retry_after=e.retry_after)
        return None
    except BinanceRateLimitError as e:
        logger.warning("request_rate_limited", endpoint=endpoint, retry_after=e.retry_after)
        return None
    except Exception as e:
        logger.error("request_failed", endpoint=endpoint, error=str(e))
        return None
