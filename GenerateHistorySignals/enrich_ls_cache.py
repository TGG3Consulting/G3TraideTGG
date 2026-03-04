# -*- coding: utf-8 -*-
"""
Скрипт обогащения L/S кэша реальными данными из Coinalyze API.

Проблема: старый код использовал h.get("c") для L/S API, но поля "c" не существует.
В результате все записи имеют дефолтные значения (ratio=1.0, long=0.5, short=0.5).

Решение: перезапросить данные с правильным парсингом (поля r, l, s).

Что делает скрипт:
1. Находит все файлы ls_*.json в cache/coinalyze/
2. Парсит имя файла для получения symbol, interval, start_ts, end_ts
3. Делает запрос к Coinalyze API
4. Парсит ответ ПРАВИЛЬНО (r=ratio, l=long%, s=short%)
5. Перезаписывает файл кэша реальными данными

НЕ ТРОГАЕТ: OI кэш, funding кэш, любые другие данные.
"""

import json
import os
import re
import time
from pathlib import Path

import requests

# =============================================================================
# CONFIGURATION
# =============================================================================

# Coinalyze API
API_BASE_URL = "https://api.coinalyze.net/v1"
API_KEY = None  # Will be loaded from config.py

# Cache directory
CACHE_DIR = Path("cache/coinalyze")

# Rate limiting: 40 req/min = 1.5 sec between requests
REQUEST_DELAY = 1.6

# Exchange code for Binance
EXCHANGE_CODE = "A"


# =============================================================================
# API CLIENT
# =============================================================================

