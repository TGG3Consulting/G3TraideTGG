# -*- coding: utf-8 -*-
"""
TradeAppTG - Главный лаунчер торговой системы.

Запускает:
1. TradeBot API Server - принимает сигналы и торгует
2. telegram_runner.py - генерирует сигналы

Использование:
    py -3.12 trade_app_tg.py telegram_runner(--symbols BTCUSDT,ETHUSDT --coin-regime --strategies ls_fade,momentum)

    py -3.12 trade_app_tg.py telegram_runner(--top 20 --coin-regime --vol-filter-low --continuous --interval 3600)

Формат:
    trade_app_tg.py telegram_runner(<параметры telegram_runner.py>)

Параметры telegram_runner.py автоматически дополняются:
    --tradebot-url http://127.0.0.1:8080  (для отправки сигналов в TradeBot)
"""

import os
import sys
import re
import asyncio
import subprocess
import signal
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

# Добавляем путь к tradebot
TRADEBOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TRADEBOT_DIR)

# Добавляем путь к GenerateHistorySignals
SIGNALS_DIR = os.path.join(os.path.dirname(TRADEBOT_DIR), "GenerateHistorySignals")
sys.path.insert(0, SIGNALS_DIR)


# =============================================================================
# CONFIGURATION
# =============================================================================

DEFAULT_TRADEBOT_HOST = "127.0.0.1"
DEFAULT_TRADEBOT_PORT = 8080
DEFAULT_PYTHON = "py -3.12"  # Windows

# Параметры telegram_runner.py которые влияют на торговлю
TRADING_PARAMS = {
    "sl": "Stop Loss % - уровень SL ордера",
    "tp": "Take Profit % - уровень TP ордера",
    "coin-regime": "Включает regime_action (FULL/DYN/OFF) для sizing",
    "strategies": "Какие стратегии активны",
    "bar": "Таймфрейм (влияет на частоту сигналов)",
}


# =============================================================================
# LOGGING
# =============================================================================

def setup_logging() -> logging.Logger:
    """Настройка логирования."""
    os.makedirs("logs", exist_ok=True)
    log_file = f"logs/trade_app_tg_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("TradeAppTG")


# =============================================================================
# ARGUMENT PARSING
# =============================================================================

def parse_telegram_runner_args(cmd_line: str) -> List[str]:
    """
    Парсит аргументы telegram_runner из командной строки.

    Формат: telegram_runner(--arg1 val1 --arg2 val2)

    Args:
        cmd_line: Строка типа "telegram_runner(--symbols BTCUSDT --coin-regime)"

    Returns:
        Список аргументов для subprocess
    """
    # Ищем telegram_runner(...) pattern
    match = re.search(r'telegram_runner\s*\(([^)]*)\)', cmd_line)
    if not match:
        return []

    args_str = match.group(1).strip()
    if not args_str:
        return []

    # Разбиваем на токены (учитываем кавычки)
    import shlex
    try:
        args = shlex.split(args_str)
    except ValueError:
        # Fallback на простой split
        args = args_str.split()

    return args


def extract_trading_config(args: List[str]) -> Dict[str, Any]:
    """
    Извлекает параметры влияющие на торговлю из аргументов telegram_runner.

    Args:
        args: Список аргументов telegram_runner.py

    Returns:
        Словарь с торговыми параметрами
    """
    config = {
        "sl_pct": None,
        "tp_pct": None,
        "coin_regime_enabled": False,
        "strategies": [],
        "bar": "daily",
        "continuous": False,
        "interval": 86400,
    }

    i = 0
    while i < len(args):
        arg = args[i]

        if arg == "--sl" and i + 1 < len(args):
            config["sl_pct"] = float(args[i + 1])
            i += 2
        elif arg == "--tp" and i + 1 < len(args):
            config["tp_pct"] = float(args[i + 1])
            i += 2
        elif arg == "--coin-regime":
            config["coin_regime_enabled"] = True
            i += 1
        elif arg == "--strategies" and i + 1 < len(args):
            config["strategies"] = args[i + 1].split(",")
            i += 2
        elif arg == "--strategy" and i + 1 < len(args):
            config["strategies"] = [args[i + 1]]
            i += 2
        elif arg == "--bar" and i + 1 < len(args):
            config["bar"] = args[i + 1]
            i += 2
        elif arg == "--continuous":
            config["continuous"] = True
            i += 1
        elif arg == "--interval" and i + 1 < len(args):
            config["interval"] = int(args[i + 1])
            i += 2
        else:
            i += 1

    return config


# =============================================================================
# MAIN
# =============================================================================

