# -*- coding: utf-8 -*-
"""
CoinalyzeClient - Downloads historical OI and L/S Ratio data from Coinalyze API.

Used for data older than 30 days (Binance API limit).

API Documentation: https://api.coinalyze.net/v1/doc/

Key points:
- Daily interval (1d) has UNLIMITED history
- Intraday intervals limited to 1500-2000 datapoints
- Rate limit: 40 requests/minute
- Symbol format: BTCUSDT_PERP.A (where .A = Binance)

Data sources:
1. Open Interest History - /v1/open-interest-history
2. Long/Short Ratio History - /v1/long-short-ratio-history
3. Funding Rate History - /v1/funding-rate-history (backup)
"""

import os
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


# =============================================================================
# EXCHANGE CODES
# =============================================================================

# Coinalyze exchange codes (suffix for symbol)
EXCHANGE_CODES = {
    "binance": "A",
    "bybit": "6",
    "okx": "K",
    "deribit": "D",
    "bitmex": "M",
    "huobi": "H",
    "kraken": "R",
    "bitfinex": "F",
    "coinbase": "C",
    "gate": "G",
}


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class CoinalyzeOIRecord:
    """Open Interest record from Coinalyze."""
    timestamp: int  # ms
    open: float
    high: float
    low: float
    close: float

    def to_binance_format(self) -> dict:
        """Convert to Binance-compatible format."""
        return {
            "symbol": None,  # Will be filled by caller
            "timestamp": self.timestamp,
            "sumOpenInterest": str(self.close),  # Use close as current value
            "sumOpenInterestValue": str(self.close),  # Same, no USD value
        }


@dataclass
class CoinalyzeLSRecord:
    """Long/Short Ratio record from Coinalyze."""
    timestamp: int  # ms
    open: float
    high: float
    low: float
    close: float

    # NOTE: This dataclass uses OHLC fields but L/S API returns {t, r, l, s}
    # The 'close' field here maps to 'r' (ratio), not actual OHLC
    # For proper parsing, use download_ls_ratio_history() method directly

    def to_binance_format(self) -> dict:
        """Convert to Binance-compatible format."""
        # L/S ratio in Coinalyze: > 1 means more longs, < 1 means more shorts
        # Note: This method assumes 'close' contains the ratio value
        ratio = self.close if self.close > 0 else 1.0
        long_pct = ratio / (1 + ratio)
        short_pct = 1 - long_pct

        return {
            "symbol": None,  # Will be filled by caller
            "timestamp": self.timestamp,
            "longAccount": f"{long_pct:.4f}",
            "shortAccount": f"{short_pct:.4f}",
            "longShortRatio": f"{ratio:.4f}",
        }


# =============================================================================
# CLIENT
# =============================================================================

