# -*- coding: utf-8 -*-
"""
StateBuilder - Builds FuturesState and SymbolState from historical data.

Takes SymbolHistoryData (klines, OI, funding, L/S ratio) and reconstructs
the state objects at any given timestamp T. These objects are compatible
with AccumulationDetector and RiskCalculator from BinanceFriend.
"""

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from data_downloader import SymbolHistoryData


# =============================================================================
# RECORD DATACLASSES (match BinanceFriend structures)
# =============================================================================

@dataclass
class OIRecord:
    """Open Interest record."""
    open_interest: Decimal
    open_interest_usd: Decimal
    timestamp: datetime


@dataclass
class FundingRecord:
    """Funding rate record."""
    funding_rate: Decimal          # raw value (e.g., 0.0001)
    funding_rate_percent: Decimal  # as percent (e.g., 0.01)
    mark_price: Decimal
    funding_time: str              # ISO format string


@dataclass
class LSRatioRecord:
    """Long/Short ratio record."""
    long_account_pct: Decimal
    short_account_pct: Decimal
    long_short_ratio: Decimal
    timestamp: datetime


# =============================================================================
# STATE DATACLASSES
# =============================================================================

@dataclass
class FuturesState:
    """
    Futures market state at a specific timestamp.
    Matches the structure used by AccumulationDetector.
    """
    symbol: str
    timestamp: datetime

    # Flag
    has_futures: bool = True

    # OI changes (calculated from oi_history)
    oi_change_1m_pct: Decimal = Decimal("0")
    oi_change_5m_pct: Decimal = Decimal("0")
    oi_change_1h_pct: Decimal = Decimal("0")

    # Current OI
    current_oi: Optional[OIRecord] = None

    # OI History (last 12 records for trend analysis)
    oi_history: List[OIRecord] = field(default_factory=list)

    # Funding
    current_funding: Optional[FundingRecord] = None
    funding_history: List[FundingRecord] = field(default_factory=list)

    # L/S Ratio
    current_ls_ratio: Optional[LSRatioRecord] = None

    # Price changes (from klines)
    price_change_5m_pct: Decimal = Decimal("0")
    price_change_1h_pct: Decimal = Decimal("0")

    # Orderbook (neutral - historical data unavailable)
    futures_bid_volume_atr: Decimal = Decimal("0")
    futures_ask_volume_atr: Decimal = Decimal("0")
    futures_book_imbalance_atr: Decimal = Decimal("0")

    # ATR
    futures_atr_1h_pct: Decimal = Decimal("0")
    futures_atr_is_real: bool = True


@dataclass
class SymbolState:
    """
    Spot market state at a specific timestamp.
    Matches the structure used by AccumulationDetector and RiskCalculator.
    """
    symbol: str
    timestamp: datetime

    # Prices
    last_price: Decimal = Decimal("0")
    best_bid: Decimal = Decimal("0")
    best_ask: Decimal = Decimal("0")
    mid_price: Decimal = Decimal("0")
    spread_pct: Decimal = Decimal("0.02")  # Fixed spread for historical

    # Price changes
    price_change_1m_pct: Decimal = Decimal("0")
    price_change_5m_pct: Decimal = Decimal("0")
    price_change_1h_pct: Decimal = Decimal("0")
    price_change_7d_pct: Decimal = Decimal("0")  # 7-дневное изменение для тренда

    # Volume
    volume_1m: Decimal = Decimal("0")
    volume_5m: Decimal = Decimal("0")
    volume_1h: Decimal = Decimal("0")
    avg_volume_1h: Decimal = Decimal("0")
    volume_spike_ratio: Decimal = Decimal("1")

    # Trades
    trade_count_1m: int = 0
    trade_count_5m: int = 0
    buy_ratio_5m: Optional[Decimal] = None

    # Orderbook SPOT (neutral - historical data unavailable)
    bid_volume_atr: Decimal = Decimal("0")
    ask_volume_atr: Decimal = Decimal("0")
    book_imbalance_atr: Optional[Decimal] = None
    bid_volume_20: Decimal = Decimal("0")
    ask_volume_20: Decimal = Decimal("0")
    book_imbalance: Decimal = Decimal("0")

    # ATR
    atr_1h_pct: Decimal = Decimal("0")
    atr_1h_pct_raw: Decimal = Decimal("0")  # For risk calculator
    atr_is_real: bool = True

    # Daily ATR (for SL/TP calculation)
    atr_daily_pct: Decimal = Decimal("0")
    atr_daily_pct_depth: Decimal = Decimal("0")
    atr_daily_is_real: bool = True

    # Price history (last 60 close values)
    price_history: List[Decimal] = field(default_factory=list)

    # Klines for ATR (last 60 1-minute candles as tuples)
    klines_1h: List[tuple] = field(default_factory=list)


