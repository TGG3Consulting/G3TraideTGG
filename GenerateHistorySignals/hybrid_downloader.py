# -*- coding: utf-8 -*-
"""
HybridHistoryDownloader - Downloads historical data from Binance + Coinalyze.

Strategy:
1. Binance API (full history): Klines, Funding Rate
2. Binance API (30 days only): OI 5m, L/S Ratio 5m - for recent data
3. Coinalyze API (unlimited): OI daily, L/S Ratio daily - for old data

Output format: ALL data in Binance-compatible format (identical structure).
"""

import gc
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

try:
    from .data_downloader import BinanceHistoryDownloader, SymbolHistoryData
    from .coinalyze_client import CoinalyzeClient
except ImportError:
    from data_downloader import BinanceHistoryDownloader, SymbolHistoryData
    from coinalyze_client import CoinalyzeClient


# =============================================================================
# CONSTANTS
# =============================================================================

# Binance API limits for OI and L/S Ratio
BINANCE_OI_HISTORY_DAYS = 30
BINANCE_LS_HISTORY_DAYS = 30

# Threshold: if requested period starts more than this many days ago,
# use Coinalyze for historical data
COINALYZE_THRESHOLD_DAYS = 25  # Use Coinalyze if data older than 25 days


# =============================================================================
# HYBRID DOWNLOADER
# =============================================================================

