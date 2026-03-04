# -*- coding: utf-8 -*-
"""
SmartCache - Intelligent caching with incremental updates.

Features:
- One cache file per symbol+datatype (not per date range)
- Metadata tracking (min_ts, max_ts)
- Incremental downloads: only fetches missing data
- Automatic merging of old + new data
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class CacheMetadata:
    """Metadata about cached data range."""
    min_ts: int  # Earliest timestamp in cache (ms)
    max_ts: int  # Latest timestamp in cache (ms)
    count: int   # Number of records


class SmartCache:
    """
    Smart caching system that reuses data across different date ranges.

    Cache structure:
        cache_dir/
            BTCUSDT/
                klines.json
                klines.meta.json  # {min_ts, max_ts, count}
                oi.json
                oi.meta.json
                ...
    """

    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_symbol_dir(self, symbol: str) -> Path:
        """Get cache directory for a symbol."""
        symbol_dir = self.cache_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        return symbol_dir

    def _get_data_path(self, symbol: str, data_type: str) -> Path:
        """Get path to data file."""
        return self._get_symbol_dir(symbol) / f"{data_type}.json"

    def _get_meta_path(self, symbol: str, data_type: str) -> Path:
        """Get path to metadata file."""
        return self._get_symbol_dir(symbol) / f"{data_type}.meta.json"

    def get_metadata(self, symbol: str, data_type: str) -> Optional[CacheMetadata]:
        """Load cache metadata."""
        meta_path = self._get_meta_path(symbol, data_type)
        if not meta_path.exists():
            return None

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return CacheMetadata(
                    min_ts=data["min_ts"],
                    max_ts=data["max_ts"],
                    count=data["count"]
                )
        except (json.JSONDecodeError, KeyError, IOError):
            return None

    def _save_metadata(self, symbol: str, data_type: str, meta: CacheMetadata) -> None:
        """Save cache metadata."""
        meta_path = self._get_meta_path(symbol, data_type)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "min_ts": meta.min_ts,
                "max_ts": meta.max_ts,
                "count": meta.count
            }, f)

    def load_data(
        self,
        symbol: str,
        data_type: str,
        start_ms: int,
        end_ms: int,
        timestamp_field: str = "timestamp"
    ) -> Tuple[Optional[List[dict]], int, int]:
        """
        Load cached data for the requested range.

        Returns:
            (data, gap_start_ms, gap_end_ms)
            - data: cached records within range (or None if no cache)
            - gap_start_ms: start of missing range (0 if no gap at start)
            - gap_end_ms: end of missing range (0 if no gap at end)
        """
        meta = self.get_metadata(symbol, data_type)
        if meta is None:
            # No cache exists - need to download everything
            return None, start_ms, end_ms

        data_path = self._get_data_path(symbol, data_type)
        if not data_path.exists():
            return None, start_ms, end_ms

        # Determine what's missing
        gap_start = 0
        gap_end = 0

        if start_ms < meta.min_ts:
            gap_start = start_ms
            # Gap ends at cache start (or at requested end if smaller)
            gap_end = min(meta.min_ts, end_ms)

        if end_ms > meta.max_ts:
            if gap_start == 0:
                gap_start = meta.max_ts
            gap_end = end_ms

        # If cache fully covers the range
        if start_ms >= meta.min_ts and end_ms <= meta.max_ts:
            gap_start = 0
            gap_end = 0

        # Load and filter data
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                all_data = json.load(f)

            # Filter to requested range
            filtered = [
                r for r in all_data
                if start_ms <= r.get(timestamp_field, 0) <= end_ms
            ]

            return filtered, gap_start, gap_end

        except (json.JSONDecodeError, IOError):
            return None, start_ms, end_ms

    def save_data(
        self,
        symbol: str,
        data_type: str,
        new_data: List[dict],
        timestamp_field: str = "timestamp"
    ) -> None:
        """
        Save data to cache, merging with existing data.

        Args:
            symbol: Trading pair
            data_type: Type of data (klines, oi, funding, ls_ratio)
            new_data: New records to add
            timestamp_field: Field name containing timestamp
        """
        if not new_data:
            return

        data_path = self._get_data_path(symbol, data_type)

        # Load existing data
        existing = []
        if data_path.exists():
            try:
                with open(data_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing = []

        # Merge: use dict to deduplicate by timestamp
        merged_map = {}
        for r in existing:
            ts = r.get(timestamp_field)
            if ts:
                merged_map[ts] = r

        for r in new_data:
            ts = r.get(timestamp_field)
            if ts:
                merged_map[ts] = r  # New data overwrites old

        # Sort by timestamp
        merged = sorted(merged_map.values(), key=lambda x: x.get(timestamp_field, 0))

        # Save data
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(merged, f)

        # Update metadata
        if merged:
            timestamps = [r.get(timestamp_field, 0) for r in merged]
            meta = CacheMetadata(
                min_ts=min(timestamps),
                max_ts=max(timestamps),
                count=len(merged)
            )
            self._save_metadata(symbol, data_type, meta)

    def get_cached_range(
        self,
        symbol: str,
        data_type: str,
        start_ms: int,
        end_ms: int,
        timestamp_field: str = "timestamp"
    ) -> List[dict]:
        """
        Get cached data for a range, returning empty list if not fully cached.
        This is a simpler interface when you just want to check cache.
        """
        data, gap_start, gap_end = self.load_data(
            symbol, data_type, start_ms, end_ms, timestamp_field
        )

        if data is not None and gap_start == 0 and gap_end == 0:
            return data

        return []

    def clear_symbol(self, symbol: str) -> None:
        """Clear all cached data for a symbol."""
        symbol_dir = self._get_symbol_dir(symbol)
        if symbol_dir.exists():
            for f in symbol_dir.iterdir():
                f.unlink()
            symbol_dir.rmdir()

    def migrate_old_cache(self, old_cache_dir: str) -> int:
        """
        Migrate old cache files (per-date-range) to new format (per-symbol).

        Old format: {symbol}_{datatype}_{start_ms}_{end_ms}.json
        New format: {symbol}/{datatype}.json + {datatype}.meta.json

        Returns:
            Number of files migrated
        """
        import re

        old_dir = Path(old_cache_dir)
        if not old_dir.exists():
            return 0

        # Pattern: SYMBOL_TYPE_STARTMS_ENDMS.json
        pattern = re.compile(r'^([A-Z0-9]+)_(klines|oi|funding|ls_ratio)_(\d+)_(\d+)\.json$')

        migrated = 0
        files_by_symbol_type = {}

        # Group files by symbol and type
        for f in old_dir.iterdir():
            if not f.is_file():
                continue

            match = pattern.match(f.name)
            if not match:
                continue

            symbol = match.group(1)
            data_type = match.group(2)
            key = (symbol, data_type)

            if key not in files_by_symbol_type:
                files_by_symbol_type[key] = []
            files_by_symbol_type[key].append(f)

        # Merge files for each symbol/type
        for (symbol, data_type), files in files_by_symbol_type.items():
            print(f"  Migrating {symbol}/{data_type}: {len(files)} files...", end=" ", flush=True)

            # Determine timestamp field
            ts_field = "fundingTime" if data_type == "funding" else "timestamp"

            all_data = {}
            for f in files:
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        records = json.load(fp)
                        for r in records:
                            ts = r.get(ts_field)
                            if ts:
                                all_data[ts] = r
                except (json.JSONDecodeError, IOError):
                    continue

            if all_data:
                merged = sorted(all_data.values(), key=lambda x: x.get(ts_field, 0))
                self.save_data(symbol, data_type, merged, ts_field)
                print(f"{len(merged)} records", flush=True)
                migrated += 1
            else:
                print("empty", flush=True)

        return migrated
