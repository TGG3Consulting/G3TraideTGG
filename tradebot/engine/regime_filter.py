# -*- coding: utf-8 -*-
"""
RegimeFilter - Dynamic market regime detection for symbol filtering.

Based on:
1. Rolling correlation 30d (BTC vs alts average)
2. BTC Dominance change 7d (from BTCDOMUSDT)

Regimes:
- BTC_ONLY: corr > 0.8 OR dom_change > +2% -> trade only BTCUSDT
- ALT_ONLY: corr < 0.6 AND dom_change < -1% -> trade only alts (no BTC)
- MIXED: everything else -> trade all symbols

Usage:
    # Backtest
    regime_filter = RegimeFilter(enabled=True)
    filtered_symbols = regime_filter.filter_symbols(symbols, history, signal_date)

    # Live
    regime_filter = RegimeFilter(enabled=True)
    filtered_symbols = regime_filter.filter_symbols_live(symbols)
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

import numpy as np
import requests

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Regime thresholds (from colleague's idea)
CORR_HIGH_THRESHOLD = 0.8   # Above this -> BTC dominates
CORR_LOW_THRESHOLD = 0.6    # Below this -> alts decorrelated
DOM_UP_THRESHOLD = 2.0      # % increase in 7d -> BTC dominance rising
DOM_DOWN_THRESHOLD = -1.0   # % decrease in 7d -> BTC dominance falling

# Binance API
BINANCE_FAPI_BASE = "https://fapi.binance.com"
BTCDOM_SYMBOL = "BTCDOMUSDT"
BTC_SYMBOL = "BTCUSDT"

# Lookback periods
CORRELATION_LOOKBACK_DAYS = 30
DOMINANCE_LOOKBACK_DAYS = 7


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class RegimeResult:
    """Result of regime calculation."""
    regime: str  # "BTC_ONLY", "ALT_ONLY", "MIXED"
    avg_correlation: float
    dominance_change_pct: float
    calculated_at: datetime
    symbols_before: int
    symbols_after: int

    def __str__(self) -> str:
        return (
            f"Regime: {self.regime} | "
            f"Corr: {self.avg_correlation:.2f} | "
            f"DomChange: {self.dominance_change_pct:+.2f}%"
        )


# =============================================================================
# REGIME FILTER
# =============================================================================

class RegimeFilter:
    """
    Dynamic market regime filter.

    Determines whether to trade BTC only, alts only, or all symbols
    based on correlation and BTC dominance metrics.
    """

    # Rate limiting
    REQUEST_DELAY = 0.1  # seconds between requests
    MAX_RETRIES = 3
    RETRY_DELAY = 5

    def __init__(
        self,
        enabled: bool = False,
        corr_high: float = CORR_HIGH_THRESHOLD,
        corr_low: float = CORR_LOW_THRESHOLD,
        dom_up: float = DOM_UP_THRESHOLD,
        dom_down: float = DOM_DOWN_THRESHOLD,
    ):
        """
        Initialize RegimeFilter.

        Args:
            enabled: Enable/disable the filter
            corr_high: Correlation threshold for BTC_ONLY (default 0.8)
            corr_low: Correlation threshold for ALT_ONLY (default 0.6)
            dom_up: Dominance change % for BTC_ONLY (default +2.0)
            dom_down: Dominance change % for ALT_ONLY (default -1.0)
        """
        self.enabled = enabled
        self.corr_high = corr_high
        self.corr_low = corr_low
        self.dom_up = dom_up
        self.dom_down = dom_down

        # HTTP session
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "RegimeFilter/1.0"
        })

        # Cache (for live mode - recalculate once per day)
        self._cache: Optional[RegimeResult] = None
        self._cache_date: Optional[str] = None  # "YYYY-MM-DD"

    # =========================================================================
    # PUBLIC API - BACKTEST
    # =========================================================================

    def filter_symbols_backtest(
        self,
        symbols: List[str],
        history: Dict[str, Any],  # SymbolHistoryData dict
        signal_date: datetime,
    ) -> Tuple[List[str], Optional[RegimeResult]]:
        """
        Filter symbols for backtest mode.

        Uses historical data from `history` dict (already downloaded).
        Calculates regime based on data BEFORE signal_date (no look-ahead bias).

        Args:
            symbols: List of symbols to filter
            history: Dict of SymbolHistoryData from downloader
            signal_date: Date of the signal (use data before this)

        Returns:
            Tuple of (filtered_symbols, regime_result)
        """
        if not self.enabled:
            return symbols, None

        if len(symbols) < 2:
            return symbols, None

        try:
            # 1. Extract daily closes from history (before signal_date)
            btc_closes = self._extract_closes_before_date(
                history.get(BTC_SYMBOL), signal_date, CORRELATION_LOOKBACK_DAYS + 1
            )

            if btc_closes is None or len(btc_closes) < CORRELATION_LOOKBACK_DAYS + 1:
                logger.debug(f"Regime filter: insufficient BTC data for {signal_date}")
                return symbols, None

            # 2. Calculate correlation with each alt
            alt_symbols = [s for s in symbols if s != BTC_SYMBOL]
            correlations = []

            for alt in alt_symbols:
                alt_closes = self._extract_closes_before_date(
                    history.get(alt), signal_date, CORRELATION_LOOKBACK_DAYS + 1
                )
                if alt_closes is not None and len(alt_closes) >= CORRELATION_LOOKBACK_DAYS + 1:
                    corr = self._calculate_correlation(btc_closes, alt_closes)
                    if corr is not None:
                        correlations.append(corr)

            if not correlations:
                logger.debug(f"Regime filter: no valid alt correlations for {signal_date}")
                return symbols, None

            avg_corr = float(np.mean(correlations))

            # 3. Calculate dominance change (need BTCDOMUSDT in history)
            dom_change = self._calculate_dominance_change_backtest(history, signal_date)

            # 4. Determine regime
            regime = self._determine_regime(avg_corr, dom_change)

            # 5. Filter symbols
            filtered = self._apply_regime_filter(symbols, regime)

            result = RegimeResult(
                regime=regime,
                avg_correlation=avg_corr,
                dominance_change_pct=dom_change,
                calculated_at=signal_date,
                symbols_before=len(symbols),
                symbols_after=len(filtered),
            )

            return filtered, result

        except Exception as e:
            logger.error(f"Regime filter backtest error: {e}")
            return symbols, None

    # =========================================================================
    # PUBLIC API - LIVE
    # =========================================================================

    def filter_symbols_live(
        self,
        symbols: List[str],
        force_recalculate: bool = False,
    ) -> Tuple[List[str], Optional[RegimeResult]]:
        """
        Filter symbols for live mode.

        Fetches current data from Binance API.
        Caches result for the day (recalculates once per day after 00:00 UTC).

        Args:
            symbols: List of symbols to filter
            force_recalculate: Force recalculation ignoring cache

        Returns:
            Tuple of (filtered_symbols, regime_result)
        """
        if not self.enabled:
            return symbols, None

        if len(symbols) < 2:
            return symbols, None

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Check cache
        if not force_recalculate and self._cache_date == today_str and self._cache is not None:
            # Use cached regime, just re-filter symbols
            filtered = self._apply_regime_filter(symbols, self._cache.regime)
            return filtered, self._cache

        try:
            # 1. Fetch BTCUSDT klines
            btc_closes = self._fetch_daily_closes(BTC_SYMBOL, CORRELATION_LOOKBACK_DAYS + 1)
            if btc_closes is None or len(btc_closes) < CORRELATION_LOOKBACK_DAYS + 1:
                logger.warning("Regime filter: failed to fetch BTC klines")
                return symbols, None

            # 2. Fetch alt klines and calculate correlations
            alt_symbols = [s for s in symbols if s != BTC_SYMBOL]
            correlations = []

            for alt in alt_symbols:
                alt_closes = self._fetch_daily_closes(alt, CORRELATION_LOOKBACK_DAYS + 1)
                if alt_closes is not None and len(alt_closes) >= CORRELATION_LOOKBACK_DAYS + 1:
                    corr = self._calculate_correlation(btc_closes, alt_closes)
                    if corr is not None:
                        correlations.append(corr)

            if not correlations:
                logger.warning("Regime filter: no valid alt correlations")
                return symbols, None

            avg_corr = float(np.mean(correlations))

            # 3. Fetch BTCDOMUSDT and calculate dominance change
            dom_change = self._calculate_dominance_change_live()

            # 4. Determine regime
            regime = self._determine_regime(avg_corr, dom_change)

            # 5. Filter symbols
            filtered = self._apply_regime_filter(symbols, regime)

            result = RegimeResult(
                regime=regime,
                avg_correlation=avg_corr,
                dominance_change_pct=dom_change,
                calculated_at=datetime.now(timezone.utc),
                symbols_before=len(symbols),
                symbols_after=len(filtered),
            )

            # Cache result
            self._cache = result
            self._cache_date = today_str

            logger.info(f"Regime filter: {result}")

            return filtered, result

        except Exception as e:
            logger.error(f"Regime filter live error: {e}")
            return symbols, None

    # =========================================================================
    # CORE CALCULATIONS
    # =========================================================================

    def _calculate_correlation(
        self,
        btc_closes: List[float],
        alt_closes: List[float],
    ) -> Optional[float]:
        """
        Calculate Pearson correlation between BTC and alt returns.

        Args:
            btc_closes: List of BTC daily closes (oldest to newest)
            alt_closes: List of alt daily closes (oldest to newest)

        Returns:
            Correlation coefficient or None if insufficient data
        """
        if len(btc_closes) < 2 or len(alt_closes) < 2:
            return None

        # Align lengths
        min_len = min(len(btc_closes), len(alt_closes))
        btc_closes = btc_closes[-min_len:]
        alt_closes = alt_closes[-min_len:]

        # Calculate log returns
        btc_arr = np.array(btc_closes, dtype=float)
        alt_arr = np.array(alt_closes, dtype=float)

        # Avoid log(0)
        if np.any(btc_arr <= 0) or np.any(alt_arr <= 0):
            return None

        btc_returns = np.diff(np.log(btc_arr))
        alt_returns = np.diff(np.log(alt_arr))

        if len(btc_returns) < 10:  # Need at least 10 data points
            return None

        # Pearson correlation
        try:
            corr_matrix = np.corrcoef(btc_returns, alt_returns)
            corr = corr_matrix[0, 1]
            if np.isnan(corr):
                return None
            return float(corr)
        except Exception:
            return None

    def _determine_regime(
        self,
        avg_corr: float,
        dom_change: float,
    ) -> str:
        """
        Determine market regime based on correlation and dominance change.

        Logic (from colleague's idea):
        - BTC_ONLY: corr > 0.8 OR dom_change > +2%
        - ALT_ONLY: corr < 0.6 AND dom_change < -1%
        - MIXED: everything else

        Args:
            avg_corr: Average correlation (BTC vs alts)
            dom_change: Dominance change % over 7 days

        Returns:
            "BTC_ONLY", "ALT_ONLY", or "MIXED"
        """
        # BTC_ONLY: high correlation OR dominance rising
        if avg_corr > self.corr_high or dom_change > self.dom_up:
            return "BTC_ONLY"

        # ALT_ONLY: low correlation AND dominance falling
        if avg_corr < self.corr_low and dom_change < self.dom_down:
            return "ALT_ONLY"

        # MIXED: everything else
        return "MIXED"

    def _apply_regime_filter(
        self,
        symbols: List[str],
        regime: str,
    ) -> List[str]:
        """
        Apply regime filter to symbol list.

        Args:
            symbols: Original symbol list
            regime: "BTC_ONLY", "ALT_ONLY", or "MIXED"

        Returns:
            Filtered symbol list
        """
        if regime == "BTC_ONLY":
            # Only trade BTC
            if BTC_SYMBOL in symbols:
                return [BTC_SYMBOL]
            return []

        if regime == "ALT_ONLY":
            # Trade alts only (exclude BTC)
            return [s for s in symbols if s != BTC_SYMBOL]

        # MIXED: trade everything
        return symbols

    # =========================================================================
    # BACKTEST DATA EXTRACTION
    # =========================================================================

    def _extract_closes_before_date(
        self,
        symbol_data: Any,  # SymbolHistoryData
        before_date: datetime,
        num_days: int,
    ) -> Optional[List[float]]:
        """
        Extract daily closes from history, before specified date.

        Args:
            symbol_data: SymbolHistoryData object
            before_date: Extract data before this date (exclusive)
            num_days: Number of days to extract

        Returns:
            List of closes (oldest to newest) or None
        """
        if symbol_data is None or not hasattr(symbol_data, 'klines'):
            return None

        klines = symbol_data.klines
        if not klines:
            return None

        # Aggregate to daily and filter
        daily_closes = {}
        before_ts = int(before_date.timestamp() * 1000)

        for k in klines:
            ts = k.get("timestamp", 0)
            if ts >= before_ts:
                continue  # Skip future data

            # Get date string
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")

            # Keep last close of each day
            close = float(k.get("close", 0))
            if close > 0:
                daily_closes[date_str] = close

        if not daily_closes:
            return None

        # Sort by date and take last num_days
        sorted_dates = sorted(daily_closes.keys())
        if len(sorted_dates) < num_days:
            return None

        recent_dates = sorted_dates[-num_days:]
        closes = [daily_closes[d] for d in recent_dates]

        return closes

    def _calculate_dominance_change_backtest(
        self,
        history: Dict[str, Any],
        signal_date: datetime,
    ) -> float:
        """
        Calculate BTCDOM change for backtest mode.

        Uses BTCDOMUSDT from history if available.
        Falls back to 0.0 if not available.

        Args:
            history: Dict of SymbolHistoryData
            signal_date: Calculate change before this date

        Returns:
            Dominance change % over 7 days
        """
        btcdom_data = history.get(BTCDOM_SYMBOL)
        if btcdom_data is None:
            # BTCDOMUSDT not in history - return 0 (neutral)
            return 0.0

        closes = self._extract_closes_before_date(
            btcdom_data, signal_date, DOMINANCE_LOOKBACK_DAYS + 1
        )

        if closes is None or len(closes) < DOMINANCE_LOOKBACK_DAYS + 1:
            return 0.0

        # Change over 7 days
        dom_today = closes[-1]
        dom_7d_ago = closes[-(DOMINANCE_LOOKBACK_DAYS + 1)]

        if dom_7d_ago <= 0:
            return 0.0

        change_pct = (dom_today / dom_7d_ago - 1) * 100
        return float(change_pct)

    # =========================================================================
    # LIVE DATA FETCHING
    # =========================================================================

    def _fetch_daily_closes(
        self,
        symbol: str,
        num_days: int,
    ) -> Optional[List[float]]:
        """
        Fetch daily closes from Binance API.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            num_days: Number of days to fetch

        Returns:
            List of closes (oldest to newest) or None
        """
        try:
            params = {
                "symbol": symbol,
                "interval": "1d",
                "limit": num_days + 5,  # Extra buffer
            }

            data = self._request("/fapi/v1/klines", params)
            if not data:
                return None

            # Extract closes (index 4 is close price)
            closes = []
            for k in data:
                close = float(k[4])
                if close > 0:
                    closes.append(close)

            if len(closes) < num_days:
                return None

            return closes[-num_days:]

        except Exception as e:
            logger.error(f"Failed to fetch klines for {symbol}: {e}")
            return None

    def _calculate_dominance_change_live(self) -> float:
        """
        Calculate BTCDOM change from live API.

        Returns:
            Dominance change % over 7 days
        """
        closes = self._fetch_daily_closes(BTCDOM_SYMBOL, DOMINANCE_LOOKBACK_DAYS + 1)

        if closes is None or len(closes) < DOMINANCE_LOOKBACK_DAYS + 1:
            logger.warning("Failed to fetch BTCDOMUSDT - using neutral dominance")
            return 0.0

        dom_today = closes[-1]
        dom_7d_ago = closes[-(DOMINANCE_LOOKBACK_DAYS + 1)]

        if dom_7d_ago <= 0:
            return 0.0

        change_pct = (dom_today / dom_7d_ago - 1) * 100
        return float(change_pct)

    # =========================================================================
    # HTTP UTILITIES
    # =========================================================================

    def _request(
        self,
        endpoint: str,
        params: Optional[dict] = None,
    ) -> Optional[Any]:
        """Make HTTP request with retry logic."""
        url = f"{BINANCE_FAPI_BASE}{endpoint}"

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                time.sleep(self.REQUEST_DELAY)

                response = self._session.get(url, params=params, timeout=30)

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", self.RETRY_DELAY))
                    logger.warning(f"Rate limited, waiting {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    logger.warning(f"Server error {response.status_code}, retry {attempt}/{self.MAX_RETRIES}")
                    time.sleep(self.RETRY_DELAY)
                    continue

                logger.error(f"HTTP {response.status_code}: {response.text[:200]}")
                return None

            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e}")
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY)
                    continue
                return None

        return None

    # =========================================================================
    # UTILITIES
    # =========================================================================

    def clear_cache(self) -> None:
        """Clear cached regime result."""
        self._cache = None
        self._cache_date = None

    def get_cached_regime(self) -> Optional[RegimeResult]:
        """Get cached regime result if available."""
        return self._cache


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    print("RegimeFilter - Test Run")
    print("=" * 60)

    # Test live mode
    rf = RegimeFilter(enabled=True)

    test_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT"]

    print(f"\nTesting with symbols: {test_symbols}")

    filtered, result = rf.filter_symbols_live(test_symbols)

    if result:
        print(f"\nResult: {result}")
        print(f"Filtered symbols: {filtered}")
    else:
        print("\nFailed to calculate regime")

    print("\nTest complete.")
