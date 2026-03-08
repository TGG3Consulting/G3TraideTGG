# -*- coding: utf-8 -*-
"""
Metrics Tracker - Статистика и PnL tracking.

Отслеживает:
- Realized PnL (daily, weekly, monthly, total)
- Win rate, profit factor
- Per-strategy и per-symbol статистика
- Drawdown
"""

import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from collections import defaultdict

from ..core.models import Position, PositionSide

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Запись о закрытой сделке."""
    position_id: str
    symbol: str
    direction: str  # LONG/SHORT
    strategy: str
    entry_price: float
    exit_price: float
    quantity: float
    realized_pnl: float
    exit_reason: str  # SL/TP/TIMEOUT/MISSING_TP/MANUAL
    opened_at: datetime
    closed_at: datetime
    hold_time_hours: float


@dataclass
class PeriodStats:
    """Статистика за период."""
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_win_pnl: float = 0.0
    total_loss_pnl: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0

    # Алиасы для совместимости
    @property
    def total_trades(self) -> int:
        """Alias for trades."""
        return self.trades

    @property
    def winning_trades(self) -> int:
        """Alias for wins."""
        return self.wins

    @property
    def losing_trades(self) -> int:
        """Alias for losses."""
        return self.losses

    @property
    def gross_profit(self) -> float:
        """Alias for total_win_pnl."""
        return self.total_win_pnl

    @property
    def gross_loss(self) -> float:
        """Alias for total_loss_pnl (absolute value)."""
        return abs(self.total_loss_pnl)

    @property
    def win_rate(self) -> float:
        """Win rate в %."""
        if self.trades == 0:
            return 0.0
        return (self.wins / self.trades) * 100

    @property
    def avg_win(self) -> float:
        """Средний win."""
        if self.wins == 0:
            return 0.0
        return self.total_win_pnl / self.wins

    @property
    def avg_loss(self) -> float:
        """Средний loss."""
        if self.losses == 0:
            return 0.0
        return self.total_loss_pnl / self.losses

    @property
    def profit_factor(self) -> float:
        """Profit Factor = gross profit / gross loss."""
        if self.total_loss_pnl == 0:
            return float('inf') if self.total_win_pnl > 0 else 0.0
        return abs(self.total_win_pnl / self.total_loss_pnl)

    @property
    def expectancy(self) -> float:
        """Expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)."""
        if self.trades == 0:
            return 0.0
        win_rate = self.wins / self.trades
        loss_rate = self.losses / self.trades
        return (win_rate * self.avg_win) + (loss_rate * self.avg_loss)

    def add_trade(self, pnl: float) -> None:
        """Добавить сделку."""
        self.trades += 1
        self.total_pnl += pnl

        if pnl >= 0:
            self.wins += 1
            self.total_win_pnl += pnl
            self.max_win = max(self.max_win, pnl)
        else:
            self.losses += 1
            self.total_loss_pnl += pnl
            self.max_loss = min(self.max_loss, pnl)


class MetricsTracker:
    """
    Трекер метрик и статистики.

    Использование:
        metrics = MetricsTracker(initial_balance=1000.0)
        metrics.record_trade(position, exit_reason, realized_pnl)
        dashboard = metrics.get_dashboard()
    """

    def __init__(self, initial_balance: float = 0.0):
        """
        Инициализация.

        Args:
            initial_balance: Начальный баланс для расчёта equity и drawdown
        """
        # Начальный баланс
        self.initial_balance = initial_balance

        # История сделок
        self.trades: List[TradeRecord] = []

        # Общая статистика
        self.total_stats = PeriodStats()

        # По стратегиям
        self.strategy_stats: Dict[str, PeriodStats] = defaultdict(PeriodStats)

        # По символам
        self.symbol_stats: Dict[str, PeriodStats] = defaultdict(PeriodStats)

        # По exit reason
        self.exit_reason_stats: Dict[str, PeriodStats] = defaultdict(PeriodStats)

        # По направлению
        self.direction_stats: Dict[str, PeriodStats] = defaultdict(PeriodStats)

        # Equity curve (PnL накопительно)
        self.equity_curve: List[tuple] = []  # [(timestamp, cumulative_pnl), ...]

        # Daily PnL
        self.daily_pnl: Dict[str, float] = defaultdict(float)  # "2024-03-07" -> pnl

        # Drawdown tracking
        self._peak_equity: float = initial_balance  # Пик начинается с начального баланса
        self._current_equity: float = initial_balance  # Текущий equity = начальный баланс
        self._max_drawdown: float = 0.0
        self._max_drawdown_pct: float = 0.0

        # Start time
        self._start_time: datetime = datetime.now(timezone.utc)

    def record_trade(
        self,
        position: Position = None,
        exit_reason: str = None,
        realized_pnl: float = None,
        *,
        # Альтернативный API для прямой записи без Position
        symbol: str = None,
        direction: str = None,
        entry_price: float = None,
        exit_price: float = None,
        quantity: float = None,
        strategy: str = None,
        opened_at: datetime = None,
        closed_at: datetime = None,
        hold_duration_hours: float = None,
    ) -> None:
        """
        Записать закрытую сделку.

        Два способа вызова:
        1. record_trade(position, exit_reason, realized_pnl) - с Position объектом
        2. record_trade(symbol=..., direction=..., ...) - с прямыми параметрами

        Args:
            position: Закрытая позиция (опционально если указаны прямые параметры)
            exit_reason: Причина закрытия
            realized_pnl: Realized PnL в USDT
            symbol: Символ (альт. API)
            direction: Направление LONG/SHORT (альт. API)
            entry_price: Цена входа (альт. API)
            exit_price: Цена выхода (альт. API)
            quantity: Количество (альт. API)
            strategy: Стратегия (альт. API)
            opened_at: Время открытия (альт. API)
            closed_at: Время закрытия (альт. API)
            hold_duration_hours: Время удержания в часах (альт. API)
        """
        now = datetime.now(timezone.utc)

        # Определяем какой API используется
        if position is not None:
            # Стандартный API с Position
            hold_time = 0.0
            if position.opened_at:
                hold_time = (now - position.opened_at).total_seconds() / 3600

            record = TradeRecord(
                position_id=position.position_id,
                symbol=position.symbol,
                direction=position.side.value,
                strategy=position.strategy or "unknown",
                entry_price=position.entry_price,
                exit_price=position.exit_price,
                quantity=position.quantity,
                realized_pnl=realized_pnl,
                exit_reason=exit_reason,
                opened_at=position.opened_at or now,
                closed_at=now,
                hold_time_hours=hold_time,
            )
        elif symbol is not None:
            # Альтернативный API с прямыми параметрами
            import uuid
            record = TradeRecord(
                position_id=f"DIRECT_{uuid.uuid4().hex[:8]}",
                symbol=symbol,
                direction=direction or "LONG",
                strategy=strategy or "unknown",
                entry_price=entry_price or 0.0,
                exit_price=exit_price or 0.0,
                quantity=quantity or 0.0,
                realized_pnl=realized_pnl or 0.0,
                exit_reason=exit_reason or "UNKNOWN",
                opened_at=opened_at or now,
                closed_at=closed_at or now,
                hold_time_hours=hold_duration_hours or 0.0,
            )
        else:
            raise ValueError("Either position or symbol must be provided")

        self.trades.append(record)

        # Обновляем статистику
        self.total_stats.add_trade(record.realized_pnl)
        self.strategy_stats[record.strategy].add_trade(record.realized_pnl)
        self.symbol_stats[record.symbol].add_trade(record.realized_pnl)
        self.exit_reason_stats[record.exit_reason].add_trade(record.realized_pnl)
        self.direction_stats[record.direction].add_trade(record.realized_pnl)

        # Обновляем equity curve
        self._current_equity += record.realized_pnl
        self.equity_curve.append((now, self._current_equity))

        # Обновляем daily PnL
        day_key = now.strftime("%Y-%m-%d")
        self.daily_pnl[day_key] += record.realized_pnl

        # Обновляем drawdown
        if self._current_equity > self._peak_equity:
            self._peak_equity = self._current_equity

        if self._peak_equity > 0:
            drawdown = self._peak_equity - self._current_equity
            drawdown_pct = (drawdown / self._peak_equity) * 100

            if drawdown > self._max_drawdown:
                self._max_drawdown = drawdown
            if drawdown_pct > self._max_drawdown_pct:
                self._max_drawdown_pct = drawdown_pct

        logger.debug(
            f"Trade recorded: {record.symbol} {record.direction} "
            f"PnL={record.realized_pnl:+.2f} Total={self._current_equity:+.2f}"
        )

    def get_period_stats(self, days: int = 0) -> PeriodStats:
        """
        Получить статистику за период.

        Args:
            days: Количество дней (0 = все время)

        Returns:
            PeriodStats за указанный период
        """
        if days == 0:
            return self.total_stats

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stats = PeriodStats()

        for trade in self.trades:
            if trade.closed_at >= cutoff:
                stats.add_trade(trade.realized_pnl)

        return stats

    def get_today_stats(self) -> PeriodStats:
        """Статистика за сегодня."""
        today = datetime.now(timezone.utc).date()
        stats = PeriodStats()

        for trade in self.trades:
            if trade.closed_at.date() == today:
                stats.add_trade(trade.realized_pnl)

        return stats

    def get_dashboard(self) -> Dict[str, Any]:
        """
        Получить полный dashboard.

        Returns:
            Dict со всей статистикой
        """
        today_stats = self.get_today_stats()
        week_stats = self.get_period_stats(7)
        month_stats = self.get_period_stats(30)

        # Top performers (symbols)
        top_symbols = sorted(
            self.symbol_stats.items(),
            key=lambda x: x[1].total_pnl,
            reverse=True
        )[:5]

        # Worst performers
        worst_symbols = sorted(
            self.symbol_stats.items(),
            key=lambda x: x[1].total_pnl
        )[:5]

        # Strategy breakdown
        strategy_breakdown = {
            name: {
                "trades": stats.trades,
                "win_rate": f"{stats.win_rate:.1f}%",
                "pnl": f"{stats.total_pnl:+.2f}",
                "profit_factor": f"{stats.profit_factor:.2f}" if stats.profit_factor != float('inf') else "∞",
            }
            for name, stats in self.strategy_stats.items()
        }

        # Exit reason breakdown
        exit_breakdown = {
            reason: {
                "trades": stats.trades,
                "pnl": f"{stats.total_pnl:+.2f}",
            }
            for reason, stats in self.exit_reason_stats.items()
        }

        # Direction breakdown
        direction_breakdown = {
            direction: {
                "trades": stats.trades,
                "win_rate": f"{stats.win_rate:.1f}%",
                "pnl": f"{stats.total_pnl:+.2f}",
            }
            for direction, stats in self.direction_stats.items()
        }

        # Recent daily PnL (last 7 days)
        recent_days = sorted(self.daily_pnl.items(), reverse=True)[:7]

        return {
            # === SUMMARY ===
            "total_trades": self.total_stats.trades,
            "total_pnl": self.total_stats.total_pnl,
            "win_rate": self.total_stats.win_rate,
            "profit_factor": self.total_stats.profit_factor,
            "expectancy": self.total_stats.expectancy,
            "max_win": self.total_stats.max_win,
            "max_loss": self.total_stats.max_loss,
            "avg_win": self.total_stats.avg_win,
            "avg_loss": self.total_stats.avg_loss,

            # === DRAWDOWN ===
            "current_equity": self._current_equity,
            "peak_equity": self._peak_equity,
            "max_drawdown": self._max_drawdown,
            "max_drawdown_pct": self._max_drawdown_pct,

            # === PERIODS ===
            "today": {
                "trades": today_stats.trades,
                "pnl": today_stats.total_pnl,
                "win_rate": today_stats.win_rate,
            },
            "week": {
                "trades": week_stats.trades,
                "pnl": week_stats.total_pnl,
                "win_rate": week_stats.win_rate,
            },
            "month": {
                "trades": month_stats.trades,
                "pnl": month_stats.total_pnl,
                "win_rate": month_stats.win_rate,
            },

            # === BREAKDOWNS ===
            "by_strategy": strategy_breakdown,
            "by_exit_reason": exit_breakdown,
            "by_direction": direction_breakdown,
            "top_symbols": [(s, f"{st.total_pnl:+.2f}") for s, st in top_symbols],
            "worst_symbols": [(s, f"{st.total_pnl:+.2f}") for s, st in worst_symbols],

            # === DAILY ===
            "recent_daily_pnl": recent_days,

            # === RUNTIME ===
            "runtime_hours": (datetime.now(timezone.utc) - self._start_time).total_seconds() / 3600,
        }

    def format_dashboard(self) -> str:
        """
        Форматировать dashboard для вывода в консоль.

        Returns:
            Форматированная строка
        """
        d = self.get_dashboard()

        lines = [
            "",
            "=" * 60,
            "                    TRADING DASHBOARD",
            "=" * 60,
            "",
            "─── SUMMARY ───",
            f"  Total Trades:    {d['total_trades']}",
            f"  Total PnL:       {d['total_pnl']:+.2f} USDT",
            f"  Win Rate:        {d['win_rate']:.1f}%",
            f"  Profit Factor:   {d['profit_factor']:.2f}" if d['profit_factor'] != float('inf') else "  Profit Factor:   ∞",
            f"  Expectancy:      {d['expectancy']:+.2f} USDT/trade",
            f"  Max Win:         {d['max_win']:+.2f} USDT",
            f"  Max Loss:        {d['max_loss']:+.2f} USDT",
            f"  Avg Win:         {d['avg_win']:+.2f} USDT",
            f"  Avg Loss:        {d['avg_loss']:+.2f} USDT",
            "",
            "─── DRAWDOWN ───",
            f"  Current Equity:  {d['current_equity']:+.2f} USDT",
            f"  Peak Equity:     {d['peak_equity']:+.2f} USDT",
            f"  Max Drawdown:    {d['max_drawdown']:.2f} USDT ({d['max_drawdown_pct']:.1f}%)",
            "",
            "─── PERIODS ───",
            f"  Today:           {d['today']['trades']} trades, {d['today']['pnl']:+.2f} USDT, {d['today']['win_rate']:.1f}% WR",
            f"  Week:            {d['week']['trades']} trades, {d['week']['pnl']:+.2f} USDT, {d['week']['win_rate']:.1f}% WR",
            f"  Month:           {d['month']['trades']} trades, {d['month']['pnl']:+.2f} USDT, {d['month']['win_rate']:.1f}% WR",
            "",
        ]

        # Strategy breakdown
        if d['by_strategy']:
            lines.append("─── BY STRATEGY ───")
            for name, stats in d['by_strategy'].items():
                lines.append(f"  {name}: {stats['trades']} trades, {stats['pnl']} USDT, {stats['win_rate']} WR, PF={stats['profit_factor']}")
            lines.append("")

        # Direction breakdown
        if d['by_direction']:
            lines.append("─── BY DIRECTION ───")
            for direction, stats in d['by_direction'].items():
                lines.append(f"  {direction}: {stats['trades']} trades, {stats['pnl']} USDT, {stats['win_rate']} WR")
            lines.append("")

        # Exit reason breakdown
        if d['by_exit_reason']:
            lines.append("─── BY EXIT REASON ───")
            for reason, stats in d['by_exit_reason'].items():
                lines.append(f"  {reason}: {stats['trades']} trades, {stats['pnl']} USDT")
            lines.append("")

        # Top/Worst symbols
        if d['top_symbols']:
            lines.append("─── TOP SYMBOLS ───")
            for symbol, pnl in d['top_symbols']:
                lines.append(f"  {symbol}: {pnl} USDT")
            lines.append("")

        if d['worst_symbols'] and any(float(pnl.replace('+', '')) < 0 for _, pnl in d['worst_symbols']):
            lines.append("─── WORST SYMBOLS ───")
            for symbol, pnl in d['worst_symbols']:
                if float(pnl.replace('+', '')) < 0:
                    lines.append(f"  {symbol}: {pnl} USDT")
            lines.append("")

        # Recent daily PnL
        if d['recent_daily_pnl']:
            lines.append("─── RECENT DAILY PnL ───")
            for day, pnl in d['recent_daily_pnl']:
                emoji = "🟢" if pnl >= 0 else "🔴"
                lines.append(f"  {emoji} {day}: {pnl:+.2f} USDT")
            lines.append("")

        lines.extend([
            f"Runtime: {d['runtime_hours']:.1f} hours",
            "=" * 60,
            "",
        ])

        return "\n".join(lines)

    def format_telegram_summary(self) -> str:
        """
        Форматировать краткую сводку для Telegram.

        Returns:
            HTML-форматированная строка
        """
        d = self.get_dashboard()

        pnl_emoji = "🟢" if d['total_pnl'] >= 0 else "🔴"

        message = (
            f"📊 <b>TRADING STATS</b>\n"
            f"\n"
            f"<b>Total Trades:</b> {d['total_trades']}\n"
            f"{pnl_emoji} <b>Total PnL:</b> {d['total_pnl']:+.2f} USDT\n"
            f"<b>Win Rate:</b> {d['win_rate']:.1f}%\n"
            f"<b>Profit Factor:</b> {d['profit_factor']:.2f}\n"
            f"\n"
            f"<b>Today:</b> {d['today']['pnl']:+.2f} USDT ({d['today']['trades']} trades)\n"
            f"<b>Week:</b> {d['week']['pnl']:+.2f} USDT ({d['week']['trades']} trades)\n"
            f"\n"
            f"<b>Max DD:</b> {d['max_drawdown']:.2f} USDT ({d['max_drawdown_pct']:.1f}%)\n"
            f"<b>Runtime:</b> {d['runtime_hours']:.1f}h"
        )

        return message

    def to_dict(self) -> Dict[str, Any]:
        """
        Сериализовать для сохранения в state.

        Returns:
            Dict для JSON
        """
        return {
            "initial_balance": self.initial_balance,
            "trades": [
                {
                    "position_id": t.position_id,
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "strategy": t.strategy,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "realized_pnl": t.realized_pnl,
                    "exit_reason": t.exit_reason,
                    "opened_at": t.opened_at.isoformat(),
                    "closed_at": t.closed_at.isoformat(),
                    "hold_time_hours": t.hold_time_hours,
                }
                for t in self.trades
            ],
            "peak_equity": self._peak_equity,
            "current_equity": self._current_equity,
            "max_drawdown": self._max_drawdown,
            "max_drawdown_pct": self._max_drawdown_pct,
            "start_time": self._start_time.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MetricsTracker":
        """
        Восстановить из сохранённого state.

        Args:
            data: Сохранённые данные

        Returns:
            MetricsTracker instance
        """
        initial_balance = data.get("initial_balance", 0.0)
        tracker = cls(initial_balance=initial_balance)

        # Restore start time
        if "start_time" in data:
            tracker._start_time = datetime.fromisoformat(data["start_time"])

        # Restore drawdown state (сохранённые значения - исторически корректны)
        tracker._peak_equity = data.get("peak_equity", 0.0)
        tracker._max_drawdown = data.get("max_drawdown", 0.0)
        tracker._max_drawdown_pct = data.get("max_drawdown_pct", 0.0)

        # Restore trades (replay to rebuild stats and equity curve)
        # Инкрементально вычисляем equity для правильного equity_curve
        cumulative_equity = 0.0

        for t_data in data.get("trades", []):
            # Используем .get() для совместимости с разными форматами данных
            record = TradeRecord(
                position_id=t_data.get("position_id", f"RESTORED_{len(tracker.trades)}"),
                symbol=t_data.get("symbol", "UNKNOWN"),
                direction=t_data.get("direction", "LONG"),
                strategy=t_data.get("strategy", "unknown"),
                entry_price=t_data.get("entry_price", 0.0),
                exit_price=t_data.get("exit_price", 0.0),
                quantity=t_data.get("quantity", 0.0),
                realized_pnl=t_data.get("realized_pnl", 0.0),
                exit_reason=t_data.get("exit_reason", "UNKNOWN"),
                opened_at=datetime.fromisoformat(t_data["opened_at"]) if t_data.get("opened_at") else datetime.now(timezone.utc),
                closed_at=datetime.fromisoformat(t_data["closed_at"]) if t_data.get("closed_at") else datetime.now(timezone.utc),
                hold_time_hours=t_data.get("hold_time_hours", t_data.get("hold_duration_hours", 0.0)),
            )

            tracker.trades.append(record)

            # Rebuild stats
            tracker.total_stats.add_trade(record.realized_pnl)
            tracker.strategy_stats[record.strategy].add_trade(record.realized_pnl)
            tracker.symbol_stats[record.symbol].add_trade(record.realized_pnl)
            tracker.exit_reason_stats[record.exit_reason].add_trade(record.realized_pnl)
            tracker.direction_stats[record.direction].add_trade(record.realized_pnl)

            # Rebuild equity curve - ИНКРЕМЕНТАЛЬНО
            cumulative_equity += record.realized_pnl
            tracker.equity_curve.append((record.closed_at, cumulative_equity))

            # Rebuild daily PnL
            day_key = record.closed_at.strftime("%Y-%m-%d")
            tracker.daily_pnl[day_key] += record.realized_pnl

        # Устанавливаем _current_equity = initial_balance + cumulative_pnl
        tracker._current_equity = initial_balance + cumulative_equity

        logger.info(
            f"MetricsTracker restored: {len(tracker.trades)} trades, "
            f"initial={initial_balance:.2f}, pnl={cumulative_equity:+.2f}, "
            f"equity={tracker._current_equity:.2f}"
        )

        return tracker