class HybridHistoryDownloader:
    """
    Hybrid data downloader using Binance + Coinalyze.

    Automatically selects data source based on date range:
    - Recent data (< 30 days): Binance 5m granularity
    - Historical data (> 30 days): Coinalyze daily granularity

    All output is in Binance-compatible format.
    """

    def __init__(
        self,
        cache_dir: str = "cache",
        coinalyze_api_key: Optional[str] = None,
        data_interval: str = "daily"
    ):
        """
        Initialize hybrid downloader.

        Args:
            cache_dir: Base cache directory
            coinalyze_api_key: Coinalyze API key (or set COINALYZE_API_KEY env var)
            data_interval: Data granularity - "daily", "5m", or "1m"
                          For daily trading, use "daily" (default)
        """
        self.cache_dir = Path(cache_dir)
        self.data_interval = data_interval

        # Initialize Binance downloader with data interval
        self.binance = BinanceHistoryDownloader(
            cache_dir=str(self.cache_dir / "binance"),
            data_interval=data_interval
        )

        # Initialize Coinalyze client (may raise if no API key)
        self.coinalyze: Optional[CoinalyzeClient] = None
        self._coinalyze_api_key = coinalyze_api_key

    def _ensure_coinalyze(self) -> bool:
        """Lazily initialize Coinalyze client."""
        if self.coinalyze is not None:
            return True

        try:
            self.coinalyze = CoinalyzeClient(
                api_key=self._coinalyze_api_key,
                cache_dir=str(self.cache_dir / "coinalyze")
            )
            return True
        except ValueError as e:
            print(f"  [WARN] Coinalyze not available: {e}")
            print(f"  [WARN] Will use Binance only (limited to 30 days for OI/LS)")
            return False

    def get_active_symbols(self, top_n: int = 500) -> List[str]:
        """Get active symbols from Binance."""
        return self.binance.get_active_symbols(top_n=top_n)

    def download_all(
        self,
        symbols: List[str],
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, SymbolHistoryData]:
        """
        Download all historical data using hybrid approach.

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

        # Calculate how old the data is
        now = datetime.now(timezone.utc)
        days_ago = (now - start_time).days

        # Determine strategy
        use_coinalyze = days_ago > COINALYZE_THRESHOLD_DAYS

        # Get interval info from Binance downloader
        interval_map = self.binance.INTERVAL_MAP[self.data_interval]
        klines_int = interval_map["klines"]
        oi_int = interval_map["oi"]
        ls_int = interval_map["ls"]

        print(f"\n{'='*60}", flush=True)
        print(f"HYBRID HISTORY DOWNLOADER", flush=True)
        print(f"{'='*60}", flush=True)
        print(f"Period: {start_time.strftime('%Y-%m-%d')} -> {end_time.strftime('%Y-%m-%d')}", flush=True)
        print(f"Days ago: {days_ago}", flush=True)
        print(f"Symbols: {len(symbols)}", flush=True)
        print(f"Data interval: {self.data_interval} (klines={klines_int}, oi={oi_int}, ls={ls_int})", flush=True)
        print(f"", flush=True)
        print(f"Data sources:", flush=True)
        print(f"  - Klines:      Binance {klines_int} (full history)", flush=True)
        print(f"  - Funding:     Binance 8h (full history)", flush=True)

        if use_coinalyze and self._ensure_coinalyze():
            print(f"  - OI:          Coinalyze daily (unlimited history)", flush=True)
            print(f"  - L/S Ratio:   Coinalyze daily (unlimited history)", flush=True)
        else:
            print(f"  - OI:          Binance {oi_int} (30 days max)", flush=True)
            print(f"  - L/S Ratio:   Binance {ls_int} (30 days max)", flush=True)
            if days_ago > BINANCE_OI_HISTORY_DAYS:
                print(f"  [WARN] Data older than 30 days - OI/LS may be incomplete!", flush=True)

        print(f"{'='*60}\n", flush=True)

        results = {}

        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        for idx, symbol in enumerate(symbols, 1):
            print(f"\n[{idx}/{len(symbols)}] {symbol}", flush=True)
            print("-" * 40, flush=True)

            data = SymbolHistoryData(symbol=symbol)

            # 1. Klines - always from Binance (full history)
            data.klines = self.binance._download_klines(symbol, start_ms, end_ms)

            # 2. Funding Rate - always from Binance (full history)
            data.funding_history = self.binance._download_funding_history(symbol, start_ms, end_ms)

            # 3. OI History - hybrid
            # Note: Coinalyze API only supports daily interval for OI
            # Binance supports oi_int from INTERVAL_MAP (5m minimum)
            if use_coinalyze and self.coinalyze:
                data.oi_history = self.coinalyze.download_oi_history(
                    symbol=symbol,
                    start_time=start_time,
                    end_time=end_time,
                    interval="daily"  # Coinalyze API limitation
                )
            else:
                data.oi_history = self.binance._download_oi_history(symbol, start_ms, end_ms)

            # 4. L/S Ratio - hybrid
            # Note: Coinalyze API only supports daily interval for L/S
            # Binance supports ls_int from INTERVAL_MAP (5m minimum)
            if use_coinalyze and self.coinalyze:
                data.ls_ratio_history = self.coinalyze.download_ls_ratio_history(
                    symbol=symbol,
                    start_time=start_time,
                    end_time=end_time,
                    interval="daily"  # Coinalyze API limitation
                )
            else:
                data.ls_ratio_history = self.binance._download_ls_ratio(symbol, start_ms, end_ms)

            # 5. Orderbook snapshot (current only, historical not available free)
            data.orderbook_snapshot = self.binance._download_orderbook(symbol)

            results[symbol] = data

            # Summary
            print(f"  -> Klines: {len(data.klines)}", flush=True)
            print(f"  -> Funding: {len(data.funding_history)}", flush=True)
            print(f"  -> OI: {len(data.oi_history)}", flush=True)
            print(f"  -> L/S Ratio: {len(data.ls_ratio_history)}", flush=True)
            print(f"  -> Orderbook: {'OK' if data.orderbook_snapshot else 'N/A'}", flush=True)

        print(f"\n{'='*60}", flush=True)
        print(f"DOWNLOAD COMPLETE: {len(results)} symbols", flush=True)
        print(f"{'='*60}\n", flush=True)

        return results

    def download_with_coinalyze_backfill(
        self,
        symbols: List[str],
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, SymbolHistoryData]:
        """
        Download data with smart backfill strategy:
        - Last 30 days: Binance 5m (high resolution)
        - Before 30 days: Coinalyze daily (lower resolution but available)

        Merges both datasets chronologically.
        """
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        binance_cutoff = now - timedelta(days=BINANCE_OI_HISTORY_DAYS - 2)  # 2 days buffer

        # If all data is within Binance range, use Binance only
        if start_time >= binance_cutoff:
            return self.download_all(symbols, start_time, end_time)

        # Otherwise, we need to merge Coinalyze (old) + Binance (recent)
        if not self._ensure_coinalyze():
            print("[WARN] Coinalyze not available, using Binance only")
            return self.download_all(symbols, start_time, end_time)

        # Get interval info for logging
        interval_map = self.binance.INTERVAL_MAP[self.data_interval]
        klines_int = interval_map["klines"]
        oi_int = interval_map["oi"]
        ls_int = interval_map["ls"]

        # Determine actual date ranges (handle case where end_time < binance_cutoff)
        has_recent_period = end_time > binance_cutoff
        historical_end = min(binance_cutoff, end_time)

        print(f"\n{'='*60}", flush=True)
        print(f"HYBRID DOWNLOAD WITH BACKFILL", flush=True)
        print(f"{'='*60}", flush=True)
        print(f"Full period: {start_time.strftime('%Y-%m-%d')} -> {end_time.strftime('%Y-%m-%d')}", flush=True)
        print(f"Binance cutoff: {binance_cutoff.strftime('%Y-%m-%d')}", flush=True)
        print(f"Data interval: {self.data_interval} (klines={klines_int}, oi={oi_int}, ls={ls_int})", flush=True)
        print(f"", flush=True)
        print(f"Strategy:", flush=True)
        print(f"  Klines: Binance {klines_int} (full period)", flush=True)
        print(f"  OI/L/S Historical ({start_time.strftime('%Y-%m-%d')} - {historical_end.strftime('%Y-%m-%d')}): Coinalyze daily", flush=True)
        if has_recent_period:
            print(f"  OI/L/S Recent ({binance_cutoff.strftime('%Y-%m-%d')} - {end_time.strftime('%Y-%m-%d')}): Binance {oi_int}", flush=True)
        else:
            print(f"  OI/L/S Recent: N/A (end_time before cutoff)", flush=True)
        print(f"{'='*60}\n", flush=True)

        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)
        cutoff_ms = int(binance_cutoff.timestamp() * 1000)
        historical_end_ms = int(historical_end.timestamp() * 1000)

        results = {}

        for idx, symbol in enumerate(symbols, 1):
            print(f"\n[{idx}/{len(symbols)}] {symbol}", flush=True)
            print("-" * 40, flush=True)

            data = SymbolHistoryData(symbol=symbol)

            # 1. Klines - full period from Binance (respects data_interval)
            data.klines = self.binance._download_klines(symbol, start_ms, end_ms)

            # 2. Funding - full period from Binance
            data.funding_history = self.binance._download_funding_history(symbol, start_ms, end_ms)

            # 3. OI - merge Coinalyze (old) + Binance (recent) if applicable
            # Note: Coinalyze only supports daily, Binance supports 5m minimum
            oi_historical = self.coinalyze.download_oi_history(
                symbol=symbol,
                start_time=start_time,
                end_time=historical_end,
                interval="daily"  # Coinalyze only supports daily
            )
            # Only fetch recent from Binance if end_time > binance_cutoff
            if has_recent_period:
                oi_recent = self.binance._download_oi_history(symbol, cutoff_ms, end_ms)
            else:
                oi_recent = []
            data.oi_history = self._merge_history(oi_historical, oi_recent, "timestamp")

            # 4. L/S Ratio - merge Coinalyze (old) + Binance (recent) if applicable
            ls_historical = self.coinalyze.download_ls_ratio_history(
                symbol=symbol,
                start_time=start_time,
                end_time=historical_end,
                interval="daily"  # Coinalyze only supports daily
            )
            if has_recent_period:
                ls_recent = self.binance._download_ls_ratio(symbol, cutoff_ms, end_ms)
            else:
                ls_recent = []
            data.ls_ratio_history = self._merge_history(ls_historical, ls_recent, "timestamp")

            # 5. Orderbook
            data.orderbook_snapshot = self.binance._download_orderbook(symbol)

            results[symbol] = data

            # Summary with source breakdown
            oi_src = f"Coinalyze:{len(oi_historical)} + Binance:{len(oi_recent)}" if oi_historical else f"Binance only:{len(oi_recent)}"
            ls_src = f"Coinalyze:{len(ls_historical)} + Binance:{len(ls_recent)}" if ls_historical else f"Binance only:{len(ls_recent)}"

            print(f"  ✓ Klines: {len(data.klines)} ({klines_int})", flush=True)
            print(f"  ✓ Funding: {len(data.funding_history)} (8h)", flush=True)
            print(f"  ✓ OI: {len(data.oi_history)} = {oi_src}", flush=True)
            print(f"  ✓ L/S: {len(data.ls_ratio_history)} = {ls_src}", flush=True)

        print(f"\n{'='*60}", flush=True)
        print(f"DOWNLOAD COMPLETE: {len(results)} symbols", flush=True)
        print(f"{'='*60}\n", flush=True)

        return results

    def _merge_history(
        self,
        historical: List[dict],
        recent: List[dict],
        timestamp_key: str = "timestamp"
    ) -> List[dict]:
        """
        Merge historical and recent data, removing duplicates.

        Recent data takes priority over historical for overlapping timestamps.
        """
        if not historical:
            return recent
        if not recent:
            return historical

        # Build map from recent data (takes priority)
        recent_map = {r[timestamp_key]: r for r in recent}

        # Add historical data that doesn't overlap
        merged = []
        for h in historical:
            ts = h[timestamp_key]
            if ts not in recent_map:
                merged.append(h)

        # Add all recent data
        merged.extend(recent)

        # Sort by timestamp
        merged.sort(key=lambda x: x[timestamp_key])

        return merged

    def download_symbol(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime
    ) -> SymbolHistoryData:
        """
        Download data for a SINGLE symbol.
        Use this for memory-efficient processing.

        Args:
            symbol: Symbol to download
            start_time: Start of period (UTC)
            end_time: End of period (UTC)

        Returns:
            SymbolHistoryData for this symbol
        """
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        binance_cutoff = now - timedelta(days=BINANCE_OI_HISTORY_DAYS - 2)
        days_ago = (now - start_time).days

        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)
        cutoff_ms = int(binance_cutoff.timestamp() * 1000)

        use_coinalyze = days_ago > COINALYZE_THRESHOLD_DAYS and self._ensure_coinalyze()

        data = SymbolHistoryData(symbol=symbol)

        # 1. Klines - full period from Binance
        data.klines = self.binance._download_klines(symbol, start_ms, end_ms)

        # 2. Funding - full period from Binance
        data.funding_history = self.binance._download_funding_history(symbol, start_ms, end_ms)

        # 3. OI - merge Coinalyze (old) + Binance (recent)
        if use_coinalyze and start_time < binance_cutoff:
            oi_historical = self.coinalyze.download_oi_history(
                symbol=symbol,
                start_time=start_time,
                end_time=binance_cutoff,
                interval="daily"
            )
            oi_recent = self.binance._download_oi_history(symbol, cutoff_ms, end_ms)
            data.oi_history = self._merge_history(oi_historical, oi_recent, "timestamp")
        else:
            data.oi_history = self.binance._download_oi_history(symbol, start_ms, end_ms)

        # 4. L/S Ratio - merge Coinalyze (old) + Binance (recent)
        if use_coinalyze and start_time < binance_cutoff:
            ls_historical = self.coinalyze.download_ls_ratio_history(
                symbol=symbol,
                start_time=start_time,
                end_time=binance_cutoff,
                interval="daily"
            )
            ls_recent = self.binance._download_ls_ratio(symbol, cutoff_ms, end_ms)
            data.ls_ratio_history = self._merge_history(ls_historical, ls_recent, "timestamp")
        else:
            data.ls_ratio_history = self.binance._download_ls_ratio(symbol, start_ms, end_ms)

        # 5. Orderbook
        data.orderbook_snapshot = self.binance._download_orderbook(symbol)

        return data

    def stream_download(
        self,
        symbols: List[str],
        start_time: datetime,
        end_time: datetime
    ) -> Generator[Tuple[int, str, SymbolHistoryData], None, None]:
        """
        Generator that yields symbols one at a time.
        Allows caller to free memory after processing each symbol.

        Yields:
            (index, symbol, data) tuples
        """
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        days_ago = (now - start_time).days
        binance_cutoff = now - timedelta(days=BINANCE_OI_HISTORY_DAYS - 2)

        # Get interval for logging
        interval_map = self.binance.INTERVAL_MAP[self.data_interval]
        klines_int = interval_map["klines"]

        print(f"\n{'='*60}", flush=True)
        print(f"STREAMING DOWNLOAD", flush=True)
        print(f"{'='*60}", flush=True)
        print(f"Period: {start_time.strftime('%Y-%m-%d')} -> {end_time.strftime('%Y-%m-%d')}", flush=True)
        print(f"Symbols: {len(symbols)}", flush=True)
        print(f"Mode: Memory-efficient (one symbol at a time)", flush=True)
        print(f"{'='*60}\n", flush=True)

        for idx, symbol in enumerate(symbols, 1):
            print(f"\n[{idx}/{len(symbols)}] {symbol}", flush=True)
            print("-" * 40, flush=True)

            data = self.download_symbol(symbol, start_time, end_time)

            print(f"  ✓ Klines: {len(data.klines)} ({klines_int})", flush=True)
            print(f"  ✓ Funding: {len(data.funding_history)} (8h)", flush=True)
            print(f"  ✓ OI: {len(data.oi_history)}", flush=True)
            print(f"  ✓ L/S: {len(data.ls_ratio_history)}", flush=True)

            yield (idx, symbol, data)

            # Force garbage collection after yielding
            gc.collect()


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    print("HybridHistoryDownloader - Test Run")
    print("=" * 60)

    # Check for API key
    if not os.environ.get("COINALYZE_API_KEY"):
        print("\nWARNING: COINALYZE_API_KEY not set")
        print("Will use Binance only (limited to 30 days for OI/LS)")
        print("Get free API key at https://coinalyze.net/\n")

    downloader = HybridHistoryDownloader(cache_dir="cache")

    # Get top 3 symbols
    symbols = downloader.get_active_symbols(top_n=3)
    print(f"\nTest symbols: {symbols[:3]}")

    # Test 1: Recent data (should use Binance)
    print("\n" + "="*60)
    print("TEST 1: Recent data (7 days) - should use Binance")
    print("="*60)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=7)

    results = downloader.download_all(
        symbols=symbols[:1],
        start_time=start_time,
        end_time=end_time
    )

    # Test 2: Historical data (should use Coinalyze)
    print("\n" + "="*60)
    print("TEST 2: Historical data (90 days) - should use Coinalyze")
    print("="*60)

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=90)

    results = downloader.download_all(
        symbols=symbols[:1],
        start_time=start_time,
        end_time=end_time
    )

    print("\nTest complete!")
