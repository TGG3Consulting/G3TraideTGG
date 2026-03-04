# -*- coding: utf-8 -*-
"""
Technical Indicators for ML Features.

Implements common technical analysis indicators:
- RSI (Relative Strength Index)
- MACD (Moving Average Convergence Divergence)
- Bollinger Bands
- ATR (Average True Range)
- EMA (Exponential Moving Average)
- Volume indicators

All indicators are vectorized for performance using numpy/pandas.
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple
import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


class TechnicalIndicators:
    """
    Calculate technical indicators from OHLCV data.

    All methods are static and work with pandas Series/DataFrame.
    """

    def __init__(self):
        """Initialize with config."""
        self._config = settings.ml.features

    @staticmethod
    def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate Relative Strength Index.

        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss

        Args:
            prices: Close prices
            period: RSI period (default 14)

        Returns:
            RSI values (0-100)
        """
        delta = prices.diff()

        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        return rsi.fillna(50)  # Neutral when undefined

    @staticmethod
    def macd(
        prices: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calculate MACD (Moving Average Convergence Divergence).

        MACD Line = EMA(fast) - EMA(slow)
        Signal Line = EMA(MACD Line)
        Histogram = MACD Line - Signal Line

        Args:
            prices: Close prices
            fast: Fast EMA period (default 12)
            slow: Slow EMA period (default 26)
            signal: Signal line period (default 9)

        Returns:
            Tuple of (macd_line, signal_line, histogram)
        """
        ema_fast = prices.ewm(span=fast, adjust=False).mean()
        ema_slow = prices.ewm(span=slow, adjust=False).mean()

        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(
        prices: pd.Series,
        period: int = 20,
        std_dev: float = 2.0,
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calculate Bollinger Bands.

        Middle Band = SMA(period)
        Upper Band = Middle + (std_dev * STD)
        Lower Band = Middle - (std_dev * STD)

        Args:
            prices: Close prices
            period: SMA period (default 20)
            std_dev: Standard deviation multiplier (default 2.0)

        Returns:
            Tuple of (upper_band, middle_band, lower_band)
        """
        middle = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()

        upper = middle + (std_dev * std)
        lower = middle - (std_dev * std)

        return upper, middle, lower

    @staticmethod
    def bb_position(prices: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.Series:
        """
        Calculate price position within Bollinger Bands.

        Returns value between 0 and 1:
        - 0 = at lower band
        - 0.5 = at middle band
        - 1 = at upper band

        Args:
            prices: Close prices
            period: BB period
            std_dev: BB standard deviation

        Returns:
            Position within bands (0-1)
        """
        upper, middle, lower = TechnicalIndicators.bollinger_bands(prices, period, std_dev)
        band_width = upper - lower

        position = (prices - lower) / band_width.replace(0, np.nan)
        return position.clip(0, 1).fillna(0.5)

    @staticmethod
    def atr(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """
        Calculate Average True Range.

        True Range = max(high - low, |high - prev_close|, |low - prev_close|)
        ATR = EMA(True Range)

        Args:
            high: High prices
            low: Low prices
            close: Close prices
            period: ATR period (default 14)

        Returns:
            ATR values
        """
        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.ewm(span=period, adjust=False).mean()

        return atr

    @staticmethod
    def atr_percent(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """
        Calculate ATR as percentage of price.

        Args:
            high: High prices
            low: Low prices
            close: Close prices
            period: ATR period

        Returns:
            ATR as percentage
        """
        atr = TechnicalIndicators.atr(high, low, close, period)
        return (atr / close * 100).fillna(0)

    @staticmethod
    def ema(prices: pd.Series, period: int) -> pd.Series:
        """
        Calculate Exponential Moving Average.

        Args:
            prices: Price series
            period: EMA period

        Returns:
            EMA values
        """
        return prices.ewm(span=period, adjust=False).mean()

    @staticmethod
    def sma(prices: pd.Series, period: int) -> pd.Series:
        """
        Calculate Simple Moving Average.

        Args:
            prices: Price series
            period: SMA period

        Returns:
            SMA values
        """
        return prices.rolling(window=period).mean()

    @staticmethod
    def price_vs_ema(prices: pd.Series, period: int) -> pd.Series:
        """
        Calculate price position relative to EMA.

        Returns percentage above/below EMA.

        Args:
            prices: Close prices
            period: EMA period

        Returns:
            Percentage above/below EMA
        """
        ema = TechnicalIndicators.ema(prices, period)
        return ((prices - ema) / ema * 100).fillna(0)

    @staticmethod
    def momentum(prices: pd.Series, period: int = 10) -> pd.Series:
        """
        Calculate price momentum.

        Momentum = (Price / Price_n_periods_ago - 1) * 100

        Args:
            prices: Close prices
            period: Lookback period

        Returns:
            Momentum percentage
        """
        return ((prices / prices.shift(period) - 1) * 100).fillna(0)

    @staticmethod
    def roc(prices: pd.Series, period: int = 10) -> pd.Series:
        """
        Calculate Rate of Change.

        Same as momentum but different naming convention.

        Args:
            prices: Close prices
            period: Lookback period

        Returns:
            ROC percentage
        """
        return TechnicalIndicators.momentum(prices, period)

    @staticmethod
    def volatility(prices: pd.Series, period: int = 20) -> pd.Series:
        """
        Calculate rolling volatility (standard deviation of returns).

        Args:
            prices: Close prices
            period: Rolling window

        Returns:
            Volatility (annualized)
        """
        returns = prices.pct_change()
        vol = returns.rolling(window=period).std()
        return (vol * np.sqrt(365 * 24) * 100).fillna(0)  # Annualized, hourly data

    @staticmethod
    def volume_sma_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
        """
        Calculate volume relative to its SMA.

        Args:
            volume: Volume series
            period: SMA period

        Returns:
            Volume / SMA(Volume)
        """
        sma = volume.rolling(window=period).mean()
        return (volume / sma.replace(0, np.nan)).fillna(1)

    @staticmethod
    def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """
        Calculate On-Balance Volume.

        OBV adds volume on up days, subtracts on down days.

        Args:
            close: Close prices
            volume: Volume series

        Returns:
            OBV values
        """
        direction = np.sign(close.diff())
        direction.iloc[0] = 0
        return (direction * volume).cumsum()

    @staticmethod
    def obv_normalized(close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
        """
        Calculate normalized OBV (z-score of OBV).

        Args:
            close: Close prices
            volume: Volume series
            period: Normalization window

        Returns:
            Normalized OBV
        """
        obv = TechnicalIndicators.obv(close, volume)
        mean = obv.rolling(window=period).mean()
        std = obv.rolling(window=period).std()
        return ((obv - mean) / std.replace(0, np.nan)).fillna(0)

    def add_all_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Add all technical indicators to DataFrame.

        Expects DataFrame with columns: close, high, low, volume
        (or price as close proxy)

        Args:
            df: DataFrame with OHLCV data

        Returns:
            DataFrame with indicator columns added
        """
        df = df.copy()

        # Get price column (close or price)
        price_col = "close" if "close" in df.columns else "price"
        prices = df[price_col]

        # High/Low (use price if not available)
        high = df["high"] if "high" in df.columns else prices
        low = df["low"] if "low" in df.columns else prices

        # Volume
        volume = df["volume"] if "volume" in df.columns else df.get("volume_1h", pd.Series([0] * len(df)))

        # RSI
        df["rsi"] = self.rsi(prices, self._config.rsi_period)

        # MACD
        macd_line, signal_line, histogram = self.macd(
            prices,
            self._config.macd_fast,
            self._config.macd_slow,
            self._config.macd_signal,
        )
        df["macd"] = macd_line
        df["macd_signal"] = signal_line
        df["macd_hist"] = histogram

        # Bollinger Bands
        df["bb_position"] = self.bb_position(prices, self._config.bb_period, self._config.bb_std)

        # ATR
        df["atr_pct"] = self.atr_percent(high, low, prices, self._config.atr_period)

        # EMAs
        for period in self._config.ema_periods:
            df[f"price_vs_ema{period}"] = self.price_vs_ema(prices, period)

        # Momentum
        df["momentum_10"] = self.momentum(prices, 10)
        df["momentum_20"] = self.momentum(prices, 20)

        # Volatility
        df["volatility_20"] = self.volatility(prices, 20)

        # Volume indicators
        if not volume.isna().all() and volume.sum() > 0:
            df["volume_sma_ratio"] = self.volume_sma_ratio(volume, 20)
            df["obv_norm"] = self.obv_normalized(prices, volume, 20)

        logger.debug("technical_indicators_added", new_columns=len(df.columns))
        return df
