# -*- coding: utf-8 -*-
"""
BinanceFriend - Manipulation Detection Screener
Entry point

Использование:
    python -m screener.main
    python -m screener.main --help
"""

import asyncio
import argparse
import json
import signal
import sys
from pathlib import Path

# Добавить src в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog

from .screener import ManipulationScreener
from .alert_dispatcher import AlertConfig
from .telegram_notifier import TelegramConfig


def load_telegram_config(config_path: Path) -> dict:
    """Загрузить конфиг Telegram из файла."""
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load {config_path}: {e}")
    return {}


def setup_logging(level: str = "INFO", json_format: bool = False):
    """Настроить structlog."""
    processors = [
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_format:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def parse_args():
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="BinanceFriend - Manipulation Detection Screener",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m screener.main
  python -m screener.main --max-symbols 50 --rescan-interval 120
  python -m screener.main --api-url https://api.binance.com/alerts --api-key YOUR_KEY
        """
    )

    parser.add_argument(
        "--max-symbols",
        type=int,
        default=100,
        help="Maximum number of symbols to monitor (default: 100)"
    )

    parser.add_argument(
        "--rescan-interval",
        type=int,
        default=300,
        help="Interval between universe scans in seconds (default: 300)"
    )

    parser.add_argument(
        "--api-url",
        type=str,
        default="",
        help="Binance API URL for sending alerts"
    )

    parser.add_argument(
        "--api-key",
        type=str,
        default="",
        help="API key for Binance alerts API"
    )

    parser.add_argument(
        "--api-secret",
        type=str,
        default="",
        help="API secret for Binance alerts API"
    )

    parser.add_argument(
        "--log-file",
        type=str,
        default="logs/alerts.jsonl",
        help="Path to local alerts log file (default: logs/alerts.jsonl)"
    )

    # Telegram
    parser.add_argument(
        "--telegram-token",
        type=str,
        default="",
        help="Telegram bot token"
    )

    parser.add_argument(
        "--telegram-chat",
        type=str,
        default="",
        help="Telegram chat ID"
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)"
    )

    parser.add_argument(
        "--json-logs",
        action="store_true",
        help="Output logs in JSON format"
    )

    return parser.parse_args()


async def main():
    """Main entry point."""
    args = parse_args()

    # Настроить логирование
    setup_logging(level=args.log_level, json_format=args.json_logs)

    logger = structlog.get_logger(__name__)

    # Конфигурация алертов
    alert_config = AlertConfig(
        binance_api_url=args.api_url,
        api_key=args.api_key,
        api_secret=args.api_secret,
        log_to_file=True,
        log_file_path=args.log_file,
    )

    # Конфигурация Telegram (из аргументов или из файла)
    telegram_token = args.telegram_token
    telegram_chat = args.telegram_chat

    # Если не указано в аргументах - читаем из файла
    signals_bot_token = ""
    signals_chat_id = ""
    if not telegram_token or not telegram_chat:
        project_root = Path(__file__).parent.parent.parent
        telegram_file = project_root / "config" / "telegram.json"
        file_config = load_telegram_config(telegram_file)

        if not telegram_token:
            telegram_token = file_config.get("bot_token", "")
        if not telegram_chat:
            telegram_chat = file_config.get("chat_id", "")
        signals_bot_token = file_config.get("signals_bot_token", "")
        signals_chat_id = file_config.get("signals_chat_id", "")

    telegram_config = TelegramConfig(
        bot_token=telegram_token,
        chat_id=telegram_chat,
        signals_bot_token=signals_bot_token,
        signals_chat_id=signals_chat_id,
    )

    if telegram_config.is_configured:
        logger.info("Telegram notifications configured", chat_id=telegram_chat)
        if signals_bot_token and signals_chat_id:
            logger.info("Trade signals: separate bot + chat", signals_chat_id=signals_chat_id)
        elif signals_chat_id:
            logger.info("Trade signals: same bot, separate chat", signals_chat_id=signals_chat_id)
        else:
            logger.info("Trade signals will go to main chat")
    else:
        logger.info("Telegram notifications disabled (no token/chat_id provided)")

    # Создать скринер
    screener = ManipulationScreener(
        alert_config=alert_config,
        telegram_config=telegram_config,
        rescan_interval=args.rescan_interval,
        max_symbols=args.max_symbols,
    )

    # Обработка сигналов для graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received")
        shutdown_event.set()

    # Регистрируем обработчики сигналов
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            pass

    # Запуск
    try:
        # Запускаем скринер в фоне
        screener_task = asyncio.create_task(screener.start())

        # Для Windows: проверяем Ctrl+C через KeyboardInterrupt
        try:
            # Ждём сигнала shutdown или завершения
            await shutdown_event.wait()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")

        # Останавливаем скринер
        await screener.stop()

        # Отменяем задачу если ещё работает
        if not screener_task.done():
            screener_task.cancel()
            try:
                await screener_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error("Fatal error", error=str(e))
        raise


def run():
    """Wrapper для запуска из командной строки."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run()
