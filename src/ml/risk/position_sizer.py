# -*- coding: utf-8 -*-
"""
Position Sizer for ML System.

Calculates optimal position sizes based on:
- Kelly criterion
- Volatility scaling
- Fixed fraction
- Risk limits

Usage:
    sizer = PositionSizer()
    size_pct = sizer.calculate_size(
        win_probability=0.6,
        risk_reward=2.0,
        sl_pct=1.5,
    )
"""

from datetime import datetime, timezone
from typing import Dict, Optional

import numpy as np
import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


class PositionSizer:
    """
    Calculates optimal position sizes for trading.

    Supports multiple sizing methods:
    - Kelly: Mathematical optimal based on edge and odds
    - Volatility: Size based on ATR/volatility
    - Fixed: Constant percentage of capital
    """

    def __init__(self):
        """Initialize position sizer with config."""
        self._config = settings.ml.risk

        logger.info(
            "position_sizer_init",
            method=self._config.position_sizing_method,
            kelly_fraction=self._config.kelly_fraction,
        )

    def calculate_size(
        self,
        win_probability: float,
        risk_reward: float,
        sl_pct: float,
        volatility: Optional[float] = None,
        current_drawdown: float = 0.0,
    ) -> float:
        """
        Calculate position size as percentage of capital.

        Args:
            win_probability: Probability of winning (0-1)
            risk_reward: Risk/reward ratio (TP/SL)
            sl_pct: Stop-loss percentage
            volatility: Optional ATR or volatility
            current_drawdown: Current drawdown percentage

        Returns:
            Position size as percentage of capital
        """
        method = self._config.position_sizing_method

        if method == "kelly":
            size_pct = self._kelly_size(win_probability, risk_reward)
        elif method == "volatility":
            size_pct = self._volatility_size(sl_pct, volatility)
        else:  # fixed
            size_pct = self._fixed_size()

        # Apply drawdown adjustment
        size_pct = self._apply_drawdown_adjustment(size_pct, current_drawdown)

        # Apply limits
        size_pct = self._apply_limits(size_pct)

        logger.debug(
            "position_size_calculated",
            method=method,
            win_prob=win_probability,
            rr=risk_reward,
            size_pct=size_pct,
        )

        return size_pct

    def _kelly_size(
        self,
        win_probability: float,
        risk_reward: float,
    ) -> float:
        """
        Calculate position size using Kelly criterion.

        Kelly formula: f* = (p * b - q) / b
        Where:
            p = win probability
            q = loss probability (1 - p)
            b = win/loss ratio (risk_reward)
            f* = fraction of capital to bet

        Args:
            win_probability: Probability of winning (0-1)
            risk_reward: R:R ratio

        Returns:
            Kelly-optimal position size
        """
        p = win_probability
        q = 1 - p
        b = risk_reward

        if b <= 0:
            return 0.0

        # Full Kelly
        full_kelly = (p * b - q) / b

        # Apply fraction (typically 0.25 = quarter Kelly)
        fractional_kelly = full_kelly * self._config.kelly_fraction

        # Don't bet on negative edge
        if fractional_kelly < 0:
            return 0.0

        return fractional_kelly * 100  # Convert to percentage

    def _volatility_size(
        self,
        sl_pct: float,
        volatility: Optional[float],
    ) -> float:
        """
        Calculate position size based on volatility.

        Targets a specific risk level regardless of volatility.

        Args:
            sl_pct: Stop-loss percentage
            volatility: ATR or similar volatility measure

        Returns:
            Position size as percentage
        """
        # Target 1% risk per trade
        target_risk_pct = 1.0

        if sl_pct <= 0:
            return self._config.min_position_pct

        # Position size = Target risk / Stop loss
        size_pct = target_risk_pct / sl_pct * 100

        # Adjust for volatility if provided
        if volatility and volatility > 0:
            # Higher volatility = smaller position
            vol_adjustment = 1.0 / (1 + volatility)
            size_pct *= vol_adjustment

        return size_pct

    def _fixed_size(self) -> float:
        """Return fixed position size."""
        # Use middle of allowed range
        return (self._config.min_position_pct + self._config.max_position_pct) / 2

    def _apply_drawdown_adjustment(
        self,
        size_pct: float,
        current_drawdown: float,
    ) -> float:
        """
        Reduce position size during drawdown.

        As drawdown increases, reduce size to preserve capital.

        Args:
            size_pct: Original position size
            current_drawdown: Current drawdown percentage

        Returns:
            Adjusted position size
        """
        max_dd = self._config.max_drawdown_pct

        if current_drawdown <= 0:
            return size_pct

        # Linear reduction from 100% to 50% as drawdown goes 0% to max_dd%
        if current_drawdown >= max_dd:
            return size_pct * 0.5

        reduction = 0.5 * (current_drawdown / max_dd)
        return size_pct * (1 - reduction)

    def _apply_limits(self, size_pct: float) -> float:
        """Apply min/max position size limits."""
        return max(
            self._config.min_position_pct,
            min(self._config.max_position_pct, size_pct),
        )

    def calculate_risk_pct(
        self,
        position_size_pct: float,
        sl_pct: float,
    ) -> float:
        """
        Calculate actual risk as percentage of capital.

        Args:
            position_size_pct: Position size percentage
            sl_pct: Stop-loss percentage

        Returns:
            Risk as percentage of total capital
        """
        return position_size_pct * sl_pct / 100

    def can_take_trade(
        self,
        current_drawdown: float,
        daily_loss_pct: float,
    ) -> bool:
        """
        Check if trading is allowed based on risk limits.

        Args:
            current_drawdown: Current drawdown percentage
            daily_loss_pct: Today's loss percentage

        Returns:
            True if trading allowed
        """
        # Check max drawdown
        if current_drawdown >= self._config.max_drawdown_pct:
            logger.warning(
                "trading_blocked_max_drawdown",
                current=current_drawdown,
                max=self._config.max_drawdown_pct,
            )
            return False

        # Check daily loss
        if daily_loss_pct >= self._config.max_daily_loss_pct:
            logger.warning(
                "trading_blocked_daily_loss",
                current=daily_loss_pct,
                max=self._config.max_daily_loss_pct,
            )
            return False

        return True
