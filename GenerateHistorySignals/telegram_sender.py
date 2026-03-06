# -*- coding: utf-8 -*-
"""
Telegram Sender - Форматирование и отправка алертов в Telegram.

Отвечает за:
- Форматирование сообщений для группы (русский язык)
- Форматирование детальных данных для ЛС
- Отправку сообщений через Telegram Bot API
- Сохранение данных для callback в signal_cache.json
"""

import html
import json
import os
from datetime import datetime
from typing import Dict, Any, Tuple, Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from strategies import Signal


# =============================================================================
# ФАЙЛЫ
# =============================================================================

SIGNAL_CACHE_FILE = "signal_cache.json"
SENT_SIGNALS_FILE = "sent_signals.log"


# =============================================================================
# ФОРМАТИРОВАНИЕ
# =============================================================================

def format_group_alert(
    signal: Signal,
    strategy_name: str,
    coin_regime: str,
    regime_action: str,
    coin_volatility: float,
    signal_id: str,
    ml_data: Optional[Dict[str, Any]] = None,
) -> Tuple[str, InlineKeyboardMarkup]:
    """
    Форматирует сообщение для группы (русский язык).

    Args:
        signal: Сигнал
        strategy_name: Название стратегии
        coin_regime: Режим монеты
        regime_action: Действие матрицы (OFF/DYN/FULL)
        coin_volatility: Волатильность
        signal_id: ID сигнала для callback
        ml_data: Данные ML (optional)

    Returns:
        (text, keyboard) - текст сообщения и клавиатура с кнопкой
    """
    # Эмодзи направления
    direction_emoji = "🟢" if signal.direction == "LONG" else "🔴"

    # Расчёт TP/SL %
    if signal.direction == "LONG":
        tp_pct = (signal.take_profit - signal.entry) / signal.entry * 100
        sl_pct = (signal.entry - signal.stop_loss) / signal.entry * 100
    else:
        tp_pct = (signal.entry - signal.take_profit) / signal.entry * 100
        sl_pct = (signal.stop_loss - signal.entry) / signal.entry * 100

    # Форматирование цен
    def fmt_price(price: float) -> str:
        if price >= 1000:
            return f"${price:,.2f}"
        elif price >= 1:
            return f"${price:.4f}"
        else:
            return f"${price:.6f}"

    # ML строка (жирная)
    if ml_data is None or not ml_data.get("enabled", False):
        ml_line = "<b>🤖 ML: режим не включен при запуске</b>"
    elif ml_data.get("recommends", False):
        ml_line = "<b>✅ ML рекомендует</b>"
    else:
        ml_line = "<b>❌ ML не рекомендует</b>"

    text = f"""{direction_emoji} {signal.direction} {signal.symbol}
━━━━━━━━━━━━━━━━━━━━━━━━━━
Стратегия: {strategy_name}
Режим монеты: {coin_regime}
━━━━━━━━━━━━━━━━━━━━━━━━━━
Вход:  {fmt_price(signal.entry)}
TP:    {fmt_price(signal.take_profit)} (+{tp_pct:.1f}%)
SL:    {fmt_price(signal.stop_loss)} (-{sl_pct:.1f}%)

{ml_line}"""

    # Кнопка "Подробности"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Подробности", callback_data=f"details_{signal_id}")]
    ])

    return text, keyboard


def _format_regime_action(action: str) -> str:
    """Форматирует действие матрицы с пояснением суммы."""
    if action == "DYN":
        return "DYN ($1 dynamic)"
    elif action == "FULL":
        return "FULL ($100)"
    elif action == "OFF":
        return "OFF (пропуск)"
    return action


