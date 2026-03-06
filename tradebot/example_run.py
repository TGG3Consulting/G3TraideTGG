# -*- coding: utf-8 -*-
"""
Trade Bot - Пример запуска.

Демонстрирует:
1. Запуск API сервера
2. Приём сигналов
3. Логирование

Запуск:
    cd G:\BinanceFriend\tradebot
    py -3.12 example_run.py
"""

import asyncio
import logging
from datetime import datetime

from core.models import TradeSignal
from api.server import SignalAPI

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def handle_signal(signal: TradeSignal) -> str:
    """
    Обработчик сигналов.

    В реальном боте здесь будет:
    1. Проверка риск-лимитов
    2. Расчёт размера позиции
    3. Отправка ордеров на биржу

    Сейчас - просто логируем.
    """
    logger.info("=" * 60)
    logger.info("NEW SIGNAL RECEIVED")
    logger.info("=" * 60)
    logger.info(f"Signal ID:    {signal.signal_id}")
    logger.info(f"Symbol:       {signal.symbol}")
    logger.info(f"Direction:    {signal.direction}")
    logger.info(f"Entry Price:  {signal.entry_price}")
    logger.info(f"Stop Loss:    {signal.stop_loss} (-{signal.sl_pct:.2f}%)")
    logger.info(f"Take Profit:  {signal.take_profit} (+{signal.tp_pct:.2f}%)")
    logger.info(f"Strategy:     {signal.strategy}")
    logger.info(f"Action:       {signal.action.value}")
    logger.info(f"Coin Regime:  {signal.coin_regime}")
    logger.info("=" * 60)

    # Генерируем ID позиции
    position_id = f"POS_{signal.signal_id}"

    # В реальном боте здесь будет открытие позиции
    # position = await engine.open_position(signal)

    return position_id


def main():
    """Точка входа."""
    logger.info("=" * 60)
    logger.info("TRADE BOT STARTING")
    logger.info("=" * 60)
    logger.info(f"Time: {datetime.utcnow().isoformat()}")
    logger.info("Mode: DEMO (no real trading)")
    logger.info("")
    logger.info("API Server: http://127.0.0.1:8080")
    logger.info("")
    logger.info("Endpoints:")
    logger.info("  POST /signal   - Send trading signal")
    logger.info("  GET  /status   - Bot status")
    logger.info("  GET  /health   - Health check")
    logger.info("")
    logger.info("Example curl:")
    logger.info('  curl -X POST http://127.0.0.1:8080/signal \\')
    logger.info('    -H "Content-Type: application/json" \\')
    logger.info('    -d \'{"signal_id":"test_001","symbol":"BTCUSDT","direction":"LONG",')
    logger.info('         "entry_price":42500,"stop_loss":40375,"take_profit":46750,')
    logger.info('         "strategy":"ls_fade","action":"FULL"}\'')
    logger.info("")
    logger.info("=" * 60)

    # Создаём API сервер
    api = SignalAPI(
        host="127.0.0.1",
        port=8080,
        on_signal=handle_signal,
    )

    # Запускаем
    api.run()


if __name__ == "__main__":
    main()
