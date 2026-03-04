# -*- coding: utf-8 -*-
"""
Limit Checker for ML System.

Validates trading limits before signal execution:
- Position limits
- Daily limits
- Symbol-specific limits
- Correlation limits

Usage:
    checker = LimitChecker()
    can_trade, reason = checker.check_all(symbol, direction, size)
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple

import structlog

from config.settings import settings


logger = structlog.get_logger(__name__)


class LimitChecker:
    """
    Validates all trading limits.

    Centralizes limit checking logic for consistent enforcement.
    """

    def __init__(self):
        """Initialize limit checker with config."""
        self._config = settings.ml.risk

        # Tracking state
        self._daily_trades: Dict[str, int] = {}  # date -> count
        self._symbol_last_trade: Dict[str, datetime] = {}  # symbol -> last trade time
        self._open_positions: Set[str] = set()
        self._daily_pnl: float = 0.0

        logger.info(
            "limit_checker_init",
            max_daily_trades=self._config.max_daily_trades,
            max_open_positions=self._config.max_open_positions,
        )

    def check_all(
        self,
        symbol: str,
        direction: int,
        position_size_pct: float,
        current_drawdown: float = 0.0,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check all limits at once.

        Args:
            symbol: Trading pair
            direction: 1 for long, -1 for short
            position_size_pct: Proposed position size
            current_drawdown: Current portfolio drawdown

        Returns:
            Tuple of (passed, failure_reason)
        """
        checks = [
            self.check_daily_trades(),
            self.check_open_positions(),
            self.check_daily_loss(),
            self.check_drawdown(current_drawdown),
            self.check_position_size(position_size_pct),
            self.check_symbol_cooldown(symbol),
            self.check_symbol_not_open(symbol),
        ]

        for passed, reason in checks:
            if not passed:
                logger.debug(
                    "limit_check_failed",
                    symbol=symbol,
                    reason=reason,
                )
                return False, reason

        return True, None

    def check_daily_trades(self) -> Tuple[bool, Optional[str]]:
        """Check daily trade count limit."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = self._daily_trades.get(today, 0)

        if count >= self._config.max_daily_trades:
            return False, f"Daily trade limit ({self._config.max_daily_trades}) reached"

        return True, None

    def check_open_positions(self) -> Tuple[bool, Optional[str]]:
        """Check open position count limit."""
        if len(self._open_positions) >= self._config.max_open_positions:
            return (
                False,
                f"Max open positions ({self._config.max_open_positions}) reached",
            )

        return True, None

    def check_daily_loss(self) -> Tuple[bool, Optional[str]]:
        """Check daily loss limit."""
        if self._daily_pnl <= -self._config.max_daily_loss_pct:
            return (
                False,
                f"Daily loss limit ({self._config.max_daily_loss_pct}%) hit",
            )

        return True, None

    def check_drawdown(self, current_drawdown: float) -> Tuple[bool, Optional[str]]:
        """Check maximum drawdown limit."""
        if current_drawdown >= self._config.max_drawdown_pct:
            return (
                False,
                f"Max drawdown ({self._config.max_drawdown_pct}%) reached",
            )

        return True, None

    def check_position_size(self, size_pct: float) -> Tuple[bool, Optional[str]]:
        """Check position size limits."""
        if size_pct < self._config.min_position_pct:
            return (
                False,
                f"Position size {size_pct:.2f}% below minimum {self._config.min_position_pct}%",
            )

        if size_pct > self._config.max_position_pct:
            return (
                False,
                f"Position size {size_pct:.2f}% above maximum {self._config.max_position_pct}%",
            )

        return True, None

    def check_symbol_cooldown(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """Check symbol-specific cooldown."""
        opt_config = settings.ml.optimization

        if symbol in self._symbol_last_trade:
            last_trade = self._symbol_last_trade[symbol]
            cooldown = timedelta(minutes=opt_config.symbol_cooldown_minutes)
            next_allowed = last_trade + cooldown

            if datetime.now(timezone.utc) < next_allowed:
                remaining = (next_allowed - datetime.now(timezone.utc)).seconds // 60
                return (
                    False,
                    f"Symbol cooldown: {remaining} minutes remaining",
                )

        return True, None

    def check_symbol_not_open(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """Check symbol doesn't have open position."""
        if symbol in self._open_positions:
            return False, f"Already in position for {symbol}"

        return True, None

    def record_trade_open(self, symbol: str) -> None:
        """Record opening a trade."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._daily_trades[today] = self._daily_trades.get(today, 0) + 1
        self._symbol_last_trade[symbol] = datetime.now(timezone.utc)
        self._open_positions.add(symbol)

        logger.debug(
            "trade_recorded",
            symbol=symbol,
            daily_count=self._daily_trades[today],
        )

    def record_trade_close(self, symbol: str, pnl_pct: float) -> None:
        """Record closing a trade."""
        if symbol in self._open_positions:
            self._open_positions.remove(symbol)

        self._daily_pnl += pnl_pct

        logger.debug(
            "trade_closed",
            symbol=symbol,
            pnl_pct=pnl_pct,
            daily_pnl=self._daily_pnl,
        )

    def reset_daily(self) -> None:
        """Reset daily counters (call at midnight UTC)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Clean old daily data
        old_dates = [d for d in self._daily_trades if d != today]
        for d in old_dates:
            del self._daily_trades[d]

        self._daily_pnl = 0.0

        logger.info("daily_limits_reset")

    def get_remaining_capacity(self) -> Dict:
        """Get remaining trading capacity."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        return {
            "daily_trades_remaining": max(
                0,
                self._config.max_daily_trades - self._daily_trades.get(today, 0),
            ),
            "positions_remaining": max(
                0,
                self._config.max_open_positions - len(self._open_positions),
            ),
            "daily_loss_remaining": max(
                0,
                self._config.max_daily_loss_pct + self._daily_pnl,
            ),
            "open_positions": list(self._open_positions),
        }