class CoinalyzeClient:
    """
    Coinalyze API client for historical OI and L/S Ratio data.

    Features:
    - Caching: saves data to cache/ folder
    - Rate limiting: respects 40 req/min limit
    - Automatic symbol conversion: BTCUSDT -> BTCUSDT_PERP.A
    - Daily data for unlimited history
    """

    BASE_URL = "https://api.coinalyze.net/v1"

    # Rate limiting: 40 req/min = 1 req per 1.5 sec
    REQUEST_DELAY = 1.6  # seconds between requests

    # Retry settings
    MAX_RETRIES = 3
    RETRY_DELAY = 5

    # Intervals (Coinalyze format)
    INTERVAL_DAILY = "daily"
    INTERVAL_4H = "4hour"
    INTERVAL_1H = "1hour"

    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_dir: str = "cache/coinalyze",
        exchange: str = "binance"
    ):
        """
        Initialize Coinalyze client.

        Args:
            api_key: Coinalyze API key. If None, reads from COINALYZE_API_KEY env var
            cache_dir: Directory for caching responses
            exchange: Exchange name (default: binance)
        """
        self.api_key = api_key or os.environ.get("COINALYZE_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "Coinalyze API key required. Set COINALYZE_API_KEY env var or pass api_key parameter. "
                "Get free API key at https://coinalyze.net/ (sign up required)"
            )

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.exchange = exchange.lower()
        self.exchange_code = EXCHANGE_CODES.get(self.exchange, "A")

        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "api_key": self.api_key,
        })

        self._last_request_time = 0.0

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def convert_symbol(self, binance_symbol: str) -> str:
        """
        Convert Binance symbol to Coinalyze format.

        Args:
            binance_symbol: e.g., "BTCUSDT"

        Returns:
            Coinalyze symbol: e.g., "BTCUSDT_PERP.A"
        """
        # Remove any existing suffix
        base = binance_symbol.replace("USDT", "").replace("_PERP", "")
        return f"{base}USDT_PERP.{self.exchange_code}"

    def download_oi_history(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "daily"
    ) -> List[dict]:
        """
        Download Open Interest history from Coinalyze.

        Args:
            symbol: Binance symbol (e.g., "BTCUSDT")
            start_time: Start datetime (UTC)
            end_time: End datetime (UTC)
            interval: Time interval ("daily" recommended for full history)

        Returns:
            List of OI records in Binance-compatible format
        """
        coinalyze_symbol = self.convert_symbol(symbol)

        start_ts = int(start_time.timestamp())
        end_ts = int(end_time.timestamp())

        cache_file = self._get_cache_path(
            f"oi_{symbol}_{interval}_{start_ts}_{end_ts}"
        )

        # Check cache
        cached = self._load_cache(cache_file)
        if cached is not None:
            print(f"  [CACHE] OI (daily, Coinalyze): {len(cached)} records")
            return cached

        print(f"  [COINALYZE] OI (>30d, {interval})...", end=" ", flush=True)

        params = {
            "symbols": coinalyze_symbol,
            "interval": interval,
            "from": start_ts,
            "to": end_ts,
        }

        data = self._request("/open-interest-history", params)

        if not data or not isinstance(data, list) or len(data) == 0:
            print("NOT FOUND (symbol not tracked)")
            return []

        # Parse response: [{symbol: ..., history: [{t, o, h, l, c}, ...]}]
        result = []
        for item in data:
            history = item.get("history", [])
            for h in history:
                record = {
                    "symbol": symbol,
                    "timestamp": h.get("t", 0) * 1000,  # Convert to ms
                    "sumOpenInterest": str(h.get("c", 0)),
                    "sumOpenInterestValue": str(h.get("c", 0)),
                }
                result.append(record)

        # Sort by timestamp
        result.sort(key=lambda x: x["timestamp"])

        print(f"{len(result)} records")

        # Save cache
        self._save_cache(cache_file, result)

        return result

    def download_ls_ratio_history(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "daily"
    ) -> List[dict]:
        """
        Download Long/Short Ratio history from Coinalyze.

        Args:
            symbol: Binance symbol (e.g., "BTCUSDT")
            start_time: Start datetime (UTC)
            end_time: End datetime (UTC)
            interval: Time interval ("daily" recommended for full history)

        Returns:
            List of L/S records in Binance-compatible format
        """
        coinalyze_symbol = self.convert_symbol(symbol)

        start_ts = int(start_time.timestamp())
        end_ts = int(end_time.timestamp())

        cache_file = self._get_cache_path(
            f"ls_{symbol}_{interval}_{start_ts}_{end_ts}"
        )

        # Check cache
        cached = self._load_cache(cache_file)
        if cached is not None:
            print(f"  [CACHE] L/S (daily, Coinalyze): {len(cached)} records")
            return cached

        print(f"  [COINALYZE] L/S (>30d, {interval})...", end=" ", flush=True)

        params = {
            "symbols": coinalyze_symbol,
            "interval": interval,
            "from": start_ts,
            "to": end_ts,
        }

        data = self._request("/long-short-ratio-history", params)

        if not data or not isinstance(data, list) or len(data) == 0:
            print("NOT FOUND (symbol not tracked)")
            return []

        # Parse response
        result = []
        for item in data:
            history = item.get("history", [])
            for h in history:
                # Coinalyze L/S API format: {t, r, l, s}
                # r = ratio, l = long %, s = short %
                # API возвращает проценты (например l=53.55 означает 53.55%)
                ratio = h.get("r", 1.0)
                if ratio <= 0:
                    ratio = 1.0

                # Используем готовые значения из API (делим на 100 для decimal формата)
                long_pct = h.get("l", 50.0) / 100.0
                short_pct = h.get("s", 50.0) / 100.0

                record = {
                    "symbol": symbol,
                    "timestamp": h.get("t", 0) * 1000,  # Convert to ms
                    "longAccount": f"{long_pct:.4f}",
                    "shortAccount": f"{short_pct:.4f}",
                    "longShortRatio": f"{ratio:.4f}",
                }
                result.append(record)

        # Sort by timestamp
        result.sort(key=lambda x: x["timestamp"])

        print(f"{len(result)} records")

        # Save cache
        self._save_cache(cache_file, result)

        return result

    def download_funding_history(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1d"
    ) -> List[dict]:
        """
        Download Funding Rate history from Coinalyze (backup for Binance).

        Args:
            symbol: Binance symbol (e.g., "BTCUSDT")
            start_time: Start datetime (UTC)
            end_time: End datetime (UTC)
            interval: Time interval

        Returns:
            List of funding records in Binance-compatible format
        """
        coinalyze_symbol = self.convert_symbol(symbol)

        start_ts = int(start_time.timestamp())
        end_ts = int(end_time.timestamp())

        cache_file = self._get_cache_path(
            f"funding_{symbol}_{interval}_{start_ts}_{end_ts}"
        )

        # Check cache
        cached = self._load_cache(cache_file)
        if cached is not None:
            print(f"  [CACHE] Coinalyze Funding: {len(cached)} records")
            return cached

        print(f"  [COINALYZE] Funding ({interval})...", end=" ", flush=True)

        params = {
            "symbols": coinalyze_symbol,
            "interval": interval,
            "from": start_ts,
            "to": end_ts,
        }

        data = self._request("/funding-rate-history", params)

        if not data or not isinstance(data, list) or len(data) == 0:
            print("EMPTY")
            return []

        # Parse response
        result = []
        for item in data:
            history = item.get("history", [])
            for h in history:
                record = {
                    "symbol": symbol,
                    "fundingTime": h.get("t", 0) * 1000,
                    "fundingRate": str(h.get("c", 0)),
                    "markPrice": "0",  # Not available from Coinalyze
                }
                result.append(record)

        # Sort by timestamp
        result.sort(key=lambda x: x["fundingTime"])

        print(f"{len(result)} records")

        # Save cache
        self._save_cache(cache_file, result)

        return result

    def get_available_symbols(self) -> List[str]:
        """
        Get list of available symbols for the configured exchange.

        Returns:
            List of Binance-style symbols (e.g., ["BTCUSDT", "ETHUSDT"])
        """
        print(f"  [COINALYZE] Fetching available symbols...", end=" ", flush=True)

        data = self._request("/future-markets")

        if not data:
            print("FAILED")
            return []

        # Filter by exchange
        symbols = []
        for market in data:
            exchange = market.get("exchange", "").lower()
            if exchange == self.exchange:
                # Convert back to Binance format
                symbol_on_exchange = market.get("symbol_on_exchange", "")
                if symbol_on_exchange.endswith("USDT"):
                    symbols.append(symbol_on_exchange)

        print(f"{len(symbols)} symbols")
        return symbols

    # =========================================================================
    # HTTP & CACHE UTILITIES
    # =========================================================================

    def _request(
        self,
        endpoint: str,
        params: Optional[dict] = None
    ) -> Optional[Any]:
        """Make HTTP request with rate limiting and retry."""

        # Rate limiting
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)

        url = f"{self.BASE_URL}{endpoint}"

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                self._last_request_time = time.time()

                response = self.session.get(url, params=params, timeout=90)

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 429:
                    # Rate limited
                    retry_after = int(response.headers.get("Retry-After", 60))
                    print(f"\n  [WARN] Coinalyze rate limited, waiting {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                if response.status_code == 401:
                    print(f"\n  [ERROR] Invalid Coinalyze API key")
                    return None

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

    def _get_cache_path(self, key: str) -> Path:
        """Generate cache file path."""
        return self.cache_dir / f"{key}.json"

    def _load_cache(self, cache_path: Path) -> Optional[List[dict]]:
        """Load data from cache if exists."""
        if not cache_path.exists():
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def _save_cache(self, cache_path: Path, data: List[dict]) -> None:
        """Save data to cache."""
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except IOError as e:
            print(f"  [WARN] Failed to save cache: {e}")


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    from datetime import timedelta

    print("CoinalyzeClient - Test Run")
    print("=" * 60)

    # Check for API key
    api_key = os.environ.get("COINALYZE_API_KEY")
    if not api_key:
        print("\nERROR: Set COINALYZE_API_KEY environment variable")
        print("Get free API key at https://coinalyze.net/")
        exit(1)

    client = CoinalyzeClient(api_key=api_key)

    # Test symbol conversion
    print(f"\nSymbol conversion:")
    print(f"  BTCUSDT -> {client.convert_symbol('BTCUSDT')}")
    print(f"  ETHUSDT -> {client.convert_symbol('ETHUSDT')}")

    # Test OI download (last 7 days)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=7)

    print(f"\nTest period: {start_time.date()} -> {end_time.date()}")

    # Download OI
    oi_data = client.download_oi_history(
        symbol="BTCUSDT",
        start_time=start_time,
        end_time=end_time,
        interval="daily"
    )

    if oi_data:
        print(f"  First OI: {oi_data[0]}")
        print(f"  Last OI: {oi_data[-1]}")

    # Download L/S Ratio
    ls_data = client.download_ls_ratio_history(
        symbol="BTCUSDT",
        start_time=start_time,
        end_time=end_time,
        interval="daily"
    )

    if ls_data:
        print(f"  First L/S: {ls_data[0]}")
        print(f"  Last L/S: {ls_data[-1]}")

    print("\nTest complete!")
