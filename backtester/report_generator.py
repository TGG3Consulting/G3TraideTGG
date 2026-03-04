# -*- coding: utf-8 -*-
"""
ManipBackTester - Генератор отчётов.

Создаёт XLSX отчёты и выводит статистику.
Использует xlsxwriter для совместимости с Python 3.14+
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import List

import xlsxwriter

from .models import (
    BacktestResult, BacktestSummary, ParsedSignal,
    Direction, ExitReason, BinanceFees
)
from .config import BacktestConfig


class ReportGenerator:
    """Генератор XLSX отчётов бэктеста."""

    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()
        self.fees = BinanceFees(
            maker=self.config.maker_fee,
            taker=self.config.taker_fee
        )

    def generate(
        self,
        results: List[BacktestResult],
        output_path: Path = None
    ) -> BacktestSummary:
        """Сгенерировать отчёт."""
        if output_path is None:
            output_path = self.config.output_dir / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        else:
            output_path = Path(str(output_path).replace('.csv', '.xlsx'))

        self._write_xlsx(results, output_path)

        summary = self._calculate_summary(results)
        self._print_summary(summary)

        return summary

    def _write_xlsx(self, results: List[BacktestResult], output_path: Path) -> None:
        """Записать результаты в XLSX используя xlsxwriter."""
        if not results:
            return

        # Создаём workbook
        wb = xlsxwriter.Workbook(str(output_path))
        ws = wb.add_worksheet("Backtest Results")

        # Форматы
        header_format = wb.add_format({
            'bold': True,
            'font_color': 'white',
            'bg_color': '#4472C4',
            'align': 'center',
            'valign': 'vcenter',
            'border': 1
        })

        cell_format = wb.add_format({'border': 1})
        green_format = wb.add_format({'border': 1, 'bg_color': '#C6EFCE'})
        red_format = wb.add_format({'border': 1, 'bg_color': '#FFC7CE'})

        # Заголовки - ВСЕ ДАННЫЕ ДЛЯ ML
        headers = [
            # === BASIC SIGNAL INFO ===
            "№", "Signal ID", "Symbol", "Timestamp", "Direction",
            "Prob", "Conf", "R/R", "Signal Type", "Valid Hours",
            "Entry Limit", "Stop Loss", "SL %",
            "TP1", "TP1 %", "TP2", "TP2 %", "TP3", "TP3 %",
            "Breakeven", "Risk %", "Reward %",
            # === EXECUTION RESULTS ===
            "Filled", "Entry Price", "Entry Time",
            "Exit Reason", "Exit Price", "Exit Time",
            "Gross PnL", "Fees", "Funding", "Net PnL",
            "PnL %", "Net %", "Hours",
            "TP1 Hit", "TP2 Hit", "TP3 Hit", "SL Hit",
            # === ACCUMULATION SCORE (22 components) ===
            "acc_oi_growth", "acc_oi_stability", "acc_funding_cheap", "acc_funding_gradient",
            "acc_crowd_bearish", "acc_crowd_bullish", "acc_coordinated_buying", "acc_volume_accumulation",
            "acc_cross_oi_migration", "acc_cross_price_lead",
            "acc_spot_bid_pressure", "acc_spot_ask_weakness", "acc_spot_imbalance_score",
            "acc_futures_bid_pressure", "acc_futures_ask_weakness", "acc_futures_imbalance_score",
            "acc_orderbook_divergence", "acc_orderbook_total",
            "acc_wash_trading_penalty", "acc_extreme_funding_penalty", "acc_orderbook_against_penalty",
            "acc_total",
            # === FUTURES SNAPSHOT - OI ===
            "futures_oi_value", "futures_oi_value_usd",
            "futures_oi_change_1m_pct", "futures_oi_change_5m_pct", "futures_oi_change_1h_pct",
            # === FUTURES SNAPSHOT - FUNDING ===
            "futures_funding_rate", "futures_funding_rate_pct", "futures_funding_mark_price",
            # === FUTURES SNAPSHOT - LONG/SHORT RATIO ===
            "futures_long_account_pct", "futures_short_account_pct", "futures_long_short_ratio",
            # === FUTURES SNAPSHOT - PRICE CHANGES ===
            "futures_price_change_5m_pct", "futures_price_change_1h_pct",
            # === SPOT SNAPSHOT - PRICE ===
            "spot_price_bid", "spot_price_ask", "spot_price_last", "spot_price_mid", "spot_price_spread_pct",
            # === SPOT SNAPSHOT - PRICE CHANGES ===
            "spot_price_change_1m_pct", "spot_price_change_5m_pct", "spot_price_change_1h_pct",
            # === SPOT SNAPSHOT - VOLUME ===
            "spot_volume_1m", "spot_volume_5m", "spot_volume_1h", "spot_volume_avg_1h", "spot_volume_spike_ratio",
            # === SPOT SNAPSHOT - ORDERBOOK ===
            "spot_orderbook_bid_volume_20", "spot_orderbook_ask_volume_20", "spot_orderbook_imbalance",
            # === SPOT SNAPSHOT - TRADES ===
            "spot_trades_count_1m", "spot_trades_count_5m", "spot_trades_buy_ratio_5m",
            # === SIGNAL DETAILS ===
            "signal_details_book_imbalance", "signal_details_volume_ratio", "signal_details_orderbook_score",
            "signal_details_spot_bid_volume_atr", "signal_details_spot_ask_volume_atr",
            "signal_details_spot_imbalance_atr", "signal_details_spot_atr_pct",
            # === TRIGGER DETECTION ===
            "trigger_type", "trigger_severity", "trigger_score",
            # === TRIGGER DETECTION DETAILS ===
            "trigger_details_bid_volume", "trigger_details_ask_volume",
            "trigger_details_buy_ratio", "trigger_details_sell_ratio",
            "trigger_details_trades_count", "trigger_details_volume_5m", "trigger_details_current_price",
            "trigger_details_long_account_pct", "trigger_details_short_account_pct",
            # === CONFIG ===
            "config_min_accumulation_score", "config_min_probability", "config_min_risk_reward",
            "config_default_sl_pct", "config_tp1_ratio", "config_tp2_ratio", "config_tp3_ratio",
            # === TIMESTAMPS ===
            "signal_hour", "signal_minute", "signal_day_of_week",
            # === OI HISTORY (derived) ===
            "oi_history_count", "oi_history_first", "oi_history_last",
            "oi_history_min", "oi_history_max", "oi_history_avg", "oi_history_std",
            "oi_history_trend", "oi_history_range_pct",
            # === FUNDING HISTORY (derived) ===
            "funding_history_count", "funding_history_first", "funding_history_last",
            "funding_history_min", "funding_history_max", "funding_history_avg", "funding_history_std",
            "funding_history_trend",
            # === PRICE HISTORY ===
            "price_history_count", "price_history_first", "price_history_last",
            # === TRIGGER DETECTIONS ===
            "trigger_detections_count",
            # === ADDITIONAL FIELDS ===
            "entry_zone_low", "entry_zone_high",
            "scenario_bullish", "scenario_bearish",
            "evidence_text", "evidence_count",
            "logged_at", "futures_last_update", "spot_last_update",
            "oi_timestamp", "funding_time", "ls_ratio_timestamp",
        ]

        # Записываем заголовки
        for col, header in enumerate(headers):
            ws.write(0, col, header, header_format)

        # Функция для безопасного преобразования в float
        def safe_float(val, default=0.0):
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        # Индекс колонки Net %
        net_pct_col = headers.index("Net %")

        # Записываем данные
        for row_num, r in enumerate(results, 1):
            breakeven = self._calc_breakeven(r.signal)
            risk_pct = self._calc_risk_percent(r.signal)
            reward_pct = self._calc_reward_percent(r.signal)
            ml = r.signal.ml_features

            row_data = [
                # === BASIC SIGNAL INFO ===
                row_num,
                str(r.signal.signal_id),
                r.signal.symbol,
                r.signal.timestamp.strftime("%Y-%m-%d %H:%M"),
                r.signal.direction.value,
                r.signal.probability,
                str(r.signal.confidence),
                safe_float(r.signal.risk_reward),
                r.signal.signal_type,
                r.signal.max_hold_hours,
                safe_float(r.signal.entry_limit),
                safe_float(r.signal.stop_loss),
                safe_float(r.signal.stop_loss_pct),
                safe_float(r.signal.tp1.price),
                safe_float(r.signal.tp1.percent),
                safe_float(r.signal.tp2.price),
                safe_float(r.signal.tp2.percent),
                safe_float(r.signal.tp3.price),
                safe_float(r.signal.tp3.percent),
                safe_float(breakeven),
                safe_float(risk_pct),
                safe_float(reward_pct),
                # === EXECUTION RESULTS ===
                "YES" if r.entry_filled else "NO",
                safe_float(r.actual_entry_price) if r.actual_entry_price else "",
                r.actual_entry_time.strftime("%Y-%m-%d %H:%M") if r.actual_entry_time else "",
                r.exit_reason.value if r.exit_reason else "",
                safe_float(r.final_exit_price) if r.final_exit_price else "",
                r.final_exit_time.strftime("%Y-%m-%d %H:%M") if r.final_exit_time else "",
                safe_float(r.gross_pnl),
                safe_float(r.total_fees),
                safe_float(r.total_funding),
                safe_float(r.net_pnl),
                safe_float(r.pnl_percent),
                safe_float(r.net_pnl_percent),
                safe_float(r.hold_time_hours),
                "YES" if r.tp1_hit else "",
                "YES" if r.tp2_hit else "",
                "YES" if r.tp3_hit else "",
                "YES" if r.sl_hit else "",
                # === ACCUMULATION SCORE ===
                ml.acc_oi_growth,
                ml.acc_oi_stability,
                ml.acc_funding_cheap,
                ml.acc_funding_gradient,
                ml.acc_crowd_bearish,
                ml.acc_crowd_bullish,
                ml.acc_coordinated_buying,
                ml.acc_volume_accumulation,
                ml.acc_cross_oi_migration,
                ml.acc_cross_price_lead,
                ml.acc_spot_bid_pressure,
                ml.acc_spot_ask_weakness,
                ml.acc_spot_imbalance_score,
                ml.acc_futures_bid_pressure,
                ml.acc_futures_ask_weakness,
                ml.acc_futures_imbalance_score,
                ml.acc_orderbook_divergence,
                ml.acc_orderbook_total,
                ml.acc_wash_trading_penalty,
                ml.acc_extreme_funding_penalty,
                ml.acc_orderbook_against_penalty,
                ml.acc_total,
                # === FUTURES SNAPSHOT - OI ===
                ml.futures_oi_value,
                ml.futures_oi_value_usd,
                ml.futures_oi_change_1m_pct,
                ml.futures_oi_change_5m_pct,
                ml.futures_oi_change_1h_pct,
                # === FUTURES SNAPSHOT - FUNDING ===
                ml.futures_funding_rate,
                ml.futures_funding_rate_pct,
                ml.futures_funding_mark_price,
                # === FUTURES SNAPSHOT - LONG/SHORT RATIO ===
                ml.futures_long_account_pct,
                ml.futures_short_account_pct,
                ml.futures_long_short_ratio,
                # === FUTURES SNAPSHOT - PRICE CHANGES ===
                ml.futures_price_change_5m_pct,
                ml.futures_price_change_1h_pct,
                # === SPOT SNAPSHOT - PRICE ===
                ml.spot_price_bid,
                ml.spot_price_ask,
                ml.spot_price_last,
                ml.spot_price_mid,
                ml.spot_price_spread_pct,
                # === SPOT SNAPSHOT - PRICE CHANGES ===
                ml.spot_price_change_1m_pct,
                ml.spot_price_change_5m_pct,
                ml.spot_price_change_1h_pct,
                # === SPOT SNAPSHOT - VOLUME ===
                ml.spot_volume_1m,
                ml.spot_volume_5m,
                ml.spot_volume_1h,
                ml.spot_volume_avg_1h,
                ml.spot_volume_spike_ratio,
                # === SPOT SNAPSHOT - ORDERBOOK ===
                ml.spot_orderbook_bid_volume_20,
                ml.spot_orderbook_ask_volume_20,
                ml.spot_orderbook_imbalance,
                # === SPOT SNAPSHOT - TRADES ===
                ml.spot_trades_count_1m,
                ml.spot_trades_count_5m,
                ml.spot_trades_buy_ratio_5m,
                # === SIGNAL DETAILS ===
                ml.signal_details_book_imbalance,
                ml.signal_details_volume_ratio,
                ml.signal_details_orderbook_score,
                ml.signal_details_spot_bid_volume_atr,
                ml.signal_details_spot_ask_volume_atr,
                ml.signal_details_spot_imbalance_atr,
                ml.signal_details_spot_atr_pct,
                # === TRIGGER DETECTION ===
                ml.trigger_type,
                ml.trigger_severity,
                ml.trigger_score,
                # === TRIGGER DETECTION DETAILS ===
                ml.trigger_details_bid_volume,
                ml.trigger_details_ask_volume,
                ml.trigger_details_buy_ratio,
                ml.trigger_details_sell_ratio,
                ml.trigger_details_trades_count,
                ml.trigger_details_volume_5m,
                ml.trigger_details_current_price,
                ml.trigger_details_long_account_pct,
                ml.trigger_details_short_account_pct,
                # === CONFIG ===
                ml.config_min_accumulation_score,
                ml.config_min_probability,
                ml.config_min_risk_reward,
                ml.config_default_sl_pct,
                ml.config_tp1_ratio,
                ml.config_tp2_ratio,
                ml.config_tp3_ratio,
                # === TIMESTAMPS ===
                ml.signal_hour,
                ml.signal_minute,
                ml.signal_day_of_week,
                # === OI HISTORY ===
                ml.oi_history_count,
                ml.oi_history_first,
                ml.oi_history_last,
                ml.oi_history_min,
                ml.oi_history_max,
                ml.oi_history_avg,
                ml.oi_history_std,
                ml.oi_history_trend,
                ml.oi_history_range_pct,
                # === FUNDING HISTORY ===
                ml.funding_history_count,
                ml.funding_history_first,
                ml.funding_history_last,
                ml.funding_history_min,
                ml.funding_history_max,
                ml.funding_history_avg,
                ml.funding_history_std,
                ml.funding_history_trend,
                # === PRICE HISTORY ===
                ml.price_history_count,
                ml.price_history_first,
                ml.price_history_last,
                # === TRIGGER DETECTIONS ===
                ml.trigger_detections_count,
                # === ADDITIONAL FIELDS ===
                ml.entry_zone_low,
                ml.entry_zone_high,
                ml.scenario_bullish,
                ml.scenario_bearish,
                ml.evidence_text,
                ml.evidence_count,
                ml.logged_at,
                ml.futures_last_update,
                ml.spot_last_update,
                ml.oi_timestamp,
                ml.funding_time,
                ml.ls_ratio_timestamp,
            ]

            # Записываем строку
            for col, value in enumerate(row_data):
                # Выбираем формат для ячейки
                if col == net_pct_col:
                    if value and isinstance(value, (int, float)) and value > 0:
                        fmt = green_format
                    elif value and isinstance(value, (int, float)) and value < 0:
                        fmt = red_format
                    else:
                        fmt = cell_format
                else:
                    fmt = cell_format

                ws.write(row_num, col, value, fmt)

        # Установить ширину колонок
        ws.set_column(0, 0, 5)      # №
        ws.set_column(1, 1, 30)     # Signal ID (full, not truncated)
        ws.set_column(2, 2, 10)     # Symbol
        ws.set_column(3, 3, 16)     # Timestamp
        ws.set_column(4, 4, 7)      # Direction
        ws.set_column(5, len(headers)-1, 12)  # Остальные

        # Заморозить первую строку
        ws.freeze_panes(1, 0)

        # Закрываем файл
        wb.close()

        if self.config.verbose:
            print(f"\nResults saved to: {output_path}")

    def _calc_breakeven(self, signal: ParsedSignal) -> Decimal:
        """Рассчитать цену breakeven."""
        try:
            entry = Decimal(str(signal.entry_limit))
            if entry <= 0:
                return Decimal("0")

            total_fee_rate = self.fees.maker + self.fees.maker

            if signal.direction == Direction.LONG:
                breakeven = entry * (1 + total_fee_rate)
            else:
                breakeven = entry * (1 - total_fee_rate)

            return breakeven.quantize(Decimal("0.00000001"))
        except:
            return Decimal("0")

    def _calc_risk_percent(self, signal: ParsedSignal) -> Decimal:
        """Рассчитать риск в процентах."""
        try:
            entry = Decimal(str(signal.entry_limit))
            sl = Decimal(str(signal.stop_loss))

            if entry <= 0:
                return Decimal("0")

            if signal.direction == Direction.LONG:
                risk = (entry - sl) / entry * 100
            else:
                risk = (sl - entry) / entry * 100

            return abs(risk)
        except:
            return Decimal("0")

    def _calc_reward_percent(self, signal: ParsedSignal) -> Decimal:
        """Рассчитать потенциальный reward."""
        try:
            entry = Decimal(str(signal.entry_limit))
            if entry <= 0:
                return Decimal("0")

            total_reward = Decimal("0")

            for tp in [signal.tp1, signal.tp2, signal.tp3]:
                try:
                    tp_price = Decimal(str(tp.price))
                    tp_portion = Decimal(str(tp.portion))
                    if tp_price > 0 and tp_portion > 0:
                        if signal.direction == Direction.LONG:
                            tp_reward = (tp_price - entry) / entry * 100
                        else:
                            tp_reward = (entry - tp_price) / entry * 100

                        weighted = tp_reward * tp_portion / 100
                        total_reward += weighted
                except:
                    continue

            return total_reward
        except:
            return Decimal("0")

    def _calculate_summary(self, results: List[BacktestResult]) -> BacktestSummary:
        """Рассчитать сводную статистику."""
        summary = BacktestSummary()
        summary.backtest_start = datetime.now()

        if not results:
            return summary

        summary.total_signals = len(results)
        summary.filled_signals = sum(1 for r in results if r.entry_filled)
        summary.not_filled_signals = summary.total_signals - summary.filled_signals

        filled = [r for r in results if r.entry_filled]

        if not filled:
            return summary

        summary.wins = sum(1 for r in filled if r.net_pnl > 0)
        summary.losses = sum(1 for r in filled if r.net_pnl < 0)
        summary.breakeven = sum(1 for r in filled if r.net_pnl == 0)

        summary.win_rate = summary.wins / len(filled) * 100 if filled else 0

        summary.total_gross_pnl = sum(r.gross_pnl for r in filled)
        summary.total_fees = sum(r.total_fees for r in filled)
        summary.total_funding = sum(r.total_funding for r in filled)
        summary.total_net_pnl = sum(r.net_pnl for r in filled)

        wins = [r for r in filled if r.net_pnl > 0]
        losses = [r for r in filled if r.net_pnl < 0]

        if wins:
            summary.avg_win_pct = sum(r.net_pnl_percent for r in wins) / len(wins)
        if losses:
            summary.avg_loss_pct = sum(r.net_pnl_percent for r in losses) / len(losses)

        summary.avg_hold_hours = sum(r.hold_time_hours for r in filled) / len(filled)

        for r in filled:
            reason = r.exit_reason.value if r.exit_reason else "UNKNOWN"
            summary.exits_by_reason[reason] = summary.exits_by_reason.get(reason, 0) + 1

        summary.tp1_hits = sum(1 for r in filled if r.tp1_hit)
        summary.tp2_hits = sum(1 for r in filled if r.tp2_hit)
        summary.tp3_hits = sum(1 for r in filled if r.tp3_hit)
        summary.sl_hits = sum(1 for r in filled if r.sl_hit)
        summary.timeout_exits = sum(1 for r in filled if r.exit_reason == ExitReason.TIMEOUT)

        if filled:
            best = max(filled, key=lambda r: r.net_pnl_percent)
            worst = min(filled, key=lambda r: r.net_pnl_percent)

            summary.best_trade_pnl_pct = best.net_pnl_percent
            summary.best_trade_symbol = best.signal.symbol
            summary.worst_trade_pnl_pct = worst.net_pnl_percent
            summary.worst_trade_symbol = worst.signal.symbol

        if results:
            first = min(r.signal.timestamp for r in results)
            last = max(r.signal.timestamp for r in results)
            summary.signals_time_range = f"{first.strftime('%Y-%m-%d')} to {last.strftime('%Y-%m-%d')}"

        summary.backtest_end = datetime.now()

        return summary

    def _print_summary(self, summary: BacktestSummary) -> None:
        """Вывести summary."""
        print("\n" + "=" * 60)
        print("                 BACKTEST SUMMARY")
        print("=" * 60)

        print(f"\n[SIGNALS]")
        print(f"   Total signals:      {summary.total_signals}")
        print(f"   Filled (entered):   {summary.filled_signals}")
        print(f"   Not filled:         {summary.not_filled_signals}")
        print(f"   Time range:         {summary.signals_time_range}")

        print(f"\n[PERFORMANCE]")
        print(f"   Wins:               {summary.wins}")
        print(f"   Losses:             {summary.losses}")
        print(f"   Breakeven:          {summary.breakeven}")
        print(f"   Win Rate:           {summary.win_rate:.1f}%")

        print(f"\n[PnL] (normalized to 1.0 position size)")
        print(f"   Gross PnL:          {summary.total_gross_pnl:+.6f}")
        print(f"   Total Fees:         -{summary.total_fees:.6f}")
        print(f"   Total Funding:      -{summary.total_funding:.6f}")
        print(f"   -----------------------------")
        print(f"   Net PnL:            {summary.total_net_pnl:+.6f}")

        print(f"\n[AVERAGES]")
        print(f"   Avg Win:            {summary.avg_win_pct:+.4f}%")
        print(f"   Avg Loss:           {summary.avg_loss_pct:+.4f}%")
        print(f"   Avg Hold Time:      {summary.avg_hold_hours:.1f} hours")

        print(f"\n[EXIT REASONS]")
        for reason, count in sorted(summary.exits_by_reason.items()):
            print(f"   {reason}:".ljust(20) + f"{count}")

        print(f"\n[TP/SL HITS]")
        print(f"   TP1 hits:           {summary.tp1_hits}")
        print(f"   TP2 hits:           {summary.tp2_hits}")
        print(f"   TP3 hits:           {summary.tp3_hits}")
        print(f"   SL hits:            {summary.sl_hits}")
        print(f"   Timeouts:           {summary.timeout_exits}")

        print(f"\n[BEST/WORST]")
        print(f"   Best trade:         {summary.best_trade_symbol} ({summary.best_trade_pnl_pct:+.4f}%)")
        print(f"   Worst trade:        {summary.worst_trade_symbol} ({summary.worst_trade_pnl_pct:+.4f}%)")

        print("\n" + "=" * 60)
