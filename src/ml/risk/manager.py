# -*- coding: utf-8 -*-
"""
Risk Manager for ML System.

Manages portfolio-level risk:
- Position limits
- Correlation tracking
- Drawdown monitoring
- Daily trade limits

Usage:
    risk_manager = RiskManager()
    can_trade = risk_manager.can_open_position("BTCUSDT")
    risk_manager.record_trade_result("BTCUSDT", pnl=-1.5)
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import structlog

from config.settings import settings
from .position_sizer import PositionSizer


logger = structlog.get_logger(__name__)


class RiskManager:
    """
    Portfolio-level risk management.

    Tracks:
    - Open positions and their sizes
    - Daily P&L and trade count
    - Correlation between positions
    - Overall drawdown
    """

    def __init__(self):
        """Initialize risk manager."""
        self._config = settings.ml.risk
        self._position_sizer = PositionSizer()

        # State tracking
        self._open_positions: Dict[str, float] = {}  # symbol -> size_pct
        self._position_entry_prices: Dict[str, float] = {}  # symbol -> entry
        self._position_directions: Dict[str, int] = {}  # symbol -> 1 or -1

        # Daily tracking
        self._daily_trades: int = 0
        self._daily_pnl: float = 0.0
        self._daily_reset_date: datetime = datetime.now(timezone.utc).date()

        # Correlation groups (symbols that tend to move together)
        self._correlation_groups: Dict[str, Set[str]] = {
            "btc": {"BTCUSDT", "BTCDOMUSDT"},
            "eth": {"ETHUSDT", "ETHBTC"},
            "defi": {"AAVEUSDT", "UNIUSDT", "MKRUSDT", "COMPUSDT"},
            "layer1": {"SOLUSDT", "AVAXUSDT", "ATOMUSDT", "NEARUSDT"},
            "meme": {"DOGEUSDT", "SHIBUSDT", "PEPEUSDT"},
        }

        # Performance tracking
        self._equity_curve: List[float] = [100.0]  # Start at 100
        self._peak_equity: float = 100.0
        self._current_drawdown: float = 0.0

        logger.info(
            "risk_manager_init",
            max_positions=self._config.max_open_positions,
            max_daily_trades=self._config.max_daily_trades,
        )

    @property
    def current_drawdown(self) -> float:
        """Current drawdown percentage."""
        return self._current_drawdown

    @property
    def open_position_count(self) -> int:
        """Number of open positions."""
        return len(self._open_positions)

    @property
    def daily_trades(self) -> int:
        """Number of trades today."""
        self._check_daily_reset()
        return self._daily_trades

    @property
    def daily_pnl(self) -> float:
        """Today's P&L percentage."""
        self._check_daily_reset()
        return self._daily_pnl

    def can_open_position(
        self,
        symbol: str,
        direction: int = 1,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a new position can be opened.

        Args:
            symbol: Symbol to trade
            direction: 1 for long, -1 for short

        Returns:
            Tuple of (can_trade, reason_if_not)
        """
        self._check_daily_reset()

        # Check if already in position
        if symbol in self._open_positions:
            return False, f"Already in position for {symbol}"

        # Check max positions
        if len(self._open_positions) >= self._config.max_open_positions:
            return False, f"Max positions ({self._config.max_open_positions}) reached"

        # Check daily trade limit
        if self._daily_trades >= self._config.max_daily_trades:
            return False, f"Max daily trades ({self._config.max_daily_trades}) reached"

        # Check daily loss
        if self._daily_pnl <= -self._config.max_daily_loss_pct:
            return False, f"Daily loss limit ({self._config.max_daily_loss_pct}%) hit"

        # Check drawdown
        if self._current_drawdown >= self._config.max_drawdown_pct:
            return False, f"Max drawdown ({self._config.max_drawdown_pct}%) reached"

        # Check correlation limits
        correlated = self._count_correlated_positions(symbol, direction)
        if correlated >= self._config.max_correlated_positions:
            return (
                False,
                f"Max correlated positions ({self._config.max_correlated_positions}) reached",
            )

        return True, None

    def open_position(
        self,
        symbol: str,
        direction: int,
        size_pct: float,
        entry_price: float,
    ) -> None:
        """
        Record opening a new position.

        Args:
            symbol: Trading pair
            direction: 1 for long, -1 for short
            size_pct: Position size as percentage of capital
            entry_price: Entry price
        """
        self._check_daily_reset()

        self._open_positions[symbol] = size_pct
        self._position_entry_prices[symbol] = entry_price
        self._position_directions[symbol] = direction
        self._daily_trades += 1

        logger.info(
            "position_opened",
            symbol=symbol,
            direction="LONG" if direction == 1 else "SHORT",
            size_pct=size_pct,
            entry_price=entry_price,
            open_positions=len(self._open_positions),
        )

    def close_position(
        self,
        symbol: str,
        exit_price: float,
    ) -> float:
        """
        Record closing a position and calculate P&L.

        Args:
            symbol: Trading pair
            exit_price: Exit price

        Returns:
            P&L percentage
        """
        if symbol not in self._open_positions:
            logger.warning("closing_unknown_position", symbol=symbol)
            return 0.0

        entry = self._position_entry_prices[symbol]
        direction = self._position_directions[symbol]
        size = self._open_positions[symbol]

        # Calculate P&L
        if direction == 1:  # Long
            pnl_pct = (exit_price - entry) / entry * 100
        else:  # Short
            pnl_pct = (entry - exit_price) / entry * 100

        # Weight by position size
        portfolio_pnl = pnl_pct * size / 100

        # Update daily P&L
        self._daily_pnl += portfolio_pnl

        # Update equity curve
        self._update_equity(portfolio_pnl)

        # Remove position
        del self._open_positions[symbol]
        del self._position_entry_prices[symbol]
        del self._position_directions[symbol]

        logger.info(
            "position_closed",
            symbol=symbol,
            pnl_pct=pnl_pct,
            portfolio_pnl=portfolio_pnl,
            daily_pnl=self._daily_pnl,
        )

        return pnl_pct

    def record_trade_result(
        self,
        symbol: str,
        pnl_pct: float,
        size_pct: Optional[float] = None,
    ) -> None:
        """
        Record a trade result without full position tracking.

        Useful for backtesting or when position tracking isn't needed.

        Args:
            symbol: Trading pair
            pnl_pct: Trade P&L percentage
            size_pct: Optional position size (default 1%)
        """
        self._check_daily_reset()

        size = size_pct or 1.0
        portfolio_pnl = pnl_pct * size / 100

        self._daily_pnl += portfolio_pnl
        self._daily_trades += 1
        self._update_equity(portfolio_pnl)

        logger.debug(
            "trade_result_recorded",
            symbol=symbol,
            pnl_pct=pnl_pct,
            daily_pnl=self._daily_pnl,
        )

    def get_position_size(
        self,
        win_probability: float,
        risk_reward: float,
        sl_pct: float,
        volatility: Optional[float] = None,
    ) -> float:
        """
        Get recommended position size.

        Args:
            win_probability: Win probability (0-1)
            risk_reward: Risk/reward ratio
            sl_pct: Stop-loss percentage
            volatility: Optional volatility

        Returns:
            Position size as percentage of capital
        """
        return self._position_sizer.calculate_size(
            win_probability=win_probability,
            risk_reward=risk_reward,
            sl_pct=sl_pct,
            volatility=volatility,
            current_drawdown=self._current_drawdown,
        )

    def _count_correlated_positions(self, symbol: str, direction: int) -> int:
        """Count positions correlated with the given symbol."""
        # Find symbol's correlation group
        symbol_group = None
        for group_name, symbols in self._correlation_groups.items():
            if symbol in symbols:
                symbol_group = group_name
                break

        if symbol_group is None:
            return 0

        # Count same-direction positions in same group
        count = 0
        group_symbols = self._correlation_groups[symbol_group]

        for pos_symbol, pos_direction in self._position_directions.items():
            if pos_symbol in group_symbols and pos_direction == direction:
                count += 1

        return count

    def _check_daily_reset(self) -> None:
        """Reset daily stats if new day."""
        today = datetime.now(timezone.utc).date()
        if today > self._daily_reset_date:
            self._daily_trades = 0
            self._daily_pnl = 0.0
            self._daily_reset_date = today
            logger.info("daily_stats_reset")

    def _update_equity(self, pnl_pct: float) -> None:
        """Update equity curve and drawdown."""
        current = self._equity_curve[-1]
        new_equity = current * (1 + pnl_pct / 100)
        self._equity_curve.append(new_equity)

        # Update peak and drawdown
        if new_equity > self._peak_equity:
            self._peak_equity = new_equity

        self._current_drawdown = (
            (self._peak_equity - new_equity) / self._peak_equity * 100
        )

    def get_portfolio_state(self) -> Dict:
        """Get current portfolio state."""
        return {
            "open_positions": len(self._open_positions),
            "positions": dict(self._open_positions),
            "daily_trades": self._daily_trades,
            "daily_pnl": self._daily_pnl,
            "current_drawdown": self._current_drawdown,
            "peak_equity": self._peak_equity,
            "current_equity": self._equity_curve[-1] if self._equity_curve else 100,
        }

    def get_risk_summary(self) -> Dict:
        """Get risk metrics summary."""
        equity = self._equity_curve

        if len(equity) < 2:
            return {
                "total_return": 0,
                "max_drawdown": 0,
                "sharpe_ratio": 0,
                "trades": self._daily_trades,
            }

        # Calculate returns
        returns = np.diff(equity) / equity[:-1]

        # Total return
        total_return = (equity[-1] / equity[0] - 1) * 100

        # Max drawdown
        running_max = np.maximum.accumulate(equity)
        drawdowns = (running_max - equity) / running_max * 100
        max_drawdown = np.max(drawdowns)

        # Sharpe ratio (assuming daily, annualized)
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
        else:
            sharpe = 0

        return {
            "total_return": float(total_return),
            "max_drawdown": float(max_drawdown),
            "current_drawdown": self._current_drawdown,
            "sharpe_ratio": float(sharpe),
            "trades": len(equity) - 1,
            "win_rate": (
                np.sum(returns > 0) / len(returns) * 100
                if len(returns) > 0 else 0
            ),
        }

    def reset(self) -> None:
        """Reset all state."""
        self._open_positions.clear()
        self._position_entry_prices.clear()
        self._position_directions.clear()
        self._daily_trades = 0
        self._daily_pnl = 0.0
        self._equity_curve = [100.0]
        self._peak_equity = 100.0
        self._current_drawdown = 0.0

        logger.info("risk_manager_reset")