async def run_tradebot_server(host: str, port: int, logger: logging.Logger):
    """
    Запускает TradeBot API сервер.

    Args:
        host: Хост
        port: Порт
        logger: Логгер
    """
    from core.models import TradeSignal
    from api.server import SignalAPI

    # Callback для обработки сигналов
    async def handle_signal(signal: TradeSignal) -> str:
        """Обработчик сигналов от telegram_runner.py"""
        logger.info("=" * 50)
        logger.info("NEW SIGNAL FROM TELEGRAM_RUNNER")
        logger.info("=" * 50)
        logger.info(f"Signal ID:    {signal.signal_id}")
        logger.info(f"Symbol:       {signal.symbol}")
        logger.info(f"Direction:    {signal.direction}")
        logger.info(f"Entry:        {signal.entry_price}")
        logger.info(f"SL:           {signal.stop_loss} (-{signal.sl_pct:.2f}%)")
        logger.info(f"TP:           {signal.take_profit} (+{signal.tp_pct:.2f}%)")
        logger.info(f"Strategy:     {signal.strategy}")
        logger.info(f"Action:       {signal.action.value}")  # FULL/DYN/OFF
        logger.info(f"Coin Regime:  {signal.coin_regime}")
        logger.info("=" * 50)

        # TODO: Здесь будет реальная торговля через Binance Adapter
        # Сейчас - только логирование

        position_id = f"POS_{signal.signal_id}"
        return position_id

    # Создаём и запускаем сервер
    api = SignalAPI(
        host=host,
        port=port,
        on_signal=handle_signal,
    )

    logger.info(f"TradeBot API starting on http://{host}:{port}")
    await api.start()


def run_telegram_runner(
    args: List[str],
    tradebot_url: str,
    logger: logging.Logger,
) -> subprocess.Popen:
    """
    Запускает telegram_runner.py как subprocess.

    Args:
        args: Аргументы для telegram_runner.py
        tradebot_url: URL TradeBot API
        logger: Логгер

    Returns:
        Popen объект процесса
    """
    # Добавляем --tradebot-url если не указан
    if "--tradebot-url" not in args:
        args.extend(["--tradebot-url", tradebot_url])

    # Формируем команду
    telegram_runner_path = os.path.join(SIGNALS_DIR, "telegram_runner.py")
    cmd = ["py", "-3.12", telegram_runner_path] + args

    logger.info(f"Starting telegram_runner.py with args: {' '.join(args)}")
    logger.info(f"Command: {' '.join(cmd)}")

    # Запускаем
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=SIGNALS_DIR,
    )

    return process


async def stream_process_output(process: subprocess.Popen, logger: logging.Logger):
    """Стримит вывод процесса в лог."""
    loop = asyncio.get_event_loop()

    while True:
        # Читаем строку асинхронно
        line = await loop.run_in_executor(None, process.stdout.readline)
        if not line:
            break
        line = line.rstrip()
        if line:
            logger.info(f"[telegram_runner] {line}")


async def main():
    """Главная функция."""
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("TRADE APP TG - STARTING")
    logger.info("=" * 60)
    logger.info(f"Time: {datetime.now().isoformat()}")
    logger.info("")

    # Парсим командную строку
    cmd_line = " ".join(sys.argv[1:])

    if not cmd_line or "telegram_runner" not in cmd_line:
        print(__doc__)
        print("\nПример:")
        print('  py -3.12 trade_app_tg.py "telegram_runner(--symbols BTCUSDT --coin-regime)"')
        print('  py -3.12 trade_app_tg.py "telegram_runner(--top 20 --continuous --interval 3600)"')
        sys.exit(1)

    # Извлекаем аргументы telegram_runner
    tr_args = parse_telegram_runner_args(cmd_line)
    logger.info(f"telegram_runner args: {tr_args}")

    # Извлекаем торговые параметры
    trading_config = extract_trading_config(tr_args)
    logger.info(f"Trading config: {trading_config}")

    # Проверяем критичные параметры
    if trading_config["sl_pct"] is None:
        logger.warning("WARNING: --sl not specified, will use default from config.json")
    if trading_config["tp_pct"] is None:
        logger.warning("WARNING: --tp not specified, will use default from config.json")
    if not trading_config["coin_regime_enabled"]:
        logger.warning("WARNING: --coin-regime not enabled, all signals will be FULL size")

    # URL TradeBot API
    tradebot_url = f"http://{DEFAULT_TRADEBOT_HOST}:{DEFAULT_TRADEBOT_PORT}"
    logger.info(f"TradeBot URL: {tradebot_url}")

    # Запускаем TradeBot сервер в фоне
    server_task = asyncio.create_task(
        run_tradebot_server(DEFAULT_TRADEBOT_HOST, DEFAULT_TRADEBOT_PORT, logger)
    )

    # Даём серверу время на запуск
    await asyncio.sleep(2)

    # Запускаем telegram_runner.py
    process = run_telegram_runner(tr_args, tradebot_url, logger)

    # Стримим вывод telegram_runner
    output_task = asyncio.create_task(stream_process_output(process, logger))

    # Ждём завершения
    try:
        await output_task
        return_code = process.wait()
        logger.info(f"telegram_runner.py finished with code {return_code}")
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        process.terminate()
        server_task.cancel()
    except Exception as e:
        logger.error(f"Error: {e}")
        process.terminate()
        server_task.cancel()
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nShutdown complete.")
