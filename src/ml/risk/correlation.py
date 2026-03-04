# -*- coding: utf-8 -*-
"""
Correlation Filter for ML System.

Filters signals based on correlation with existing positions.
Prevents over-exposure to correlated assets.

Usage:
    filter = CorrelationFilter()
    if filter.can_add_signal(signal, open_positions):
        # Safe to add
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import numpy as np
import pandas as pd
import structlog

from config.settings import settings

if TYPE_CHECKING:
    from src.signals.models import TradeSignal


logger = structlog.get_logger(__name__)


# Pre-defined correlation groups
# Symbols in the same group tend to move together
CORRELATION_GROUPS: Dict[str, Set[str]] = {
    "btc_ecosystem": {"BTCUSDT", "BTCDOMUSDT", "WBTCUSDT"},
    "eth_ecosystem": {"ETHUSDT", "ETHBTC", "STETHUSDT"},
    "bnb_ecosystem": {"BNBUSDT", "BNBBTC"},
    "layer1": {"SOLUSDT", "AVAXUSDT", "ATOMUSDT", "NEARUSDT", "DOTUSDT", "ADAUSDT"},
    "layer2": {"MATICUSDT", "ARBUSDT", "OPUSDT"},
    "defi": {"AAVEUSDT", "UNIUSDT", "MKRUSDT", "COMPUSDT", "CRVUSDT", "SNXUSDT"},
    "meme": {"DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "FLOKIUSDT"},
    "ai": {"FETUSDT", "AGIXUSDT", "OCEANUSDT", "RNDRUSDT"},
    "gaming": {"AXSUSDT", "SANDUSDT", "MANAUSDT", "GALAUSDT", "ENJUSDT"},
    "exchange_tokens": {"FTTUSDT", "BNBUSDT", "OKBUSDT"},  # Note: FTT delisted
}


@dataclass
class Position:
    """Simple position representation."""

    symbol: str
    direction: int  # 1 = long, -1 = short
    size_pct: float
    entry_time: datetime


@dataclass
class CorrelationCheckResult:
    """Result of correlation check."""

    can_add: bool
    reason: Optional[str] = None
    correlated_with: List[str] = None
    correlation_value: float = 0.0

    def __post_init__(self):
        if self.correlated_with is None:
            self.correlated_with = []


class CorrelationFilter:
    """
    Filters signals based on correlation with open positions.

    Prevents:
    - Multiple positions in same direction on correlated assets
    - Over-concentration in single correlation group
    """

    def __init__(
        self,
        max_correlation: Optional[float] = None,
        max_same_group_positions: Optional[int] = None,
        correlation_matrix: Optional[pd.DataFrame] = None,
    ):
        """
        Initialize correlation filter.

        Args:
            max_correlation: Maximum allowed correlation (default from config)
            max_same_group_positions: Max positions in same group (default from config)
            correlation_matrix: Optional custom correlation matrix
        """
        self._config = settings.ml.risk
        self._max_correlation = max_correlation or self._config.correlation_threshold
        self._max_same_group = max_same_group_positions or self._config.max_correlated_positions

        # Symbol to group mapping
        self._symbol_to_group: Dict[str, str] = {}
        for group_name, symbols in CORRELATION_GROUPS.items():
            for symbol in symbols:
                self._symbol_to_group[symbol] = group_name

        # Correlation matrix (can be updated with real data)
        self._correlation_matrix = correlation_matrix

        logger.info(
            "correlation_filter_init",
            max_correlation=self._max_correlation,
            max_same_group=self._max_same_group,
            groups=len(CORRELATION_GROUPS),
        )

    def can_add_signal(
        self,
        signal: "TradeSignal",
        open_positions: List[Position],
    ) -> CorrelationCheckResult:
        """
        Check if a new signal can be added.

        Args:
            signal: New trading signal
            open_positions: Currently open positions

        Returns:
            CorrelationCheckResult with decision and reason
        """
        if not open_positions:
            return CorrelationCheckResult(can_add=True)

        symbol = signal.symbol
        direction = 1 if signal.direction.value == "LONG" else -1

        # Get signal's correlation group
        signal_group = self._symbol_to_group.get(symbol)

        # Count positions in same group with same direction
        same_group_same_dir = []
        correlated_positions = []

        for pos in open_positions:
            # Same symbol
            if pos.symbol == symbol:
                return CorrelationCheckResult(
                    can_add=False,
                    reason=f"Already have position in {symbol}",
                    correlated_with=[symbol],
                    correlation_value=1.0,
                )

            pos_group = self._symbol_to_group.get(pos.symbol)

            # Same group
            if signal_group and pos_group == signal_group:
                # Same direction in same group
                if pos.direction == direction:
                    same_group_same_dir.append(pos.symbol)
                    correlated_positions.append(pos.symbol)

            # Check correlation matrix if available
            elif self._correlation_matrix is not None:
                corr = self._get_correlation(symbol, pos.symbol)
                if corr > self._max_correlation and pos.direction == direction:
                    correlated_positions.append(pos.symbol)

        # Check same group limit
        if len(same_group_same_dir) >= self._max_same_group:
            return CorrelationCheckResult(
                can_add=False,
                reason=f"Max {self._max_same_group} same-direction positions in {signal_group} group",
                correlated_with=same_group_same_dir,
                correlation_value=0.8,  # Estimated for same group
            )

        # Check total correlated positions
        if len(correlated_positions) >= self._max_same_group:
            return CorrelationCheckResult(
                can_add=False,
                reason=f"Too many correlated positions ({len(correlated_positions)})",
                correlated_with=correlated_positions,
                correlation_value=self._max_correlation,
            )

        return CorrelationCheckResult(
            can_add=True,
            correlated_with=correlated_positions,
        )

    def filter_correlated_signals(
        self,
        signals: List["TradeSignal"],
        open_positions: List[Position],
    ) -> List["TradeSignal"]:
        """
        Filter a list of signals, keeping only non-correlated ones.

        Args:
            signals: List of potential signals
            open_positions: Currently open positions

        Returns:
            Filtered list of signals
        """
        filtered = []
        # Track positions we'll add
        simulated_positions = list(open_positions)

        for signal in signals:
            result = self.can_add_signal(signal, simulated_positions)

            if result.can_add:
                filtered.append(signal)
                # Add to simulated positions for next check
                direction = 1 if signal.direction.value == "LONG" else -1
                simulated_positions.append(
                    Position(
                        symbol=signal.symbol,
                        direction=direction,
                        size_pct=1.0,  # Placeholder
                        entry_time=datetime.now(timezone.utc),
                    )
                )
            else:
                logger.debug(
                    "signal_filtered_correlation",
                    symbol=signal.symbol,
                    reason=result.reason,
                )

        logger.info(
            "correlation_filter_applied",
            original=len(signals),
            filtered=len(filtered),
            removed=len(signals) - len(filtered),
        )

        return filtered

    def _get_correlation(self, symbol1: str, symbol2: str) -> float:
        """Get correlation between two symbols."""
        if self._correlation_matrix is None:
            # Default: check if in same group
            group1 = self._symbol_to_group.get(symbol1)
            group2 = self._symbol_to_group.get(symbol2)
            if group1 and group1 == group2:
                return 0.8  # Estimated for same group
            return 0.3  # Default low correlation

        try:
            return self._correlation_matrix.loc[symbol1, symbol2]
        except KeyError:
            return 0.3  # Default if not in matrix

    def update_correlation_matrix(self, matrix: pd.DataFrame) -> None:
        """
        Update correlation matrix with new data.

        Args:
            matrix: New correlation matrix (symbol x symbol)
        """
        self._correlation_matrix = matrix
        logger.info(
            "correlation_matrix_updated",
            symbols=len(matrix.columns),
        )

    def calculate_portfolio_correlation(
        self,
        positions: List[Position],
    ) -> float:
        """
        Calculate average correlation of portfolio.

        Args:
            positions: List of positions

        Returns:
            Average pairwise correlation (0-1)
        """
        if len(positions) < 2:
            return 0.0

        correlations = []
        for i, pos1 in enumerate(positions):
            for pos2 in positions[i + 1:]:
                corr = self._get_correlation(pos1.symbol, pos2.symbol)
                # Weight by direction (same direction = more correlated risk)
                if pos1.direction == pos2.direction:
                    corr = min(1.0, corr * 1.2)
                else:
                    corr = max(0.0, corr * 0.8)
                correlations.append(corr)

        return np.mean(correlations) if correlations else 0.0

    def get_group(self, symbol: str) -> Optional[str]:
        """Get correlation group for a symbol."""
        return self._symbol_to_group.get(symbol)

    def get_group_exposure(
        self,
        positions: List[Position],
    ) -> Dict[str, float]:
        """
        Get exposure by correlation group.

        Args:
            positions: List of positions

        Returns:
            Dict mapping group name to total size %
        """
        exposure = {}

        for pos in positions:
            group = self._symbol_to_group.get(pos.symbol, "ungrouped")
            # Direction-adjusted: long adds, short subtracts
            size = pos.size_pct * pos.direction
            exposure[group] = exposure.get(group, 0) + size

        return exposure