def format_dm_details(signal_data: Dict[str, Any]) -> str:
    """
    Форматирует полные данные для ЛС (русский язык).

    Args:
        signal_data: Полные данные сигнала из signal_cache.json

    Returns:
        Текст сообщения для ЛС
    """
    # Форматирование цен
    def fmt_price(price: float) -> str:
        if price >= 1000:
            return f"${price:,.2f}"
        elif price >= 1:
            return f"${price:.4f}"
        else:
            return f"${price:.6f}"

    def fmt_big_number(num: float) -> str:
        if num >= 1_000_000_000:
            return f"${num/1_000_000_000:.2f}B"
        elif num >= 1_000_000:
            return f"${num/1_000_000:.2f}M"
        else:
            return f"${num:,.0f}"

    # Извлекаем данные
    md = signal_data.get("market_data", {})
    candle = signal_data.get("candle", {})
    indicators = signal_data.get("indicators", {})

    # Рассчитываем L/S Ratio
    long_pct = md.get("long_pct", 50)
    short_pct = md.get("short_pct", 50)
    ls_ratio = long_pct / short_pct if short_pct > 0 else 1.0

    # Taker Buy %
    taker_buy_pct = candle.get("taker_buy_pct", 50)

    # ADX интерпретация
    # WORKAROUND: ADX в коде завышен в ~14x из-за бага в wilder_smooth (strategies/base.py:236)
    # Делим на 14 для корректного отображения. ML не трогаем — там баг компенсируется.
    # TODO: Полное исправление требует re-backtest + re-train ML. Погрумить перед кодингом!
    adx_raw = indicators.get("adx", 0)
    adx = adx_raw / 14 if adx_raw > 0 else 0  # Нормализуем для отображения
    if adx >= 40:
        adx_desc = "сильный тренд"
    elif adx >= 25:
        adx_desc = "умеренный тренд"
    else:
        adx_desc = "слабый тренд"

    text = f"""📊 ПОЛНЫЕ ДАННЫЕ СИГНАЛА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ОСНОВНОЕ:
• Монета: {signal_data.get("symbol", "N/A")}
• Направление: {signal_data.get("direction", "N/A")}
• Дата сигнала: {signal_data.get("date", "N/A")}
• Стратегия: {signal_data.get("strategy", "N/A")}

УРОВНИ:
• Вход: {fmt_price(signal_data.get("entry", 0))}
• Take Profit: {fmt_price(signal_data.get("tp", 0))} (+{signal_data.get("tp_pct", 0):.1f}%)
• Stop Loss: {fmt_price(signal_data.get("sl", 0))} (-{signal_data.get("sl_pct", 0):.1f}%)
• R:R Ratio: {signal_data.get("rr_ratio", 0):.1f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЕЖИМ МОНЕТЫ:
• Coin Regime: {signal_data.get("coin_regime", "N/A")}
• Изменение за 14д: {signal_data.get("coin_regime_change_pct", 0):+.1f}%
• Волатильность (ATR%): {signal_data.get("coin_volatility", 0):.1f}%
• Действие матрицы: {_format_regime_action(signal_data.get("regime_action", "N/A"))}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
РЫНОЧНЫЕ ДАННЫЕ:
• Long %: {long_pct:.1f}%
• Short %: {short_pct:.1f}%
• L/S Ratio: {ls_ratio:.2f}
• Funding Rate: {md.get("funding_rate", 0)*100:+.4f}%
• Open Interest: {fmt_big_number(md.get("open_interest", 0))}
• Объём 24ч: {fmt_big_number(md.get("volume_24h", 0))}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
СВЕЧА (предыдущий день):
• Open: {fmt_price(candle.get("open", 0))}
• High: {fmt_price(candle.get("high", 0))}
• Low: {fmt_price(candle.get("low", 0))}
• Close: {fmt_price(candle.get("close", 0))}
• Volume: {candle.get("volume", 0):,.0f}
• Quote Volume: {fmt_big_number(candle.get("quote_volume", 0))}
• Trades: {candle.get("trades_count", 0):,}
• Taker Buy %: {taker_buy_pct:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ИНДИКАТОРЫ:
• ADX: {adx:.1f} ({adx_desc})
• ATR: {fmt_price(indicators.get("atr", 0))}
• ATR %: {indicators.get("atr_pct", 0):.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ПРИЧИНА СИГНАЛА:
{html.escape(str(signal_data.get("reason", "N/A")))}"""

    # ML блоки
    ml = signal_data.get("ml", {})
    if not ml.get("enabled", False):
        text += """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>🤖 ML АНАЛИЗ:</b>
• Режим ML не включен при запуске"""
    else:
        recommends = ml.get("recommends", False)
        confidence = ml.get("confidence", 0) * 100
        filter_score = ml.get("filter_score", 0) * 100
        reason = html.escape(str(ml.get("reason", "N/A")))

        # Direction to readable name
        pred_dir = ml.get("predicted_direction", 0)
        if pred_dir == 1:
            dir_name = "LONG"
        elif pred_dir == -1:
            dir_name = "SHORT"
        else:
            dir_name = "SKIP"

        rec_text = "<b>✅ РЕКОМЕНДУЕТ</b>" if recommends else "<b>❌ НЕ РЕКОМЕНДУЕТ</b>"

        text += f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>🤖 ML АНАЛИЗ:</b>