# =============================================================================
# STATE BUILDER
# =============================================================================

class StateBuilder:
    """
    Builds FuturesState and SymbolState from historical data.

    Usage:
        builder = StateBuilder(history_data)
        futures_state = builder.build_futures_state("BTCUSDT", timestamp)
        spot_state = builder.build_spot_state("BTCUSDT", timestamp)
    """

    # Time windows in milliseconds
    MS_1M = 60 * 1000
    MS_5M = 5 * 60 * 1000
    MS_1H = 60 * 60 * 1000
    MS_24H = 24 * 60 * 60 * 1000

    def __init__(self, history: Dict[str, SymbolHistoryData]):
        """
        Initialize with historical data.

        Args:
            history: Dict mapping symbol -> SymbolHistoryData
        """
        self.history = history

        # Build indexes for fast lookup
        self._kline_index: Dict[str, List[dict]] = {}
        self._oi_index: Dict[str, List[dict]] = {}
        self._funding_index: Dict[str, List[dict]] = {}
        self._ls_ratio_index: Dict[str, List[dict]] = {}

        # Кэши timestamp для бинарного поиска (инициализируем ДО _build_indexes)
        self._kline_ts_cache: Dict[str, List[int]] = {}
        self._oi_ts_cache: Dict[str, List[int]] = {}
        self._funding_ts_cache: Dict[str, List[int]] = {}
        self._ls_ts_cache: Dict[str, List[int]] = {}

        self._build_indexes()

    def _build_indexes(self):
        """Pre-sort all data by timestamp for binary search."""
        for symbol, data in self.history.items():
            # Sort by timestamp
            self._kline_index[symbol] = sorted(
                data.klines, key=lambda x: x["timestamp"]
            )
            self._oi_index[symbol] = sorted(
                data.oi_history, key=lambda x: x["timestamp"]
            )
            self._funding_index[symbol] = sorted(
                data.funding_history, key=lambda x: x["fundingTime"]
            )
            self._ls_ratio_index[symbol] = sorted(
                data.ls_ratio_history, key=lambda x: x["timestamp"]
            )

        # Строим кэши timestamp для bisect
        for symbol in self.history:
            self._kline_ts_cache[symbol] = [
                k["timestamp"] for k in self._kline_index[symbol]
            ]
            self._oi_ts_cache[symbol] = [
                r["timestamp"] for r in self._oi_index[symbol]
            ]
            self._funding_ts_cache[symbol] = [
                r["fundingTime"] for r in self._funding_index[symbol]
            ]
            self._ls_ts_cache[symbol] = [
                r["timestamp"] for r in self._ls_ratio_index[symbol]
            ]

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def build_futures_state(
        self,
        symbol: str,
        timestamp: datetime
    ) -> Optional[FuturesState]:
        """
        Build FuturesState for a symbol at given timestamp.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            timestamp: Point in time (UTC)

        Returns:
            FuturesState or None if no data available
        """
        if symbol not in self.history:
            return None

        ts_ms = self._to_ms(timestamp)

        state = FuturesState(symbol=symbol, timestamp=timestamp)

        # Get klines up to timestamp
        klines = self._get_klines_up_to(symbol, ts_ms)
        if not klines:
            return None

        # Calculate ATR from klines
        state.futures_atr_1h_pct = self._calculate_atr(klines, periods=14)
        state.futures_atr_is_real = len(klines) >= 14

        # Price changes from klines
        state.price_change_5m_pct = self._calc_price_change(klines, self.MS_5M)
        state.price_change_1h_pct = self._calc_price_change(klines, self.MS_1H)

        # OI data
        oi_records = self._oi_index.get(symbol, [])
        current_oi = self._find_record_at(oi_records, ts_ms, "timestamp")

        if current_oi:
            state.current_oi = OIRecord(
                open_interest=Decimal(str(current_oi.get("sumOpenInterest", 0))),
                open_interest_usd=Decimal(str(current_oi.get("sumOpenInterestValue", 0))),
                timestamp=self._from_ms(current_oi["timestamp"])
            )

            # OI changes
            state.oi_change_1m_pct = self._calc_oi_change(
                oi_records, ts_ms, self.MS_1M
            )
            state.oi_change_5m_pct = self._calc_oi_change(
                oi_records, ts_ms, self.MS_5M
            )
            state.oi_change_1h_pct = self._calc_oi_change(
                oi_records, ts_ms, self.MS_1H
            )

            # OI History (last 12 records for trend analysis)
            state.oi_history = self._get_oi_history(oi_records, ts_ms, count=12)

        # Funding data
        funding_records = self._funding_index.get(symbol, [])
        current_funding = self._find_record_at(
            funding_records, ts_ms, "fundingTime"
        )

        if current_funding:
            rate = Decimal(str(current_funding.get("fundingRate", 0)))
            state.current_funding = FundingRecord(
                funding_rate=rate,
                funding_rate_percent=rate * 100,
                mark_price=Decimal(str(current_funding.get("markPrice", 0))),
                funding_time=self._from_ms(
                    current_funding["fundingTime"]
                ).isoformat()
            )

            # Last 8 funding records
            state.funding_history = self._get_funding_history(
                funding_records, ts_ms, count=8
            )

        # L/S Ratio data
        ls_records = self._ls_ratio_index.get(symbol, [])
        current_ls = self._find_record_at(ls_records, ts_ms, "timestamp")

        if current_ls:
            state.current_ls_ratio = LSRatioRecord(
                long_account_pct=Decimal(str(current_ls.get("longAccount", 0.5))) * 100,
                short_account_pct=Decimal(str(current_ls.get("shortAccount", 0.5))) * 100,
                long_short_ratio=Decimal(str(current_ls.get("longShortRatio", 1))),
                timestamp=self._from_ms(current_ls["timestamp"])
            )

        return state

    def build_spot_state(
        self,
        symbol: str,
        timestamp: datetime
    ) -> Optional[SymbolState]:
        """
        Build SymbolState for a symbol at given timestamp.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            timestamp: Point in time (UTC)

        Returns:
            SymbolState or None if no data available
        """
        if symbol not in self.history:
            return None

        ts_ms = self._to_ms(timestamp)

        # Get klines up to timestamp
        klines = self._get_klines_up_to(symbol, ts_ms)
        if not klines:
            return None

        state = SymbolState(symbol=symbol, timestamp=timestamp)

        # Current price from last kline
        last_kline = klines[-1]
        close = Decimal(str(last_kline["close"]))

        state.last_price = close
        state.best_bid = close * Decimal("0.9999")
        state.best_ask = close * Decimal("1.0001")
        state.mid_price = close
        state.spread_pct = Decimal("0.02")

        # Price changes
        state.price_change_1m_pct = self._calc_price_change(klines, self.MS_1M)
        state.price_change_5m_pct = self._calc_price_change(klines, self.MS_5M)
        state.price_change_1h_pct = self._calc_price_change(klines, self.MS_1H)

        # 7-day price change for trend detection (7 * 24 * 60 * 60 * 1000 = 604800000 ms)
        MS_7D = 7 * 24 * 60 * 60 * 1000
        state.price_change_7d_pct = self._calc_price_change(klines, MS_7D)

        # Volume calculations
        state.volume_1m = self._sum_volume(klines, self.MS_1M)
        state.volume_5m = self._sum_volume(klines, self.MS_5M)
        state.volume_1h = self._sum_volume(klines, self.MS_1H)

        # Volume spike ratio
        # FIX-S-4: Use minimum 6h window for avg_volume_1h to avoid false
        # spikes at the start of the data period when only a few candles exist.
        # If fewer than 360 candles available (< 6h), use the actual available
        # window but require at least 60 candles (1h) for a meaningful average.
        if len(klines) >= 60:
            avg_window = self.MS_24H if len(klines) >= 1440 else max(self.MS_1H * 6, len(klines) * self.MS_1M)
            state.avg_volume_1h = self._calc_avg_volume(klines, avg_window)
        else:
            state.avg_volume_1h = Decimal("0")

        if state.avg_volume_1h > 0:
            state.volume_spike_ratio = state.volume_1h / state.avg_volume_1h
        else:
            state.volume_spike_ratio = Decimal("1")

        # Trade counts
        state.trade_count_1m = self._sum_trades(klines, self.MS_1M)
        state.trade_count_5m = self._sum_trades(klines, self.MS_5M)

        # Buy ratio (taker buy volume / total volume)
        state.buy_ratio_5m = self._calc_buy_ratio(klines, self.MS_5M)

        # ATR calculation
        state.atr_1h_pct = self._calculate_atr(klines, periods=14)
        state.atr_1h_pct_raw = state.atr_1h_pct  # Raw value for risk calculator
        state.atr_is_real = len(klines) >= 14

        # Daily ATR (use longer period if available)
        state.atr_daily_pct = self._calculate_atr(klines, periods=60)
        state.atr_daily_pct_depth = state.atr_daily_pct
        state.atr_daily_is_real = len(klines) >= 60

        # Price history (last 60 close values)
        state.price_history = [
            Decimal(str(k["close"])) for k in klines[-60:]
        ]

        # Klines for ATR (as tuples: high, low, close)
        state.klines_1h = [
            (
                Decimal(str(k["high"])),
                Decimal(str(k["low"])),
                Decimal(str(k["close"]))
            )
            for k in klines[-60:]
        ]

        return state

    def get_available_timestamps(
        self,
        symbol: str,
        interval_minutes: int = 1
    ) -> List[datetime]:
        """
        Get all available timestamps for a symbol.

        Useful for iterating through historical data.

        Args:
            symbol: Trading pair
            interval_minutes: Step interval (default 1 minute)

        Returns:
            List of timestamps where data is available
        """
        klines = self._kline_index.get(symbol, [])
        if not klines:
            return []

        timestamps = []
        interval_ms = interval_minutes * 60 * 1000

        for kline in klines[::interval_minutes]:
            ts = self._from_ms(kline["timestamp"])
            timestamps.append(ts)

        return timestamps

    # =========================================================================
    # CALCULATION HELPERS
    # =========================================================================

    def _calculate_atr(self, klines: List[dict], periods: int = 14) -> Decimal:
        """Calculate Average True Range as percentage of price (optimized with float)."""
        if len(klines) < 2:
            return Decimal("0")
        recent = klines[-periods-1:] if len(klines) > periods else klines
        true_ranges = []
        for i in range(1, len(recent)):
            high = float(recent[i]["high"])
            low = float(recent[i]["low"])
            prev_close = float(recent[i-1]["close"])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)
        if not true_ranges:
            return Decimal("0")
        atr = sum(true_ranges) / len(true_ranges)
        last_close = float(klines[-1]["close"])
        if last_close == 0:
            return Decimal("0")
        return Decimal(str(round(atr / last_close * 100, 4)))

    def _calc_price_change(
        self,
        klines: List[dict],
        window_ms: int
    ) -> Decimal:
        """Calculate price change % over a time window."""
        if not klines:
            return Decimal("0")

        last_kline = klines[-1]
        current_ts = last_kline["timestamp"]
        current_price = Decimal(str(last_kline["close"]))

        target_ts = current_ts - window_ms

        # Find kline closest to target timestamp
        past_kline = self._find_kline_at(klines, target_ts)

        if not past_kline:
            return Decimal("0")

        past_price = Decimal(str(past_kline["close"]))

        if past_price == 0:
            return Decimal("0")

        change = ((current_price - past_price) / past_price) * 100

        return Decimal(str(round(float(change), 4)))

    def _calc_oi_change(
        self,
        oi_records: List[dict],
        current_ts_ms: int,
        window_ms: int
    ) -> Decimal:
        """Calculate OI change % over a time window."""
        current = self._find_record_at(oi_records, current_ts_ms, "timestamp")
        past = self._find_record_at(
            oi_records, current_ts_ms - window_ms, "timestamp"
        )

        if not current or not past:
            return Decimal("0")

        current_oi = Decimal(str(current.get("sumOpenInterestValue", 0)))
        past_oi = Decimal(str(past.get("sumOpenInterestValue", 0)))

        if past_oi == 0:
            return Decimal("0")

        change = ((current_oi - past_oi) / past_oi) * 100

        return Decimal(str(round(float(change), 4)))

    def _sum_volume(
        self,
        klines: List[dict],
        window_ms: int
    ) -> Decimal:
        """Sum quote volume over a time window."""
        if not klines:
            return Decimal("0")

        current_ts = klines[-1]["timestamp"]
        start_ts = current_ts - window_ms

        total = Decimal("0")
        for k in reversed(klines):
            if k["timestamp"] < start_ts:
                break
            total += Decimal(str(k["quote_volume"]))

        return Decimal(str(round(float(total), 2)))

    def _calc_avg_volume(
        self,
        klines: List[dict],
        window_ms: int
    ) -> Decimal:
        """Calculate average hourly volume over a longer window."""
        if not klines:
            return Decimal("0")

        current_ts = klines[-1]["timestamp"]
        start_ts = current_ts - window_ms

        total = Decimal("0")
        count = 0

        for k in reversed(klines):
            if k["timestamp"] < start_ts:
                break
            total += Decimal(str(k["quote_volume"]))
            count += 1

        if count == 0:
            return Decimal("0")

        # Convert to hourly average
        hours = count / 60  # minutes to hours
        if hours == 0:
            return total

        return Decimal(str(round(float(total / Decimal(str(hours))), 2)))

    def _sum_trades(
        self,
        klines: List[dict],
        window_ms: int
    ) -> int:
        """Sum trade count over a time window."""
        if not klines:
            return 0

        current_ts = klines[-1]["timestamp"]
        start_ts = current_ts - window_ms

        total = 0
        for k in reversed(klines):
            if k["timestamp"] < start_ts:
                break
            total += int(k.get("trades_count", 0))

        return total

    def _calc_buy_ratio(
        self,
        klines: List[dict],
        window_ms: int
    ) -> Optional[Decimal]:
        """Calculate buy ratio (taker buy volume / total volume)."""
        if not klines:
            return None

        current_ts = klines[-1]["timestamp"]
        start_ts = current_ts - window_ms

        total_volume = Decimal("0")
        taker_buy_volume = Decimal("0")

        for k in reversed(klines):
            if k["timestamp"] < start_ts:
                break
            total_volume += Decimal(str(k["quote_volume"]))
            taker_buy_volume += Decimal(str(k["taker_buy_quote_volume"]))

        if total_volume == 0:
            return None

        ratio = taker_buy_volume / total_volume

        return Decimal(str(round(float(ratio), 4)))

    def _get_funding_history(self, funding_records: List[dict], ts_ms: int, count: int = 8) -> List[FundingRecord]:
        if not funding_records:
            return []
        ts_cache = [r["fundingTime"] for r in funding_records]
        idx = bisect.bisect_right(ts_cache, ts_ms)
        recent = funding_records[max(0, idx - count):idx]
        result = []
        for record in reversed(recent):
            rate = Decimal(str(record.get("fundingRate", 0)))
            result.append(FundingRecord(
                funding_rate=rate,
                funding_rate_percent=rate * 100,
                mark_price=Decimal(str(record.get("markPrice", 0))),
                funding_time=self._from_ms(record["fundingTime"]).isoformat()
            ))
        return result

    def _get_oi_history(self, oi_records: List[dict], ts_ms: int, count: int = 12) -> List[OIRecord]:
        """Get last N OI records up to timestamp for trend analysis."""
        if not oi_records:
            return []
        ts_cache = [r["timestamp"] for r in oi_records]
        idx = bisect.bisect_right(ts_cache, ts_ms)
        recent = oi_records[max(0, idx - count):idx]
        result = []
        for record in recent:
            result.append(OIRecord(
                open_interest=Decimal(str(record.get("sumOpenInterest", 0))),
                open_interest_usd=Decimal(str(record.get("sumOpenInterestValue", 0))),
                timestamp=self._from_ms(record["timestamp"])
            ))
        return result

    # =========================================================================
    # LOOKUP HELPERS
    # =========================================================================

    def _get_klines_up_to(self, symbol: str, ts_ms: int) -> List[dict]:
        """Get all klines up to (and including) timestamp."""
        klines = self._kline_index.get(symbol, [])
        ts_cache = self._kline_ts_cache.get(symbol, [])
        if not klines:
            return []
        idx = bisect.bisect_right(ts_cache, ts_ms)
        return klines[:idx]

    def _find_kline_at(self, klines: List[dict], ts_ms: int) -> Optional[dict]:
        """Find kline at or before the target timestamp using binary search."""
        if not klines:
            return None

        # Binary search for kline at target timestamp
        timestamps = [k["timestamp"] for k in klines]
        idx = bisect.bisect_right(timestamps, ts_ms)

        if idx == 0:
            return None  # Target is before all klines

        return klines[idx - 1]

    def _find_record_at(self, records: List[dict], ts_ms: int, ts_field: str) -> Optional[dict]:
        if not records:
            return None
        # Выбираем нужный кэш по полю
        # Ищем символ через records identity
        # Используем bisect напрямую по полю
        lo, hi = 0, len(records) - 1
        result = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if records[mid][ts_field] <= ts_ms:
                result = records[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        return result

    # =========================================================================
    # TIMESTAMP UTILITIES
    # =========================================================================

    def _to_ms(self, dt: datetime) -> int:
        """Convert datetime to milliseconds timestamp."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def _from_ms(self, ts_ms: int) -> datetime:
        """Convert milliseconds timestamp to datetime."""
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    from datetime import timedelta
    from data_downloader import BinanceHistoryDownloader

    print("StateBuilder - Test Run")
    print("=" * 60)

    # Download some test data
    downloader = BinanceHistoryDownloader(cache_dir="cache")

    symbols = ["BTCUSDT"]
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=2)

    print(f"Downloading data for {symbols}...")
    history = downloader.download_all(symbols, start_time, end_time)

    # Build states
    builder = StateBuilder(history)

    # Test at middle of period
    test_time = start_time + timedelta(hours=1)

    print(f"\nBuilding states at {test_time}")
    print("-" * 60)

    for symbol in symbols:
        print(f"\n{symbol}:")

        futures_state = builder.build_futures_state(symbol, test_time)
        if futures_state:
            print(f"  FuturesState:")
            print(f"    OI change 5m: {futures_state.oi_change_5m_pct}%")
            print(f"    OI change 1h: {futures_state.oi_change_1h_pct}%")
            print(f"    ATR: {futures_state.futures_atr_1h_pct}%")
            if futures_state.current_funding:
                print(f"    Funding: {futures_state.current_funding.funding_rate_percent}%")
            if futures_state.current_ls_ratio:
                print(f"    L/S Ratio: {futures_state.current_ls_ratio.long_short_ratio}")

        spot_state = builder.build_spot_state(symbol, test_time)
        if spot_state:
            print(f"  SymbolState:")
            print(f"    Price: {spot_state.last_price}")
            print(f"    Price change 5m: {spot_state.price_change_5m_pct}%")
            print(f"    Volume 1h: ${spot_state.volume_1h:,.0f}")
            print(f"    Volume spike: {spot_state.volume_spike_ratio}x")
            print(f"    Buy ratio 5m: {spot_state.buy_ratio_5m}")
            print(f"    ATR: {spot_state.atr_1h_pct}%")
            print(f"    Trade count 5m: {spot_state.trade_count_5m}")

    # Test available timestamps
    print(f"\nAvailable timestamps for {symbols[0]}:")
    timestamps = builder.get_available_timestamps(symbols[0], interval_minutes=5)
    print(f"  Total: {len(timestamps)} (every 5 min)")
    if timestamps:
        print(f"  First: {timestamps[0]}")
        print(f"  Last: {timestamps[-1]}")
