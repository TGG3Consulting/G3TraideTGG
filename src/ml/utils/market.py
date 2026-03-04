# -*- coding: utf-8 -*-
"""
Market Utilities for ML System.

Market-related calculations and models:
- SlippageModel: Realistic slippage estimation
- MarketRegimeDetector: Bull/bear/sideways detection
- MarketImpactModel: Position impact on price
- FullTransactionCosts: Complete cost modeling

Usage:
    slippage = SlippageModel()
    cost = slippage.estimate("BTCUSDT", position_size)

    regime = MarketRegimeDetector()
    current_regime = regime.detect(btc_data)
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, Optional

import numpy as np
import pandas as pd
import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


class MarketRegime(Enum):
    """Market regime classification."""

    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    CAPITULATION = "CAPITULATION"


class Liquidity(Enum):
    """Liquidity level classification."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class SlippageEstimate:
    """Estimated slippage for a trade."""

    entry_slippage_pct: float
    exit_slippage_pct: float
    sl_slippage_pct: float
    total_slippage_pct: float
    limit_fill_probability: float


class SlippageModel:
    """
    Models realistic slippage for trades.

    Considers:
    - Liquidity level
    - Position size relative to volume
    - Market conditions
    """

    def __init__(self):
        """Initialize slippage model."""
        # Slippage by liquidity level
        self._slippage_by_liquidity = {
            Liquidity.HIGH: 0.0005,    # 0.05%
            Liquidity.MEDIUM: 0.002,   # 0.2%
            Liquidity.LOW: 0.01,       # 1%
        }

        # SL slippage is worse (market order in bad conditions)
        self._sl_slippage_multiplier = 2.0

        # Limit order fill rates
        self._limit_fill_rates = {
            Liquidity.HIGH: 0.8,
            Liquidity.MEDIUM: 0.6,
            Liquidity.LOW: 0.4,
        }

        logger.info("slippage_model_init")

    def estimate(
        self,
        symbol: str,
        position_size_usd: float,
        adv_usd: Optional[float] = None,
        liquidity: Optional[Liquidity] = None,
    ) -> SlippageEstimate:
        """
        Estimate slippage for a trade.

        Args:
            symbol: Trading pair
            position_size_usd: Position size in USD
            adv_usd: Average daily volume in USD
            liquidity: Override liquidity level

        Returns:
            SlippageEstimate
        """
        # Determine liquidity
        if liquidity is None:
            liquidity = self._determine_liquidity(symbol, adv_usd)

        base_slippage = self._slippage_by_liquidity[liquidity]

        # Adjust for position size
        size_factor = 1.0
        if adv_usd and adv_usd > 0:
            pct_of_adv = position_size_usd / adv_usd
            # Quadratic impact for large positions
            size_factor = 1.0 + (pct_of_adv * 100) ** 2

        entry_slippage = base_slippage * size_factor
        exit_slippage = base_slippage * size_factor
        sl_slippage = base_slippage * size_factor * self._sl_slippage_multiplier

        total = entry_slippage + exit_slippage  # SL is alternative to exit

        return SlippageEstimate(
            entry_slippage_pct=entry_slippage * 100,
            exit_slippage_pct=exit_slippage * 100,
            sl_slippage_pct=sl_slippage * 100,
            total_slippage_pct=total * 100,
            limit_fill_probability=self._limit_fill_rates[liquidity],
        )

    def _determine_liquidity(
        self,
        symbol: str,
        adv_usd: Optional[float],
    ) -> Liquidity:
        """Determine liquidity level from symbol and volume."""
        # High liquidity pairs
        high_liq = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"}
        if symbol in high_liq:
            return Liquidity.HIGH

        # Volume-based
        if adv_usd:
            if adv_usd > 100_000_000:  # > $100M/day
                return Liquidity.HIGH
            elif adv_usd > 10_000_000:  # > $10M/day
                return Liquidity.MEDIUM

        return Liquidity.LOW

    def apply_to_backtest(
        self,
        entry_price: float,
        exit_price: float,
        sl_price: float,
        direction: int,
        estimate: SlippageEstimate,
    ) -> tuple[float, float, float]:
        """
        Apply slippage to backtest prices.

        Args:
            entry_price: Original entry price
            exit_price: Original exit price
            sl_price: Original SL price
            direction: 1 for long, -1 for short
            estimate: SlippageEstimate

        Returns:
            Tuple of (adjusted_entry, adjusted_exit, adjusted_sl)
        """
        if direction == 1:  # Long
            # Entry worse (higher), exit worse (lower), SL worse (lower)
            adj_entry = entry_price * (1 + estimate.entry_slippage_pct / 100)
            adj_exit = exit_price * (1 - estimate.exit_slippage_pct / 100)
            adj_sl = sl_price * (1 - estimate.sl_slippage_pct / 100)
        else:  # Short
            # Entry worse (lower), exit worse (higher), SL worse (higher)
            adj_entry = entry_price * (1 - estimate.entry_slippage_pct / 100)
            adj_exit = exit_price * (1 + estimate.exit_slippage_pct / 100)
            adj_sl = sl_price * (1 + estimate.sl_slippage_pct / 100)

        return adj_entry, adj_exit, adj_sl


