# -*- coding: utf-8 -*-
"""
BinanceHistoryDownloader - Downloads all historical data needed for signal generation.

Features:
- Smart caching: one file per symbol, reuses data across different date ranges
- Incremental downloads: only fetches missing data
- Rate limiting & retry logic

Data sources:
1. Klines (1m candles) - /fapi/v1/klines
2. Open Interest History (5m) - /futures/data/openInterestHist
3. Funding Rate History - /fapi/v1/fundingRate
4. Long/Short Ratio (5m) - /futures/data/globalLongShortAccountRatio
5. Orderbook (current snapshot) - /fapi/v1/depth
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from smart_cache import SmartCache


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class SymbolHistoryData:
    """Container for all historical data of a symbol."""
    symbol: str
    klines: List[dict] = field(default_factory=list)
    oi_history: List[dict] = field(default_factory=list)
    funding_history: List[dict] = field(default_factory=list)
    ls_ratio_history: List[dict] = field(default_factory=list)
    orderbook_snapshot: Optional[dict] = None


# =============================================================================
# DOWNLOADER
# =============================================================================

class BinanceHistoryDownloader:
    """
    Downloads historical data from Binance Futures API.

    Features:
    - Smart caching: reuses data across different date ranges
    - Incremental: only downloads missing data
    - Rate limiting: 0.1s delay between requests
    - Retry: 3 attempts on HTTP 429 or 5xx errors
    - Pagination: handles large date ranges automatically
    """

    BASE_URL = "https://fapi.binance.com"

    # Limits per request
    KLINES_LIMIT = 1500
    OI_LIMIT = 500
    FUNDING_LIMIT = 1000
    LS_RATIO_LIMIT = 500

    # Excluded base assets (не подходят для нашего вида трейда)
    EXCLUDED_BASES = {
        # Major coins (слишком стабильные/ликвидные)
        "BTC", "ETH", "BNB", "XRP", "SOL", "ADA", "DOGE", "AVAX", "LINK", "DOT",
        # Stablecoins
        "USDT", "USDC", "BUSD", "TUSD", "DAI", "FDUSD", "USDP", "USDD",
        # Wrapped tokens
        "WBTC", "WETH", "WBNB",
        # Legacy/Large caps
        "BCH", "ETC", "AAVE", "FIL", "NEAR", "AXS",
        # Meme multiplier tokens
        "1000PEPE", "1000SHIB", "1000FLOKI", "1000BONK", "1000LUNC",
    }

    # Excluded symbol patterns (акции, комодитис, индексы)
    EXCLUDED_PATTERNS = ("PAXG", "GOLD", "AAPL", "TSLA", "GOOG", "AMZN", "COIN", "MSTR")

    # Rate limiting
    REQUEST_DELAY = 0.1  # seconds

    # Retry settings
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds

    # Supported data intervals
    VALID_INTERVALS = ("daily", "5m", "1m")

    # Interval mapping: our name -> Binance API parameter
    INTERVAL_MAP = {
        "daily": {"klines": "1d", "oi": "1d", "ls": "1d"},
        "5m": {"klines": "5m", "oi": "5m", "ls": "5m"},
        "1m": {"klines": "1m", "oi": "5m", "ls": "5m"},  # OI/LS don't support 1m
    }

    def __init__(self, cache_dir: str = "cache", data_interval: str = "daily"):
        """
        Initialize downloader.

        Args:
            cache_dir: Cache directory path
            data_interval: Data granularity - "daily", "5m", or "1m"
                          For daily trading, use "daily" (default)
        """
        if data_interval not in self.VALID_INTERVALS:
            raise ValueError(f"Invalid data_interval: {data_interval}. Must be one of {self.VALID_INTERVALS}")

        self.data_interval = data_interval
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache = SmartCache(str(self.cache_dir))
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "BinanceHistoryDownloader/2.0"
        })

        # Auto-migrate old cache format if exists
        self._migrate_old_cache_if_needed()

    def _migrate_old_cache_if_needed(self) -> None:
        """Migrate old cache format to new smart cache if needed."""
        import re

        # Check if old-format files exist (SYMBOL_TYPE_START_END.json)
        pattern = re.compile(r'^[A-Z0-9]+_(klines|oi|funding|ls_ratio)_\d+_\d+\.json$')

        old_files = [f for f in self.cache_dir.iterdir()
                     if f.is_file() and pattern.match(f.name)]

        if old_files:
            print(f"\n[MIGRATE] Found {len(old_files)} old cache files, converting to smart cache...", flush=True)
            migrated = self.cache.migrate_old_cache(str(self.cache_dir))
            print(f"[MIGRATE] Migrated {migrated} symbol/type combinations\n", flush=True)

            # Optionally delete old files after successful migration
            for f in old_files:
                try:
                    f.unlink()
                except:
                    pass

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def get_active_symbols(self, top_n: int = 500) -> List[str]:
        """
        Get top-N active USDT perpetual futures symbols by volume.

        Args:
            top_n: Number of symbols to return (default 100)

        Returns:
            List of symbol names (e.g., ["BTCUSDT", "ETHUSDT", ...])
        """
        print(f"[INFO] Fetching active symbols from exchangeInfo...")

        # Get exchange info
        exchange_info = self._request("/fapi/v1/exchangeInfo")
        if not exchange_info:
            raise RuntimeError("Failed to fetch exchangeInfo")

        # Filter: USDT pairs, TRADING status, PERPETUAL contract
        symbols_data = []
        for s in exchange_info.get("symbols", []):
            if (s.get("quoteAsset") == "USDT" and
                s.get("status") == "TRADING" and
                s.get("contractType") == "PERPETUAL"):
                symbol = s["symbol"]
                base_asset = s.get("baseAsset", "")

                # Skip excluded base assets
                if base_asset in self.EXCLUDED_BASES:
                    continue

                # Skip excluded patterns (stocks, commodities)
                if any(p in symbol for p in self.EXCLUDED_PATTERNS):
                    continue

                symbols_data.append(symbol)

        print(f"[INFO] Found {len(symbols_data)} USDT perpetual symbols (after exclusions)")

        # Get 24h ticker to sort by volume
        print(f"[INFO] Fetching 24h tickers for volume ranking...")
        tickers = self._request("/fapi/v1/ticker/24hr")
        if not tickers:
            # Fallback: return first top_n symbols
            return symbols_data[:top_n]

        # Build volume map
        volume_map = {}
        for t in tickers:
            symbol = t.get("symbol", "")
            quote_volume = float(t.get("quoteVolume", 0))
            volume_map[symbol] = quote_volume

        # Sort by volume descending
        symbols_data.sort(key=lambda s: volume_map.get(s, 0), reverse=True)

        result = symbols_data[:top_n]
        print(f"[INFO] Selected top {len(result)} symbols by volume")

        return result

    def download_all(
        self,
        symbols: List[str],
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, SymbolHistoryData]:
        """
        Download all historical data for given symbols and time period.

        Args:
            symbols: List of symbol names
            start_time: Start of period (UTC)
            end_time: End of period (UTC)

        Returns:
            Dict mapping symbol -> SymbolHistoryData
        """
        # Ensure UTC timezone
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        print(f"\n{'='*60}", flush=True)
        print(f"DOWNLOADING HISTORICAL DATA", flush=True)
        print(f"Period: {start_time.strftime('%Y-%m-%d %H:%M')} -> {end_time.strftime('%Y-%m-%d %H:%M')}", flush=True)
        print(f"Symbols: {len(symbols)}", flush=True)
        print(f"{'='*60}\n", flush=True)

        results = {}

        for idx, symbol in enumerate(symbols, 1):
            print(f"\n[{idx}/{len(symbols)}] {symbol}", flush=True)
            print("-" * 40, flush=True)

            data = SymbolHistoryData(symbol=symbol)

            # 1. Download klines (1m)
            data.klines = self._download_klines(symbol, start_ms, end_ms)

            # 2. Download OI history (5m)
            data.oi_history = self._download_oi_history(symbol, start_ms, end_ms)

            # 3. Download funding rate history
            data.funding_history = self._download_funding_history(symbol, start_ms, end_ms)

            # 4. Download L/S ratio history (5m)
            data.ls_ratio_history = self._download_ls_ratio(symbol, start_ms, end_ms)

            # 5. Get current orderbook snapshot (cannot get historical)
            data.orderbook_snapshot = self._download_orderbook(symbol)

            results[symbol] = data

            print(f"  -> Klines: {len(data.klines)}", flush=True)
            print(f"  -> OI: {len(data.oi_history)}", flush=True)
            print(f"  -> Funding: {len(data.funding_history)}", flush=True)
            print(f"  -> L/S Ratio: {len(data.ls_ratio_history)}", flush=True)
            print(f"  -> Orderbook: {'OK' if data.orderbook_snapshot else 'FAILED'}", flush=True)

        print(f"\n{'='*60}", flush=True)
        print(f"DOWNLOAD COMPLETE: {len(results)} symbols", flush=True)
        print(f"{'='*60}\n", flush=True)

        return results

    # =========================================================================
    # DOWNLOAD METHODS WITH SMART CACHING
    # =========================================================================

    def _download_klines(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int
    ) -> List[dict]:
        """Download 1-minute klines with smart caching."""

        # Get interval for display and cache key
        klines_interval = self.INTERVAL_MAP[self.data_interval]["klines"]
        cache_key = f"klines_{klines_interval}"

        # Check cache for existing data and gaps
        cached_data, gap_start, gap_end = self.cache.load_data(
            symbol, cache_key, start_ms, end_ms, "timestamp"
        )

        # If fully cached
        if cached_data is not None and gap_start == 0 and gap_end == 0:
            print(f"  [CACHE] Klines ({klines_interval}): {len(cached_data)} records")
            return cached_data

        # Need to download something
        print(f"  [BINANCE] Klines ({klines_interval})...", end=" ", flush=True)

        new_klines = []

        # Download missing data
        if gap_start > 0 and gap_end > 0:
            new_klines = self._fetch_klines(symbol, gap_start, gap_end)

        # Merge cached + new
        if cached_data:
            all_klines = cached_data + new_klines
        else:
            all_klines = new_klines

        # Sort by timestamp
        all_klines.sort(key=lambda x: x["timestamp"])

        # Remove duplicates
        seen = set()
        unique_klines = []
        for k in all_klines:
            if k["timestamp"] not in seen:
                seen.add(k["timestamp"])
                unique_klines.append(k)

        # Filter to requested range
        result = [k for k in unique_klines if start_ms <= k["timestamp"] <= end_ms]

        print(f"{len(result)} records")

        # Save to cache (all data, not just requested range)
        if new_klines:
            self.cache.save_data(symbol, cache_key, unique_klines, "timestamp")

        return result

    def _fetch_klines(self, symbol: str, start_ms: int, end_ms: int) -> List[dict]:
        """Fetch klines from API (internal helper)."""
        all_klines = []
        current_start = start_ms

        # Use interval from mapping (daily -> "1d", 5m -> "5m", etc.)
        klines_interval = self.INTERVAL_MAP[self.data_interval]["klines"]

        while current_start < end_ms:
            params = {
                "symbol": symbol,
                "interval": klines_interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": self.KLINES_LIMIT
            }

            data = self._request("/fapi/v1/klines", params)

            if not data:
                break

            for k in data:
                kline = {
                    "timestamp": k[0],
                    "open": k[1],
                    "high": k[2],
                    "low": k[3],
                    "close": k[4],
                    "volume": k[5],
                    "close_time": k[6],
                    "quote_volume": k[7],
                    "trades_count": k[8],
                    "taker_buy_volume": k[9],
                    "taker_buy_quote_volume": k[10]
                }
                all_klines.append(kline)

            if len(data) < self.KLINES_LIMIT:
                break

            # Move start to last candle close time + 1ms
            current_start = data[-1][6] + 1

        return all_klines

    def _download_oi_history(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int
    ) -> List[dict]:
        """Download Open Interest history with smart caching."""

        # Get period for display and cache key
        oi_period = self.INTERVAL_MAP[self.data_interval]["oi"]
        cache_key = f"oi_{oi_period}"

        # Check cache
        cached_data, gap_start, gap_end = self.cache.load_data(
            symbol, cache_key, start_ms, end_ms, "timestamp"
        )

        if cached_data is not None and gap_start == 0 and gap_end == 0:
            print(f"  [CACHE] OI ({oi_period}): {len(cached_data)} records")
            return cached_data

        print(f"  [BINANCE] OI ({oi_period}, last 30d)...", end=" ", flush=True)

        new_oi = []
        if gap_start > 0 and gap_end > 0:
            new_oi = self._fetch_oi_history(symbol, gap_start, gap_end)

        # Merge
        if cached_data:
            all_oi = cached_data + new_oi
        else:
            all_oi = new_oi

        # Dedupe and sort
        oi_map = {r["timestamp"]: r for r in all_oi}
        all_oi = sorted(oi_map.values(), key=lambda x: x["timestamp"])

        result = [r for r in all_oi if start_ms <= r["timestamp"] <= end_ms]

        print(f"{len(result)} records")

        if new_oi:
            self.cache.save_data(symbol, cache_key, all_oi, "timestamp")

        return result

    def _fetch_oi_history(self, symbol: str, start_ms: int, end_ms: int) -> List[dict]:
        """Fetch OI history from API (paginates backwards)."""
        all_oi = []
        current_end = end_ms

        # Use period from mapping (daily -> "1d", 5m -> "5m")
        oi_period = self.INTERVAL_MAP[self.data_interval]["oi"]

        while current_end > start_ms:
            params = {
                "symbol": symbol,
                "period": oi_period,
                "endTime": current_end,
                "limit": self.OI_LIMIT
            }

            data = self._request("/futures/data/openInterestHist", params)

            if not data:
                break

            for item in data:
                oi_record = {
                    "symbol": item.get("symbol"),
                    "timestamp": item.get("timestamp"),
                    "sumOpenInterest": item.get("sumOpenInterest"),
                    "sumOpenInterestValue": item.get("sumOpenInterestValue")
                }
                all_oi.append(oi_record)

            oldest_ts = data[0]["timestamp"]
            if oldest_ts <= start_ms:
                break

            if len(data) < self.OI_LIMIT:
                break

            current_end = oldest_ts - 1

        return all_oi

    def _download_funding_history(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int
    ) -> List[dict]:
        """Download Funding Rate history with smart caching."""

        cached_data, gap_start, gap_end = self.cache.load_data(
            symbol, "funding", start_ms, end_ms, "fundingTime"
        )

        if cached_data is not None and gap_start == 0 and gap_end == 0:
            print(f"  [CACHE] Funding (8h): {len(cached_data)} records")
            return cached_data

        print(f"  [BINANCE] Funding (8h, full history)...", end=" ", flush=True)

        new_funding = []
        if gap_start > 0 and gap_end > 0:
            new_funding = self._fetch_funding_history(symbol, gap_start, gap_end)

        if cached_data:
            all_funding = cached_data + new_funding
        else:
            all_funding = new_funding

        # Dedupe and sort
        funding_map = {r["fundingTime"]: r for r in all_funding}
        all_funding = sorted(funding_map.values(), key=lambda x: x["fundingTime"])

        result = [r for r in all_funding if start_ms <= r["fundingTime"] <= end_ms]

        print(f"{len(result)} records")

        if new_funding:
            self.cache.save_data(symbol, "funding", all_funding, "fundingTime")

        return result

    def _fetch_funding_history(self, symbol: str, start_ms: int, end_ms: int) -> List[dict]:
        """Fetch funding history from API."""
        all_funding = []
        current_start = start_ms

        while current_start < end_ms:
            params = {
                "symbol": symbol,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": self.FUNDING_LIMIT
            }

            data = self._request("/fapi/v1/fundingRate", params)

            if not data:
                break

            for item in data:
                funding_record = {
                    "symbol": item.get("symbol"),
                    "fundingTime": item.get("fundingTime"),
                    "fundingRate": item.get("fundingRate"),
                    "markPrice": item.get("markPrice")
                }
                all_funding.append(funding_record)

            if len(data) < self.FUNDING_LIMIT:
                break

            current_start = data[-1]["fundingTime"] + 1

        return all_funding

    def _download_ls_ratio(
        self,
        symbol: str,
        start_ms: int,
        end_ms: int
    ) -> List[dict]:
        """Download Long/Short Ratio history with smart caching."""

        # Get period for display and cache key
        ls_period = self.INTERVAL_MAP[self.data_interval]["ls"]
        cache_key = f"ls_ratio_{ls_period}"

        cached_data, gap_start, gap_end = self.cache.load_data(
            symbol, cache_key, start_ms, end_ms, "timestamp"
        )

        if cached_data is not None and gap_start == 0 and gap_end == 0:
            print(f"  [CACHE] L/S ({ls_period}): {len(cached_data)} records")
            return cached_data

        print(f"  [BINANCE] L/S ({ls_period}, last 30d)...", end=" ", flush=True)

        new_ls = []
        if gap_start > 0 and gap_end > 0:
            new_ls = self._fetch_ls_ratio(symbol, gap_start, gap_end)

        if cached_data:
            all_ls = cached_data + new_ls
        else:
            all_ls = new_ls

        # Dedupe and sort
        ls_map = {r["timestamp"]: r for r in all_ls}
        all_ls = sorted(ls_map.values(), key=lambda x: x["timestamp"])

        result = [r for r in all_ls if start_ms <= r["timestamp"] <= end_ms]

        print(f"{len(result)} records")

        if new_ls:
            self.cache.save_data(symbol, cache_key, all_ls, "timestamp")

        return result

    def _fetch_ls_ratio(self, symbol: str, start_ms: int, end_ms: int) -> List[dict]:
        """Fetch L/S ratio from API (paginates backwards)."""
        all_ls = []
        current_end = end_ms

        # Use period from mapping (daily -> "1d", 5m -> "5m")
        ls_period = self.INTERVAL_MAP[self.data_interval]["ls"]

        while current_end > start_ms:
            params = {
                "symbol": symbol,
                "period": ls_period,
                "endTime": current_end,
                "limit": self.LS_RATIO_LIMIT
            }

            data = self._request("/futures/data/globalLongShortAccountRatio", params)

            if not data:
                break

            for item in data:
                ls_record = {
                    "symbol": item.get("symbol"),
                    "timestamp": item.get("timestamp"),
                    "longAccount": item.get("longAccount"),
                    "shortAccount": item.get("shortAccount"),
                    "longShortRatio": item.get("longShortRatio")
                }
                all_ls.append(ls_record)

            oldest_ts = data[0]["timestamp"]
            if oldest_ts <= start_ms:
                break

            if len(data) < self.LS_RATIO_LIMIT:
                break

            current_end = oldest_ts - 1

        return all_ls

    def _download_orderbook(self, symbol: str, limit: int = 20) -> Optional[dict]:
        """Download current orderbook snapshot."""

        print(f"  [BINANCE] Orderbook (current snapshot)...", end=" ", flush=True)

        params = {
            "symbol": symbol,
            "limit": limit
        }

        data = self._request("/fapi/v1/depth", params)

        if not data:
            print("FAILED")
            return None

        orderbook = {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000),
            "bids": data.get("bids", []),
            "asks": data.get("asks", [])
        }

        print("OK")

        return orderbook

    # =========================================================================
    # HTTP UTILITIES
    # =========================================================================

    def _request(
        self,
        endpoint: str,
        params: Optional[dict] = None
    ) -> Optional[Any]:
        """Make HTTP request with retry logic."""
        url = f"{self.BASE_URL}{endpoint}"

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                time.sleep(self.REQUEST_DELAY)

                response = self.session.get(url, params=params, timeout=90)

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", self.RETRY_DELAY))
                    print(f"\n  [WARN] Rate limited, waiting {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    print(f"\n  [WARN] Server error {response.status_code}, retry {attempt}/{self.MAX_RETRIES}")
                    time.sleep(self.RETRY_DELAY)
                    continue

                print(f"\n  [ERROR] HTTP {response.status_code}: {response.text[:200]}")
                return None

            except requests.exceptions.RequestException as e:
                print(f"\n  [ERROR] Request failed: {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY)
                    continue
                return None

        return None


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    from datetime import timedelta

    print("BinanceHistoryDownloader - Test Run")
    print("=" * 60)

    downloader = BinanceHistoryDownloader(cache_dir="cache/binance")

    # Get top 3 symbols for testing
    symbols = downloader.get_active_symbols(top_n=3)
    print(f"\nTest symbols: {symbols}")

    # Download last 2 days of data
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=2)

    print(f"\nTest period: {start_time} -> {end_time}")

    results = downloader.download_all(
        symbols=symbols[:1],
        start_time=start_time,
        end_time=end_time
    )

    for symbol, data in results.items():
        print(f"\n{symbol}:")
        print(f"  Klines: {len(data.klines)}")
        print(f"  OI: {len(data.oi_history)}")
        print(f"  Funding: {len(data.funding_history)}")
        print(f"  L/S Ratio: {len(data.ls_ratio_history)}")
