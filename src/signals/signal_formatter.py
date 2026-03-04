# -*- coding: utf-8 -*-
"""
Форматирование торговых сигналов для Telegram.
"""

import html
from typing import List
from .models import TradeSignal, SignalDirection, SignalConfidence


class SignalFormatter:
    """Форматирует торговые сигналы для отправки в Telegram."""

    @staticmethod
    def format_signal(signal: TradeSignal) -> str:
        """
        Форматировать сигнал для Telegram.

        Returns:
            Отформатированное сообщение с HTML разметкой
        """
        # Эмодзи направления
        direction_emoji = "🟢" if signal.direction == SignalDirection.LONG else "🔴"
        direction_text = "LONG" if signal.direction == SignalDirection.LONG else "SHORT"

        # Эмодзи уверенности
        confidence_emoji = SignalFormatter._confidence_emoji(signal.confidence)

        # Header
        lines = [
            f"🎯 <b>ТОРГОВЫЙ СИГНАЛ: {signal.symbol}</b>",
            f"📊 НАПРАВЛЕНИЕ: {direction_emoji} <b>{direction_text}</b>",
            f"💰 ВЕРОЯТНОСТЬ: <b>{signal.probability}%</b>",
            f"⚡ УВЕРЕННОСТЬ: {confidence_emoji} {signal.confidence.value}",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]

        # Entry
        lines.append("📍 <b>ВХОД:</b>")
        lines.append("")
        lines.append(f"Зона входа: <code>${signal.entry_zone_low} - ${signal.entry_zone_high}</code>")
        lines.append(f"Лимитный ордер: <code>${signal.entry_limit}</code>")
        lines.append("")

        # Stop Loss
        lines.append(f"🛑 <b>СТОП-ЛОСС:</b> <code>${signal.stop_loss}</code> (-{signal.stop_loss_pct:.1f}%)")

        # Take Profits
        lines.append("🎯 <b>ТЕЙК-ПРОФИТЫ:</b>")
        lines.append("")
        for tp in signal.take_profits:
            lines.append(
                f"{tp.label}: <code>${tp.price}</code> (+{tp.percent:.1f}%) — забрать {tp.portion}%"
            )
        lines.append("")

        # Timing
        lines.append(f"⏱ ЖДАТЬ: до {signal.valid_hours} часов")
        lines.append(f"📈 R:R = <b>{signal.risk_reward_ratio}</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Orderbook data (SPOT + FUTURES)
        lines.append("📊 <b>СТАКАН:</b>")
        lines.append("")

        # SPOT orderbook
        spot_bid = signal.details.get("spot_bid_volume_atr")
        spot_ask = signal.details.get("spot_ask_volume_atr")
        spot_imb = signal.details.get("spot_imbalance_atr")
        spot_atr = signal.details.get("spot_atr_pct")

        if spot_bid is not None and spot_ask is not None:
            imb_pct = abs(spot_imb or 0) * 100
            imb_side = "BUY" if (spot_imb or 0) > 0 else "SELL"
            lines.append(f"🔵 SPOT (ATR ±{spot_atr:.1f}%):")
            lines.append(f"   Bid: ${spot_bid:,.0f} | Ask: ${spot_ask:,.0f}")
            lines.append(f"   Imbalance: {imb_pct:.0f}% → {imb_side}")
        else:
            lines.append("🔵 SPOT: нет данных")

        # FUTURES orderbook
        fut_bid = signal.details.get("futures_bid_volume_atr")
        fut_ask = signal.details.get("futures_ask_volume_atr")
        fut_imb = signal.details.get("futures_imbalance_atr")
        fut_atr = signal.details.get("futures_atr_pct")

        if fut_bid is not None and fut_ask is not None:
            imb_pct = abs(fut_imb or 0) * 100
            imb_side = "BUY" if (fut_imb or 0) > 0 else "SELL"
            lines.append(f"🟠 FUTURES (ATR ±{fut_atr:.1f}%):")
            lines.append(f"   Bid: ${fut_bid:,.0f} | Ask: ${fut_ask:,.0f}")
            lines.append(f"   Imbalance: {imb_pct:.0f}% → {imb_side}")
        else:
            lines.append("🟠 FUTURES: нет данных")

        # Accumulation/Orderbook scores (если есть)
        acc_score = signal.details.get("accumulation_score")
        ob_score = signal.details.get("orderbook_score")
        if acc_score is not None or ob_score is not None:
            lines.append("")
            lines.append("📊 <b>СКОРИНГ:</b>")
            if acc_score is not None:
                lines.append(f"   Accumulation: {acc_score}/100")
            if ob_score is not None:
                lines.append(f"   Orderbook: {ob_score}/45")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Evidence
        lines.append(f"📈 <b>ПОЧЕМУ {direction_text}:</b>")
        lines.append("<i>Данные BinanceFriend:</i>")
        lines.append("")
        for evidence in signal.evidence[:6]:  # Max 6 пунктов
            # Экранируем HTML символы чтобы < и > не ломали Telegram
            safe_evidence = html.escape(str(evidence))
            lines.append(f"• {safe_evidence}")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Scenarios
        lines.append("⚠️ <b>СЦЕНАРИИ:</b>")
        scenario_emojis = {"pump_started": "📗", "dump_started": "📗", "sideways": "📙", "invalidation": "📕"}
        for key, scenario in signal.scenarios.items():
            emoji = scenario_emojis.get(key, "📘")
            # Экранируем HTML
            safe_scenario = html.escape(str(scenario))
            # Split by ": → " to format
            if ": → " in safe_scenario:
                condition, action = safe_scenario.split(": → ", 1)
                lines.append(f"{emoji} {condition}:")
                lines.append(f"   → {action}")
            else:
                lines.append(f"{emoji} {safe_scenario}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Links
        lines.append("🔗 <b>ССЫЛКИ:</b>")
        link_parts = []
        if "binance_futures" in signal.links:
            link_parts.append(f"<a href=\"{signal.links['binance_futures']}\">Binance</a>")
        if "tradingview" in signal.links:
            link_parts.append(f"<a href=\"{signal.links['tradingview']}\">TradingView</a>")
        if "coinglass" in signal.links:
            link_parts.append(f"<a href=\"{signal.links['coinglass']}\">CoinGlass</a>")

        lines.append(" | ".join(link_parts))

        # Footer
        lines.append("")
        lines.append(f"<i>Сигнал #{signal.signal_id} | {signal.timestamp.strftime('%H:%M:%S')}</i>")

        return "\n".join(lines)

    @staticmethod
    def format_signal_compact(signal: TradeSignal) -> str:
        """Компактный формат сигнала (для групп)."""
        direction_emoji = "🟢" if signal.direction == SignalDirection.LONG else "🔴"
        direction_text = "LONG" if signal.direction == SignalDirection.LONG else "SHORT"

        lines = [
            f"🎯 <b>{signal.symbol}</b> {direction_emoji} {direction_text}",
            f"Вход: <code>${signal.entry_limit}</code>",
            f"SL: <code>${signal.stop_loss}</code> | TP1: <code>${signal.take_profits[0].price if signal.take_profits else 'N/A'}</code>",  # FIX-14: защита от IndexError
            f"Вероятность: {signal.probability}% | R:R {signal.risk_reward_ratio}",
        ]

        if signal.evidence:
            lines.append(f"<i>{signal.evidence[0]}</i>")

        return "\n".join(lines)

    @staticmethod
    def _confidence_emoji(confidence: SignalConfidence) -> str:
        """Эмодзи для уровня уверенности."""
        mapping = {
            SignalConfidence.LOW: "⚪",
            SignalConfidence.MEDIUM: "🟡",
            SignalConfidence.HIGH: "🟢",
            SignalConfidence.VERY_HIGH: "💚",
        }
        return mapping.get(confidence, "⚪")

    @staticmethod
    def format_signal_update(
        signal: TradeSignal,
        update_type: str,
        new_value: str = None
    ) -> str:
        """Форматировать обновление сигнала."""
        direction_emoji = "🟢" if signal.direction == SignalDirection.LONG else "🔴"

        if update_type == "tp_hit":
            return (
                f"✅ <b>TP HIT!</b> {signal.symbol} {direction_emoji}\n"
                f"Достигнут {new_value}\n"
                f"Сигнал #{signal.signal_id}"
            )
        elif update_type == "sl_hit":
            return (
                f"❌ <b>STOP LOSS</b> {signal.symbol} {direction_emoji}\n"
                f"Сработал стоп на {signal.stop_loss}\n"
                f"Сигнал #{signal.signal_id}"
            )
        elif update_type == "sl_moved":
            return (
                f"🔄 <b>SL MOVED</b> {signal.symbol} {direction_emoji}\n"
                f"Стоп передвинут на {new_value}\n"
                f"Сигнал #{signal.signal_id}"
            )
        elif update_type == "expired":
            return (
                f"⏰ <b>EXPIRED</b> {signal.symbol}\n"
                f"Сигнал #{signal.signal_id} истёк"
            )
        else:
            return f"ℹ️ Update: {signal.symbol} - {update_type}"