class MarketRegimeDetector:
    """
    Detects current market regime.

    Regimes affect model behavior and parameters.
    """

    def __init__(
        self,
        ma_short: int = 50,
        ma_long: int = 200,
        vol_window: int = 20,
    ):
        """
        Initialize regime detector.

        Args:
            ma_short: Short MA period
            ma_long: Long MA period
            vol_window: Volatility window
        """
        self._ma_short = ma_short
        self._ma_long = ma_long
        self._vol_window = vol_window

        logger.info("market_regime_detector_init")

    def detect(self, btc_data: pd.DataFrame) -> MarketRegime:
        """
        Detect current market regime from BTC data.

        Args:
            btc_data: DataFrame with 'close' column

        Returns:
            MarketRegime
        """
        if len(btc_data) < self._ma_long:
            logger.warning("insufficient_data_for_regime_detection")
            return MarketRegime.SIDEWAYS

        close = btc_data["close"]

        # Calculate indicators
        ma_short = close.rolling(self._ma_short).mean()
        ma_long = close.rolling(self._ma_long).mean()
        returns = close.pct_change()
        volatility = returns.rolling(self._vol_window).std() * np.sqrt(365 * 24)

        current_price = close.iloc[-1]
        current_ma_short = ma_short.iloc[-1]
        current_ma_long = ma_long.iloc[-1]
        current_vol = volatility.iloc[-1]
        avg_vol = volatility.mean()

        # Weekly return for capitulation check
        weekly_return = (close.iloc[-1] / close.iloc[-168] - 1) if len(close) > 168 else 0

        # Regime detection
        if weekly_return < -0.20:  # -20% in a week
            return MarketRegime.CAPITULATION

        if current_vol > avg_vol * 2:  # 2x average volatility
            return MarketRegime.HIGH_VOLATILITY

        if current_price > current_ma_long and current_ma_short > current_ma_long:
            return MarketRegime.BULL_TREND

        if current_price < current_ma_long and current_ma_short < current_ma_long:
            return MarketRegime.BEAR_TREND

        return MarketRegime.SIDEWAYS

    def get_regime_multipliers(self, regime: MarketRegime) -> Dict[str, float]:
        """
        Get parameter multipliers for regime.

        Args:
            regime: Current market regime

        Returns:
            Dict of parameter multipliers
        """
        multipliers = {
            MarketRegime.BULL_TREND: {
                "position_size": 1.2,
                "sl_width": 0.8,
                "tp_width": 1.2,
                "confidence_threshold": 0.55,
            },
            MarketRegime.BEAR_TREND: {
                "position_size": 0.7,
                "sl_width": 1.2,
                "tp_width": 0.8,
                "confidence_threshold": 0.65,
            },
            MarketRegime.SIDEWAYS: {
                "position_size": 0.8,
                "sl_width": 1.0,
                "tp_width": 1.0,
                "confidence_threshold": 0.60,
            },
            MarketRegime.HIGH_VOLATILITY: {
                "position_size": 0.5,
                "sl_width": 1.5,
                "tp_width": 1.5,
                "confidence_threshold": 0.70,
            },
            MarketRegime.CAPITULATION: {
                "position_size": 0.3,
                "sl_width": 2.0,
                "tp_width": 0.5,
                "confidence_threshold": 0.80,
            },
        }

        return multipliers.get(regime, multipliers[MarketRegime.SIDEWAYS])


