# -*- coding: utf-8 -*-
"""
ManipBackTester - Загрузчик исторических данных.

Скачивает klines с Binance Futures API с кэшированием.
Синхронная версия для совместимости.
"""

import json
import time
import hashlib
import glob
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .models import Kline
from .config import BacktestConfig, BINANCE_FUTURES_URL, MAX_KLINES_PER_REQUEST, API_RATE_LIMIT_DELAY


class BinanceDataLoader:
    """
    Загрузчик исторических данных с Binance Futures.

    Особенности:
    - Кэширование на диск
    - Rate limiting
    - Параллельная загрузка через ThreadPool
    """

    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()
        self.base_url = BINANCE_FUTURES_URL
        self._session: Optional[requests.Session] = None

    def __enter__(self):
        self._session = requests.Session()
        return self

    def __exit__(self, *args):
        if self._session:
            self._session.close()

    # Async compatibility - just return self
    async def __aenter__(self):
        self._session = requests.Session()
        return self

    async def __aexit__(self, *args):
        if self._session:
            self._session.close()

    def load_klines(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1m"
    ) -> List[Kline]:
        """
        Загрузить свечи для символа.

        Умная загрузка:
        1. Проверить свой кэш (полное покрытие)
        2. Проверить кэш GenerateHistorySignals
        3. Загрузить из кэша генератора что есть
        4. Скачать ТОЛЬКО недостающие периоды с Binance
        5. Объединить и сохранить в свой кэш
        """
        cache_file = self._get_cache_path(symbol, start_time, end_time, interval)

        # 1. Проверить свой кэш (полное покрытие)
        if cache_file.exists():
            if self.config.verbose:
                print(f"  [CACHE] {symbol} - loading from cache")
            return self._load_from_cache(cache_file)

        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        # 2. Проверить кэш GenerateHistorySignals
        generator_result = self._find_generator_cache(symbol, start_time, end_time)

        if generator_result:
            cache_path, cache_start_ms, cache_end_ms = generator_result

            # 3. Загрузить данные из кэша генератора
            cached_klines = self._load_from_generator_cache(cache_path, start_time, end_time)

            # 4. Определить недостающие периоды
            missing_before = None
            missing_after = None

            if cache_start_ms > start_ms:
                # Нужны данные ДО начала кэша
                missing_before = (start_ms, cache_start_ms)

            if cache_end_ms < end_ms:
                # Нужны данные ПОСЛЕ конца кэша
                missing_after = (cache_end_ms, end_ms)

            # 5. Скачать недостающие периоды
            all_klines = list(cached_klines)

            if missing_before:
                before_start = datetime.fromtimestamp(missing_before[0] / 1000, tz=timezone.utc)
                before_end = datetime.fromtimestamp(missing_before[1] / 1000, tz=timezone.utc)
                if self.config.verbose:
                    print(f"  [GEN-CACHE+API] {symbol} - cache + downloading missing BEFORE")
                before_klines = self._fetch_from_binance(symbol, before_start, before_end, interval)
                all_klines = before_klines + all_klines

            if missing_after:
                after_start = datetime.fromtimestamp(missing_after[0] / 1000, tz=timezone.utc)
                after_end = datetime.fromtimestamp(missing_after[1] / 1000, tz=timezone.utc)
                if self.config.verbose:
                    if not missing_before:
                        print(f"  [GEN-CACHE+API] {symbol} - cache + downloading missing AFTER")
                after_klines = self._fetch_from_binance(symbol, after_start, after_end, interval)
                all_klines = all_klines + after_klines

            if not missing_before and not missing_after:
                if self.config.verbose:
                    print(f"  [GEN-CACHE] {symbol} - full coverage from generator cache")

            # 6. Сортировать и убрать дубликаты
            if all_klines:
                all_klines = self._merge_klines(all_klines)
                # Сохранить в свой кэш для будущего использования
                self._save_to_cache(cache_file, all_klines)

            return all_klines

        # Нет кэша генератора - скачать всё с Binance
        if self.config.verbose:
            print(f"  [API] {symbol} - downloading from Binance...")

        klines = self._fetch_from_binance(symbol, start_time, end_time, interval)

        # Сохранить в кэш
        if klines:
            self._save_to_cache(cache_file, klines)

        return klines

    def _merge_klines(self, klines: List[Kline]) -> List[Kline]:
        """
        Объединить klines: сортировка + удаление дубликатов по timestamp.
        """
        # Используем dict для удаления дубликатов (последний выигрывает)
        seen = {}
        for k in klines:
            seen[k.timestamp] = k

        # Сортируем по timestamp
        return sorted(seen.values(), key=lambda x: x.timestamp)

    # Async wrapper for compatibility
    async def load_klines_async(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1m"
    ) -> List[Kline]:
        return self.load_klines(symbol, start_time, end_time, interval)

    def load_all_symbols(
        self,
        symbols: Set[str],
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, List[Kline]]:
        """
        Загрузить данные для всех символов.
        """
        # Добавить padding
        padded_start = start_time - timedelta(hours=self.config.data_padding_before_hours)
        padded_end = end_time + timedelta(hours=self.config.data_padding_after_hours)

        if self.config.verbose:
            print(f"\nLoading klines for {len(symbols)} symbols...")
            print(f"Period: {padded_start.strftime('%Y-%m-%d %H:%M')} to {padded_end.strftime('%Y-%m-%d %H:%M')}")

        all_klines = {}

        # Загрузить последовательно для стабильности
        for symbol in symbols:
            try:
                klines = self.load_klines(
                    symbol,
                    padded_start,
                    padded_end,
                    self.config.kline_interval
                )
                all_klines[symbol] = klines
                if self.config.verbose and klines:
                    print(f"  [OK] {symbol}: {len(klines)} klines")
            except Exception as e:
                print(f"  [ERROR] {symbol}: {e}")

        return all_klines

    # Async wrapper
    async def load_all_symbols_async(
        self,
        symbols: Set[str],
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, List[Kline]]:
        return self.load_all_symbols(symbols, start_time, end_time)

    def _fetch_from_binance(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        interval: str
    ) -> List[Kline]:
        """
        Скачать klines с Binance Futures API.
        """
        klines = []
        current_start = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        session = self._session or requests.Session()

        while current_start < end_ms:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": MAX_KLINES_PER_REQUEST
            }

            try:
                resp = session.get(
                    f"{self.base_url}/fapi/v1/klines",
                    params=params,
                    timeout=30
                )

                if resp.status_code == 429:
                    print(f"  [RATE LIMIT] {symbol} - waiting 60s")
                    time.sleep(60)
                    continue

                if resp.status_code != 200:
                    print(f"  [ERROR] {symbol}: HTTP {resp.status_code} - {resp.text[:100]}")
                    break

                data = resp.json()

                if not data:
                    break

                for k in data:
                    kline = Kline(
                        timestamp=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                        open=Decimal(str(k[1])),
                        high=Decimal(str(k[2])),
                        low=Decimal(str(k[3])),
                        close=Decimal(str(k[4])),
                        volume=Decimal(str(k[5])),
                        quote_volume=Decimal(str(k[7])),
                        trades_count=int(k[8]),
                        taker_buy_volume=Decimal(str(k[9])),
                        taker_buy_quote_volume=Decimal(str(k[10]))
                    )
                    klines.append(kline)

                # Следующий batch
                current_start = data[-1][0] + 1

                # Rate limit
                time.sleep(API_RATE_LIMIT_DELAY)

            except requests.RequestException as e:
                print(f"  [ERROR] {symbol}: {e}")
                break

        return klines

    def _get_cache_path(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        interval: str
    ) -> Path:
        """Получить путь к файлу кэша."""
        key = f"{symbol}_{start_time.strftime('%Y%m%d%H%M')}_{end_time.strftime('%Y%m%d%H%M')}_{interval}"
        filename = f"{symbol}_{hashlib.md5(key.encode()).hexdigest()[:8]}.json"
        return self.config.cache_dir / filename

    def _find_generator_cache(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime
    ) -> Optional[Tuple[Path, int, int]]:
        """
        Найти файл кэша генератора который пересекается с нужным периодом.

        Поддерживает 2 формата:
        1. NEW (SmartCache): {generator_dir}/binance/{symbol}/klines.json + klines.meta.json
        2. OLD (legacy): {generator_dir}/{symbol}_klines_{start_ms}_{end_ms}.json

        Returns:
            Tuple(path, cache_start_ms, cache_end_ms) или None
        """
        generator_dir = self.config.generator_cache_dir
        if not generator_dir.exists():
            return None

        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        # =================================================================
        # 1. Пробуем NEW формат (SmartCache): binance/{symbol}/klines.json
        # =================================================================
        smart_cache_dir = generator_dir / "binance" / symbol
        smart_cache_file = smart_cache_dir / "klines.json"
        smart_cache_meta = smart_cache_dir / "klines.meta.json"

        if smart_cache_file.exists() and smart_cache_meta.exists():
            try:
                with open(smart_cache_meta, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                    file_start = meta.get("min_ts", 0)
                    file_end = meta.get("max_ts", 0)

                    # Проверяем пересечение
                    overlap_start = max(file_start, start_ms)
                    overlap_end = min(file_end, end_ms)
                    overlap = overlap_end - overlap_start

                    if overlap > 3600000:  # > 1 hour
                        return (smart_cache_file, file_start, file_end)
            except (json.JSONDecodeError, IOError, KeyError):
                pass

        # =================================================================
        # 2. Fallback на OLD формат: {symbol}_klines_{start}_{end}.json
        # =================================================================
        pattern = str(generator_dir / f"{symbol}_klines_*.json")
        files = glob.glob(pattern)

        best_file = None
        best_overlap = 0
        best_range = (0, 0)

        for file_path in files:
            filename = Path(file_path).stem
            parts = filename.split("_")
            if len(parts) >= 4 and parts[1] == "klines":
                try:
                    file_start = int(parts[2])
                    file_end = int(parts[3])

                    overlap_start = max(file_start, start_ms)
                    overlap_end = min(file_end, end_ms)
                    overlap = overlap_end - overlap_start

                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_file = Path(file_path)
                        best_range = (file_start, file_end)
                except (ValueError, IndexError):
                    continue

        if best_overlap > 3600000:
            return (best_file, best_range[0], best_range[1])

        return None

    def _load_from_generator_cache(
        self,
        cache_file: Path,
        start_time: datetime,
        end_time: datetime
    ) -> List[Kline]:
        """
        Загрузить klines из кэша генератора.

        Формат генератора: timestamp = int (ms)
        Наш формат: timestamp = ISO string
        """
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  [WARN] Failed to read generator cache: {e}")
            return []

        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        klines = []
        for k in data:
            # Генератор хранит timestamp как int (ms)
            ts = k.get("timestamp")
            if isinstance(ts, int):
                # Фильтруем по времени
                if ts < start_ms or ts > end_ms:
                    continue
                timestamp = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            elif isinstance(ts, str):
                # Если вдруг ISO формат
                timestamp = datetime.fromisoformat(ts)
                ts_ms = int(timestamp.timestamp() * 1000)
                if ts_ms < start_ms or ts_ms > end_ms:
                    continue
            else:
                continue

            kline = Kline(
                timestamp=timestamp,
                open=Decimal(str(k["open"])),
                high=Decimal(str(k["high"])),
                low=Decimal(str(k["low"])),
                close=Decimal(str(k["close"])),
                volume=Decimal(str(k["volume"])),
                quote_volume=Decimal(str(k.get("quote_volume", 0))),
                trades_count=int(k.get("trades_count", 0)),
                taker_buy_volume=Decimal(str(k.get("taker_buy_volume", 0))),
                taker_buy_quote_volume=Decimal(str(k.get("taker_buy_quote_volume", 0)))
            )
            klines.append(kline)

        return klines

    def _load_from_cache(self, cache_file: Path) -> List[Kline]:
        """Загрузить klines из кэша."""
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        klines = []
        for k in data:
            kline = Kline(
                timestamp=datetime.fromisoformat(k["timestamp"]),
                open=Decimal(str(k["open"])),
                high=Decimal(str(k["high"])),
                low=Decimal(str(k["low"])),
                close=Decimal(str(k["close"])),
                volume=Decimal(str(k["volume"])),
                quote_volume=Decimal(str(k.get("quote_volume", 0))),
                trades_count=int(k.get("trades_count", 0)),
                taker_buy_volume=Decimal(str(k.get("taker_buy_volume", 0))),
                taker_buy_quote_volume=Decimal(str(k.get("taker_buy_quote_volume", 0)))
            )
            klines.append(kline)

        return klines

    def _save_to_cache(self, cache_file: Path, klines: List[Kline]) -> None:
        """Сохранить klines в кэш."""
        data = []
        for k in klines:
            data.append({
                "timestamp": k.timestamp.isoformat(),
                "open": str(k.open),
                "high": str(k.high),
                "low": str(k.low),
                "close": str(k.close),
                "volume": str(k.volume),
                "quote_volume": str(k.quote_volume),
                "trades_count": k.trades_count,
                "taker_buy_volume": str(k.taker_buy_volume),
                "taker_buy_quote_volume": str(k.taker_buy_quote_volume)
            })

        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f)

    def clear_cache(self) -> int:
        """Очистить кэш."""
        count = 0
        for f in self.config.cache_dir.glob("*.json"):
            f.unlink()
            count += 1
        return count