• Рекомендация: {rec_text}
• Причина: {reason}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
<b>🔬 ML ПОДРОБНО (6 моделей):</b>
• filter (прибыльный или нет) — {filter_score:.1f}%
• confidence (уровень доверия) — {confidence:.1f}%
• direction (направление) — {dir_name}
• sl (предсказанный стоп-лосс) — {ml.get("predicted_sl", 0):.1f}%
• tp (предсказанный тейк-профит) — {ml.get("predicted_tp", 0):.1f}%
• lifetime (сколько дней в сделке) — {ml.get("predicted_lifetime", 0):.1f} дней"""

    return text


# =============================================================================
# КЭШИРОВАНИЕ
# =============================================================================

def save_signal_cache(signal_id: str, signal_data: Dict[str, Any]) -> None:
    """
    Сохраняет данные сигнала в signal_cache.json (бессрочно).

    Args:
        signal_id: ID сигнала
        signal_data: Полные данные для callback
    """
    cache = {}

    # Загружаем существующий кэш
    if os.path.exists(SIGNAL_CACHE_FILE):
        try:
            with open(SIGNAL_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except:
            cache = {}

    # Добавляем новый сигнал
    cache[signal_id] = signal_data

    # Сохраняем
    with open(SIGNAL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False, default=str)


def load_signal_cache(signal_id: str) -> Optional[Dict[str, Any]]:
    """
    Загружает данные сигнала из signal_cache.json.

    Args:
        signal_id: ID сигнала

    Returns:
        Данные сигнала или None
    """
    if not os.path.exists(SIGNAL_CACHE_FILE):
        return None

    try:
        with open(SIGNAL_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache.get(signal_id)
    except:
        return None


# =============================================================================
# ЗАЩИТА ОТ ДУБЛИКАТОВ
# =============================================================================

def load_sent_signals() -> set:
    """
    Загружает список отправленных сигналов из sent_signals.log.

    Returns:
        Set с ID отправленных сигналов
    """
    if not os.path.exists(SENT_SIGNALS_FILE):
        return set()

    try:
        with open(SENT_SIGNALS_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    except:
        return set()


def save_sent_signal(signal_id: str) -> None:
    """
    Добавляет ID сигнала в sent_signals.log.

    Args:
        signal_id: ID отправленного сигнала
    """
    with open(SENT_SIGNALS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{signal_id}\n")


# =============================================================================
# ОТПРАВКА
# =============================================================================

async def send_alert(
    bot: Bot,
    chat_id: str,
    signal: Signal,
    signal_data: Dict[str, Any],
    strategy_name: str,
) -> bool:
    """
    Отправляет алерт в группу и сохраняет данные для callback.

    Args:
        bot: Telegram Bot
        chat_id: ID чата для отправки
        signal: Сигнал
        signal_data: Полные данные для callback
        strategy_name: Название стратегии

    Returns:
        True если успешно, False если ошибка
    """
    signal_id = signal_data.get("signal_id", "")

    # Форматируем сообщение
    text, keyboard = format_group_alert(
        signal=signal,
        strategy_name=strategy_name,
        coin_regime=signal_data.get("coin_regime", "UNKNOWN"),
        regime_action=signal_data.get("regime_action", "FULL"),
        coin_volatility=signal_data.get("coin_volatility", 0),
        signal_id=signal_id,
        ml_data=signal_data.get("ml"),
    )

    try:
        # Отправляем в группу
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )

        # Сохраняем данные для callback
        save_signal_cache(signal_id, signal_data)

        return True

    except Exception as e:
        print(f"[ERROR] Ошибка отправки: {e}")
        return False