class CoinalyzeAPI:
    """Minimal Coinalyze client for L/S enrichment."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "api_key": api_key,
        })
        self._last_request = 0.0

    def get_ls_ratio(self, symbol: str, interval: str, start_ts: int, end_ts: int) -> list:
        """
        Fetch L/S ratio from API with CORRECT parsing.

        API Response format: {t, r, l, s}
        - t = timestamp (seconds)
        - r = ratio (e.g., 1.1529)
        - l = long % (e.g., 53.55 means 53.55%)
        - s = short % (e.g., 46.45 means 46.45%)
        """
        # Rate limiting
        elapsed = time.time() - self._last_request
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)

        # Convert symbol to Coinalyze format
        coinalyze_symbol = f"{symbol}_PERP.{EXCHANGE_CODE}"
        if not symbol.endswith("USDT"):
            coinalyze_symbol = f"{symbol}USDT_PERP.{EXCHANGE_CODE}"

        params = {
            "symbols": coinalyze_symbol,
            "interval": interval,
            "from": start_ts,
            "to": end_ts,
        }

        try:
            self._last_request = time.time()
            response = self.session.get(
                f"{API_BASE_URL}/long-short-ratio-history",
                params=params,
                timeout=90
            )

            if response.status_code == 429:
                # Rate limited - wait and retry
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"    Rate limited, waiting {retry_after}s...")
                time.sleep(retry_after)
                return self.get_ls_ratio(symbol, interval, start_ts, end_ts)

            if response.status_code != 200:
                print(f"    API error: {response.status_code}")
                return []

            data = response.json()

            if not data or not isinstance(data, list) or len(data) == 0:
                return []

            # Parse with CORRECT field names
            result = []
            for item in data:
                history = item.get("history", [])
                for h in history:
                    # CORRECT parsing: r=ratio, l=long%, s=short%
                    ratio = h.get("r", 1.0)
                    if ratio <= 0:
                        ratio = 1.0

                    # API returns percentages (53.55), divide by 100 for decimal
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

            result.sort(key=lambda x: x["timestamp"])
            return result

        except Exception as e:
            print(f"    Request failed: {e}")
            return []


# =============================================================================
# CACHE FILE PARSER
# =============================================================================

def parse_cache_filename(filename: str) -> dict:
    """
    Parse cache filename to extract parameters.

    Format: ls_SYMBOL_INTERVAL_STARTTS_ENDTS.json
    Example: ls_BTCUSDT_daily_1704067200_1769470505.json

    Returns: {symbol, interval, start_ts, end_ts} or None
    """
    pattern = r"ls_([A-Z0-9]+)_(\w+)_(\d+)_(\d+)\.json"
    match = re.match(pattern, filename)

    if not match:
        return None

    return {
        "symbol": match.group(1),
        "interval": match.group(2),
        "start_ts": int(match.group(3)),
        "end_ts": int(match.group(4)),
    }


# =============================================================================
# MAIN
# =============================================================================

def load_api_key() -> str:
    """Load API key from config.py"""
    try:
        # Try importing from config
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from config import AppConfig
        config = AppConfig()
        if config.coinalyze_api_key:
            return config.coinalyze_api_key
    except ImportError:
        pass

    # Try environment variable
    key = os.environ.get("COINALYZE_API_KEY")
    if key:
        return key

    raise ValueError(
        "Coinalyze API key not found. "
        "Set coinalyze_api_key in config.py or COINALYZE_API_KEY environment variable."
    )


def enrich_cache():
    """Main function to enrich L/S cache with real data."""

    print("=" * 60)
    print("L/S Cache Enrichment Script")
    print("=" * 60)

    # Load API key
    try:
        api_key = load_api_key()
        print(f"API Key: {api_key[:8]}...{api_key[-4:]}")
    except ValueError as e:
        print(f"ERROR: {e}")
        return

    # Find all L/S cache files
    if not CACHE_DIR.exists():
        print(f"ERROR: Cache directory not found: {CACHE_DIR}")
        return

    ls_files = list(CACHE_DIR.glob("ls_*.json"))
    print(f"Found {len(ls_files)} L/S cache files")

    if not ls_files:
        print("Nothing to enrich.")
        return

    # Initialize API client
    api = CoinalyzeAPI(api_key)

    # Process each file
    enriched = 0
    skipped = 0
    failed = 0

    for filepath in ls_files:
        filename = filepath.name
        params = parse_cache_filename(filename)

        if not params:
            print(f"  SKIP: Cannot parse filename: {filename}")
            skipped += 1
            continue

        symbol = params["symbol"]
        interval = params["interval"]
        start_ts = params["start_ts"]
        end_ts = params["end_ts"]

        print(f"\n[{enriched + failed + 1}/{len(ls_files)}] {symbol} ({interval})")
        print(f"  Period: {start_ts} -> {end_ts}")

        # Read existing cache file
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  ERROR reading file: {e}")
            failed += 1
            continue

        if not existing_data:
            print(f"  SKIP: Empty file")
            skipped += 1
            continue

        print(f"  Existing records: {len(existing_data)}")

        # Fetch real data from API
        print(f"  Fetching from Coinalyze API...", end=" ", flush=True)
        api_data = api.get_ls_ratio(symbol, interval, start_ts, end_ts)

        if not api_data:
            print("EMPTY (symbol not tracked or API error)")
            failed += 1
            continue

        print(f"{len(api_data)} records from API")

        # Build lookup by timestamp for fast matching
        api_lookup = {record["timestamp"]: record for record in api_data}

        # ENRICH existing data - only update L/S fields
        updated_count = 0
        for record in existing_data:
            ts = record.get("timestamp")
            if ts in api_lookup:
                api_record = api_lookup[ts]
                # Update ONLY L/S fields, keep everything else
                record["longAccount"] = api_record["longAccount"]
                record["shortAccount"] = api_record["shortAccount"]
                record["longShortRatio"] = api_record["longShortRatio"]
                updated_count += 1

        print(f"  Updated {updated_count}/{len(existing_data)} records")

        if updated_count == 0:
            print(f"  WARNING: No records matched by timestamp!")
            failed += 1
            continue

        # Verify data is real (not defaults)
        sample = existing_data[0]
        ratio = float(sample.get("longShortRatio", 1.0))
        if ratio == 1.0:
            print(f"  WARNING: First record still has ratio=1.0")
        else:
            print(f"  Sample: ratio={ratio}, long={sample['longAccount']}, short={sample['shortAccount']}")

        # Save enriched data back to file
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(existing_data, f)

        print(f"  SAVED: {filepath.name}")
        enriched += 1

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Enriched: {enriched}")
    print(f"  Skipped:  {skipped}")
    print(f"  Failed:   {failed}")
    print(f"  Total:    {len(ls_files)}")
    print("\nDone!")


if __name__ == "__main__":
    enrich_cache()
