# -*- coding: utf-8 -*-
"""
Telegram Callback Daemon - Обработчик нажатий кнопки "Подробности".

Запускается как 24/7 сервис и слушает callback от Telegram.
При нажатии кнопки отправляет полные данные сигнала в ЛС пользователю.

Использование:
    python telegram_callback_daemon.py
    python telegram_callback_daemon.py --config config.json

Для запуска в фоне:
    nohup python telegram_callback_daemon.py &
    или через screen/tmux
"""

import sys
import io
import json
import logging
import argparse
from typing import Dict, Any, Optional

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from telegram_sender import load_signal_cache, format_dm_details


# =============================================================================
# НАСТРОЙКА
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)


def load_config(config_path: str) -> Dict[str, Any]:
    """Загрузка config.json."""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# ОБРАБОТЧИК CALLBACK
# =============================================================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик нажатия кнопки 'Подробности'.

    При нажатии:
    1. Извлекает signal_id из callback_data
    2. Загружает данные из signal_cache.json
    3. Форматирует и отправляет в ЛС пользователю
    """
    query = update.callback_query

    # Отвечаем на callback (убираем "часики")
    await query.answer()

    # Извлекаем signal_id
    callback_data = query.data
    if not callback_data.startswith("details_"):
        log.warning(f"Unknown callback: {callback_data}")
        return

    signal_id = callback_data.replace("details_", "")
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name

    log.info(f"Callback: signal={signal_id}, user={user_id} (@{username})")

    # Загружаем данные из кэша
    signal_data = load_signal_cache(signal_id)

    if not signal_data:
        log.warning(f"Signal not found in cache: {signal_id}")
        await query.answer("Данные не найдены", show_alert=True)
        return

    # Форматируем полные данные
    text = format_dm_details(signal_data)

    # Отправляем в ЛС пользователю
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
        )
        log.info(f"Sent to DM: user={user_id}, signal={signal_id}")

    except Exception as e:
        error_msg = str(e)
        log.error(f"Failed to send DM: {error_msg}")

        if "bot was blocked" in error_msg.lower():
            await query.answer("Вы заблокировали бота. Разблокируйте и нажмите /start", show_alert=True)
        elif "chat not found" in error_msg.lower():
            await query.answer("Напишите боту /start чтобы получать сообщения", show_alert=True)
        else:
            await query.answer(f"Ошибка: {error_msg[:100]}", show_alert=True)


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Telegram Callback Daemon")
    parser.add_argument("--config", type=str, default="config.json", help="Path to config.json")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("TELEGRAM CALLBACK DAEMON")
    log.info("=" * 60)

    # Загружаем конфиг
    config = load_config(args.config)
    bot_token = config["telegram"]["bot_token"]

    log.info(f"Config: {args.config}")
    log.info(f"Bot token: {bot_token[:10]}...{bot_token[-5:]}")

    # Создаём приложение
    app = Application.builder().token(bot_token).build()

    # Добавляем обработчик callback
    app.add_handler(CallbackQueryHandler(handle_callback, pattern="^details_"))

    log.info("Starting polling...")
    log.info("Press Ctrl+C to stop")
    log.info("=" * 60)

    # Запускаем polling (только callback_query)
    app.run_polling(allowed_updates=["callback_query"])


if __name__ == "__main__":
    main()