@dataclass
class FullTransactionCosts:
    """Complete transaction cost model."""

    # Commission
    maker_fee_pct: float = 0.02  # 0.02%
    taker_fee_pct: float = 0.05  # 0.05%

    # Funding (average per 8h)
    avg_funding_rate_pct: float = 0.01  # 0.01%

    # Spread
    avg_spread_pct: float = 0.05  # 0.05%

    def calculate_total(
        self,
        hold_time_hours: float,
        is_maker_entry: bool = False,
        is_maker_exit: bool = False,
    ) -> float:
        """
        Calculate total transaction costs.

        Args:
            hold_time_hours: Position hold time in hours
            is_maker_entry: Whether entry is maker order
            is_maker_exit: Whether exit is maker order

        Returns:
            Total cost as percentage
        """
        # Entry cost
        entry_fee = self.maker_fee_pct if is_maker_entry else self.taker_fee_pct
        entry_spread = self.avg_spread_pct / 2

        # Exit cost
        exit_fee = self.maker_fee_pct if is_maker_exit else self.taker_fee_pct
        exit_spread = self.avg_spread_pct / 2

        # Funding (every 8 hours)
        funding_periods = int(hold_time_hours / 8)
        funding_cost = self.avg_funding_rate_pct * funding_periods

        total = entry_fee + exit_fee + entry_spread + exit_spread + funding_cost

        return total


class MarketImpactModel:
    """
    Models market impact of position size.

    Large positions move the market against us.
    """

    def __init__(self, max_position_pct_of_adv: float = 0.01):
        """
        Initialize impact model.

        Args:
            max_position_pct_of_adv: Max position as % of daily volume
        """
        self._max_pct = max_position_pct_of_adv

        logger.info("market_impact_model_init")

    def calculate_impact(
        self,
        position_size_usd: float,
        adv_usd: float,
    ) -> float:
        """
        Calculate market impact as percentage.

        Args:
            position_size_usd: Position size in USD
            adv_usd: Average daily volume in USD

        Returns:
            Estimated price impact in percentage
        """
        if adv_usd <= 0:
            return 1.0  # High impact for unknown liquidity

        pct_of_adv = position_size_usd / adv_usd

        # Square-root market impact model (empirically observed)
        impact = np.sqrt(pct_of_adv) * 100

        return float(impact)

    def get_max_position(self, adv_usd: float) -> float:
        """
        Get maximum recommended position size.

        Args:
            adv_usd: Average daily volume in USD

        Returns:
            Maximum position size in USD
        """
        return adv_usd * self._max_pct

    def should_reduce_size(
        self,
        position_size_usd: float,
        adv_usd: float,
    ) -> tuple[bool, float]:
        """
        Check if position should be reduced.

        Args:
            position_size_usd: Proposed position size
            adv_usd: Average daily volume

        Returns:
            Tuple of (should_reduce, recommended_size)
        """
        max_size = self.get_max_position(adv_usd)

        if position_size_usd > max_size:
            return True, max_size

        return False, position_size_usd
