# -*- coding: utf-8 -*-
"""
Telegram Notifier - отправка алертов в Telegram.
"""

import asyncio
import html
import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any
import aiohttp
import structlog

from config.settings import settings
from .models import Detection, AlertSeverity
from .alert_details_store import get_alert_store, AlertDetails


logger = structlog.get_logger(__name__)


# Русские названия типов детекции
DETECTION_TYPES_RU: Dict[str, str] = {
    "ACTIVE_PUMP": "🚀 Активный памп",
    "ACTIVE_DUMP": "💥 Активный дамп",
    "VOLUME_SPIKE": "📊 Всплеск объёма",
    "VOLUME_SPIKE_HIGH": "📊⬆️ Высокий всплеск объёма",
    "VOLUME_SPIKE_EXTREME": "📊🔥 Экстремальный всплеск объёма",
    "PRICE_VELOCITY": "⚡ Резкое движение цены",
    "PRICE_VELOCITY_HIGH": "⚡⚡ Сильное движение цены",
    "PRICE_VELOCITY_EXTREME": "⚡🔥 Экстремальное движение цены",
    "WASH_TRADING": "🔄 Отмывочная торговля",
    "WASH_TRADING_SUSPECTED": "🔄❓ Подозрение на wash trading",
    "WASH_TRADING_LIKELY": "🔄🚨 Вероятный wash trading",
    "COORDINATED_BUYING": "🟢 Координированные покупки",
    "COORDINATED_SELLING": "🔴 Координированные продажи",
    "ORDERBOOK_IMBALANCE": "📚 Дисбаланс стакана",
    "WIDE_SPREAD": "↔️ Широкий спред",
}

# Русские названия severity
SEVERITY_RU: Dict[str, str] = {
    "INFO": "ℹ️ Информация",
    "WARNING": "⚠️ Внимание",
    "ALERT": "🚨 Алерт",
    "CRITICAL": "🔴 Критический",
}

# Интерпретации для деталей
DETAIL_INTERPRETATIONS_RU: Dict[str, str] = {
    "price_change_pct": "Изменение цены",
    "price_change_5m_pct": "Изменение за 5 мин",
    "price_change_1m_pct": "Изменение за 1 мин",
    "volume_ratio": "Объём к среднему",
    "spike_ratio": "Spike объёма",
    "volume_5m": "Объём 5м (USD)",
    "trades_count": "Сделок",
    "buy_ratio": "Покупки",
    "sell_ratio": "Продажи",
    "buy_count": "Кол-во покупок",
    "sell_count": "Кол-во продаж",
    "imbalance": "Дисбаланс стакана",
    "book_imbalance": "Дисбаланс стакана",
    "bid_volume": "Bid объём",
    "ask_volume": "Ask объём",
    "current_price": "Текущая цена",
    "best_bid": "Лучший Bid",
    "best_ask": "Лучший Ask",
    "oi_change_1h": "OI за 1ч",
    "oi_change_1h_pct": "OI за 1ч",
    "oi_change_5m_pct": "OI за 5мин",
    "funding": "Funding rate",
    "funding_rate_pct": "Funding rate",
    "futures_funding": "Futures Funding",
    "futures_oi_change_1h": "Futures OI за 1ч",
    "futures_pump_risk": "Риск пампа (futures)",
    "current_oi_usd": "Open Interest (USD)",
    "spread_pct": "Спред",
    "direction": "Направление",
    "repeat_ratio": "Повтор. сделок",
    "repeated_quantity": "Повтор. размер",
    "avg_interval_ms": "Интервал (мс)",
}


@dataclass
class TelegramConfig:
    """
    Конфигурация Telegram.

    Значения по умолчанию загружаются из config/config.yaml через settings.telegram.*
    """
    bot_token: str = ""
    chat_id: str = ""              # Для манипуляций (алертов)
    signals_bot_token: str = ""    # Бот для сигналов (если пусто - используется bot_token)
    signals_chat_id: str = ""      # Чат для сигналов (если пусто - используется chat_id)

    # Настройки отправки (defaults из config.yaml)
    enabled: bool = True
    min_severity: AlertSeverity = None  # Будет установлено из settings
    max_messages_per_minute: int = None  # Будет установлено из settings

    # Батчинг
    batch_enabled: bool = True
    batch_size: int = 5
    batch_interval_sec: int = 30
    min_interval_sec: int = 10

    def __post_init__(self):
        """Загрузить defaults из settings если не указаны."""
        if self.min_severity is None:
            self.min_severity = AlertSeverity[settings.telegram.min_severity]
        if self.max_messages_per_minute is None:
            self.max_messages_per_minute = settings.telegram.max_messages_per_minute
        # Батчинг настройки
        self.batch_enabled = getattr(settings.telegram, 'batch_enabled', True)
        self.batch_size = getattr(settings.telegram, 'batch_size', 5)
        self.batch_interval_sec = getattr(settings.telegram, 'batch_interval_sec', 30)
        self.min_interval_sec = getattr(settings.telegram, 'min_interval_sec', 10)

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


class TelegramNotifier:
    """
    Отправка алертов в Telegram.

    Использование:
        config = TelegramConfig(bot_token="...", chat_id="...")
        notifier = TelegramNotifier(config)
        await notifier.start()
        await notifier.send_alert(detection)
        await notifier.stop()
    """

    API_URL = "https://api.telegram.org/bot{token}/sendMessage"

    # Эмодзи для severity
    SEVERITY_EMOJI = {
        AlertSeverity.INFO: "ℹ️",
        AlertSeverity.WARNING: "⚠️",
        AlertSeverity.ALERT: "🚨",
        AlertSeverity.CRITICAL: "🔴",
    }

    # Эмодзи для типов детекции
    TYPE_EMOJI = {
        "ACTIVE_PUMP": "📈🚀",
        "ACTIVE_DUMP": "📉💥",
        "VOLUME_SPIKE": "📊",
        "VOLUME_SPIKE_HIGH": "📊⬆️",
        "VOLUME_SPIKE_EXTREME": "📊🔥",
        "PRICE_VELOCITY": "⚡",
        "PRICE_VELOCITY_HIGH": "⚡⚡",
        "PRICE_VELOCITY_EXTREME": "⚡🔥",
        "WASH_TRADING": "🔄",
        "WASH_TRADING_SUSPECTED": "🔄❓",
        "WASH_TRADING_LIKELY": "🔄🚨",
        "COORDINATED_BUYING": "🟢📥",
        "COORDINATED_SELLING": "🔴📤",
        "ORDERBOOK_IMBALANCE": "📚",
        "WIDE_SPREAD": "↔️",
    }

    def __init__(self, config: TelegramConfig):
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._message_times: list[datetime] = []
        # LEAK-3 FIX: Ограничиваем размер очереди чтобы не было memory leak
        self._queue: asyncio.Queue[Detection] = asyncio.Queue(maxsize=1000)
        self._running = False
        self._sender_task: Optional[asyncio.Task] = None
        self._callback_task: Optional[asyncio.Task] = None

        # Батчинг
        self._batch_buffer: list[Detection] = []
        self._last_send_time: Optional[datetime] = None
        # RACE-5 FIX: Инициализируем в __init__ (было hasattr check)
        self._batch_start_time: Optional[datetime] = None

        # Callback tracking
        self._last_update_id: int = 0
        self._alert_store = get_alert_store()

        # Stats
        self._stats = {
            "sent": 0,
            "failed": 0,
            "rate_limited": 0,
            "batched": 0,
            "callbacks_handled": 0,
        }

    async def start(self):
        """Запустить notifier."""
        if not self._config.is_configured:
            logger.warning("telegram_not_configured")
            return

        self._running = True
        self._session = aiohttp.ClientSession()
        self._sender_task = asyncio.create_task(self._sender_loop())
        self._callback_task = asyncio.create_task(self._callback_loop())

        # Запустить хранилище алертов
        await self._alert_store.start()

        # Отправить тестовое сообщение
        await self._send_startup_message()

        logger.info("telegram_notifier_started")

    async def stop(self):
        """Остановить notifier."""
        self._running = False

        if self._sender_task:
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass

        if self._callback_task:
            self._callback_task.cancel()
            try:
                await self._callback_task
            except asyncio.CancelledError:
                pass

        # Остановить хранилище алертов
        await self._alert_store.stop()

        # Отправить сообщение о завершении
        if self._session and not self._session.closed:
            await self._send_shutdown_message()
            await self._session.close()

        logger.info("telegram_notifier_stopped", stats=self._stats)

    async def send_alert(self, detection: Detection):
        """Добавить алерт в очередь на отправку."""
        if not self._config.enabled or not self._config.is_configured:
            return

        if detection.severity.value < self._config.min_severity.value:
            return

        # LEAK-3 FIX: Не блокируемся при полной очереди, просто пропускаем
        try:
            self._queue.put_nowait(detection)
        except asyncio.QueueFull:
            logger.warning("telegram_queue_full", symbol=detection.symbol, dropped=True)
            self._stats["failed"] += 1

    async def _sender_loop(self):
        """Фоновый цикл отправки сообщений с батчингом."""
        while self._running:
            try:
                # Попытаться получить из очереди
                try:
                    detection = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0
                    )
                    self._batch_buffer.append(detection)
                except asyncio.TimeoutError:
                    pass

                # Проверить нужно ли отправлять батч
                should_send = False
                now = datetime.now()

                if self._batch_buffer:
                    # Батч заполнен
                    if len(self._batch_buffer) >= self._config.batch_size:
                        should_send = True
                    # Прошёл batch_interval_sec
                    elif self._last_send_time:
                        elapsed = (now - self._last_send_time).total_seconds()
                        if elapsed >= self._config.batch_interval_sec:
                            should_send = True
                    # Первый алерт - подождать
                    elif not self._last_send_time and len(self._batch_buffer) > 0:
                        # Установить время первого алерта в батче
                        # RACE-5 FIX: Используем proper None check вместо hasattr
                        if self._batch_start_time is None:
                            self._batch_start_time = now
                        elif (now - self._batch_start_time).total_seconds() >= self._config.batch_interval_sec:
                            should_send = True

                if should_send and self._batch_buffer:
                    # Проверить минимальный интервал
                    if self._last_send_time:
                        elapsed = (now - self._last_send_time).total_seconds()
                        if elapsed < self._config.min_interval_sec:
                            await asyncio.sleep(self._config.min_interval_sec - elapsed)

                    # Rate limiting
                    if not self._check_rate_limit():
                        self._stats["rate_limited"] += 1
                        await asyncio.sleep(2)
                        continue

                    # Отправить батч
                    await self._send_batch()
                    self._last_send_time = datetime.now()
                    self._batch_start_time = None

            except asyncio.CancelledError:
                # Отправить оставшиеся перед выходом
                if self._batch_buffer:
                    await self._send_batch()
                break
            except Exception as e:
                logger.error("telegram_sender_error", error=str(e))
                await asyncio.sleep(1)

    def _check_rate_limit(self) -> bool:
        """Проверить rate limit."""
        now = datetime.now()
        minute_ago = datetime.now().replace(second=0, microsecond=0)

        # Очистить старые
        self._message_times = [
            t for t in self._message_times
            if (now - t).total_seconds() < 60
        ]

        return len(self._message_times) < self._config.max_messages_per_minute

    async def _send_batch(self):
        """Отправить батч детекций в одном сообщении с inline-кнопками."""
        if not self._batch_buffer:
            return

        detections = self._batch_buffer[:self._config.batch_size]
        self._batch_buffer = self._batch_buffer[self._config.batch_size:]

        # Сохранить детали каждого алерта в store
        alert_ids = []
        for det in detections:
            alert = self._alert_store.create_alert_from_detection(det)
            alert_ids.append(alert.alert_id)

        if len(detections) == 1:
            # Одна детекция - полный формат с кнопкой
            message = self._format_detection_ru(detections[0])
            buttons = [[{
                "text": f"📋 Подробный отчёт",
                "callback_data": f"alert:{alert_ids[0]}",
            }]]
            success = await self._send_message_with_buttons(message, buttons)
        else:
            # Несколько - компактный батч формат с кнопками
            message, buttons = self._format_batch(detections, alert_ids)
            success = await self._send_message_with_buttons(message, buttons)

        if success:
            self._stats["sent"] += 1
            self._stats["batched"] += len(detections)
            self._message_times.append(datetime.now())
            logger.info("telegram_batch_sent", count=len(detections), alert_ids=alert_ids)
        else:
            self._stats["failed"] += 1
            # Вернуть в буфер при ошибке
            self._batch_buffer = detections + self._batch_buffer

    async def _send_detection(self, detection: Detection):
        """Отправить одну детекцию в Telegram."""
        message = self._format_detection(detection)
        success = await self._send_message(message)

        if success:
            self._stats["sent"] += 1
            self._message_times.append(datetime.now())
        else:
            self._stats["failed"] += 1

    def _format_batch(self, detections: list, alert_ids: List[str]) -> tuple:
        """
        Форматировать батч детекций в компактном виде на русском.

        Returns:
            (text, buttons) - текст сообщения и inline-кнопки
        """
        lines = [
            f"🚨 <b>АЛЕРТЫ МАНИПУЛЯЦИЙ</b> ({len(detections)} шт.)",
            f"⏰ {datetime.now().strftime('%H:%M:%S')}",
            "",
        ]

        buttons = []

        for i, (det, alert_id) in enumerate(zip(detections, alert_ids), 1):
            severity_emoji = self.SEVERITY_EMOJI.get(det.severity, "❓")
            type_ru = DETECTION_TYPES_RU.get(det.detection_type, det.detection_type)

            # Компактная строка для каждого алерта
            score_bar = "█" * (det.score // 20) + "░" * (5 - det.score // 20)
            # TG-1 FIX: Escape symbol для HTML безопасности
            safe_symbol = html.escape(det.symbol)
            line = (
                f"{i}. {severity_emoji} <code>{safe_symbol}</code>\n"
                f"   {type_ru}\n"
                f"   [{score_bar}] {det.score}/100"
            )
            lines.append(line)

            # Ключевые детали (если есть)
            key_details = []
            if det.details:
                if 'price_change_pct' in det.details:
                    sign = "+" if det.details['price_change_pct'] > 0 else ""
                    key_details.append(f"Цена: {sign}{det.details['price_change_pct']:.1f}%")
                if 'volume_ratio' in det.details:
                    key_details.append(f"Объём: {det.details['volume_ratio']:.1f}x")
                if 'imbalance' in det.details:
                    imb = det.details['imbalance']
                    if isinstance(imb, float) and imb <= 1:
                        key_details.append(f"Дисб.: {imb:.0%}")
                    else:
                        key_details.append(f"Дисб.: {imb}")

            if key_details:
                lines.append(f"   └ {' | '.join(key_details)}")

            lines.append("")

            # Кнопка для этого алерта
            buttons.append([{
                "text": f"📋 {det.symbol} - Подробнее",
                "callback_data": f"alert:{alert_id}",
            }])

        lines.append("<i>👆 Нажмите кнопку для детального отчёта в ЛС</i>")

        return "\n".join(lines), buttons

    def _format_detection(self, detection: Detection) -> str:
        """Форматировать детекцию для Telegram (legacy English)."""
        severity_emoji = self.SEVERITY_EMOJI.get(detection.severity, "❓")
        type_emoji = self.TYPE_EMOJI.get(detection.detection_type, "🔍")

        # Заголовок
        # TG-1 FIX: Escape для HTML безопасности
        safe_symbol = html.escape(detection.symbol)
        lines = [
            f"{severity_emoji} <b>MANIPULATION DETECTED</b> {type_emoji}",
            "",
            f"<b>Symbol:</b> <code>{safe_symbol}</code>",
            f"<b>Type:</b> <code>{detection.detection_type}</code>",
            f"<b>Severity:</b> {detection.severity.name}",
            f"<b>Score:</b> {detection.score}/100",
            f"<b>Time:</b> {detection.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        # Детали
        if detection.details:
            lines.append("")
            lines.append("<b>📊 Details:</b>")
            for key, value in detection.details.items():
                # Форматирование значений
                if isinstance(value, float):
                    formatted = f"{value:.4f}"
                elif isinstance(value, str) and value.replace('.', '').replace('-', '').isdigit():
                    try:
                        num = float(value)
                        if abs(num) > 1000:
                            formatted = f"{num:,.0f}"
                        elif abs(num) > 1:
                            formatted = f"{num:.2f}"
                        else:
                            formatted = f"{num:.6f}"
                    except:
                        formatted = value
                else:
                    formatted = str(value)

                # Красивое имя поля
                display_key = key.replace("_", " ").title()
                lines.append(f"  • {display_key}: <code>{html.escape(formatted)}</code>")

        # Evidence
        if detection.evidence:
            lines.append("")
            lines.append("<b>🔍 Evidence:</b>")
            for evidence in detection.evidence:
                lines.append(f"  • {html.escape(evidence)}")

        # Ссылки
        lines.append("")
        symbol_base = detection.symbol.replace("USDT", "")
        lines.append(
            f"<a href='https://www.binance.com/en/trade/{symbol_base}_USDT'>📈 Open Chart</a> | "
            f"<a href='https://www.binance.com/en/futures/{detection.symbol}'>📊 Futures</a>"
        )

        return "\n".join(lines)

    def _format_detection_ru(self, detection: Detection) -> str:
        """Форматировать детекцию для Telegram на русском языке."""
        severity_emoji = self.SEVERITY_EMOJI.get(detection.severity, "❓")
        detection_type_ru = DETECTION_TYPES_RU.get(detection.detection_type, detection.detection_type)
        severity_ru = SEVERITY_RU.get(detection.severity.name, detection.severity.name)

        score_bar = "█" * (detection.score // 10) + "░" * (10 - detection.score // 10)

        # Заголовок
        # TG-1 FIX: Escape для HTML безопасности
        safe_symbol = html.escape(detection.symbol)
        lines = [
            f"{severity_emoji} <b>ОБНАРУЖЕНА МАНИПУЛЯЦИЯ</b>",
            "",
            f"🪙 <b>Символ:</b> <code>{safe_symbol}</code>",
            f"📌 <b>Тип:</b> {detection_type_ru}",
            f"⚡ <b>Уровень:</b> {severity_ru}",
            f"📊 <b>Score:</b> [{score_bar}] {detection.score}/100",
            f"⏰ <b>Время:</b> {detection.timestamp.strftime('%H:%M:%S')}",
        ]

        # Детали
        if detection.details:
            lines.append("")
            lines.append("<b>📋 Детали:</b>")

            # Приоритетные поля
            priority_keys = ['price_change_pct', 'volume_ratio', 'volume_5m', 'trades_count', 'buy_ratio', 'imbalance']
            shown_keys = set()

            for key in priority_keys:
                if key in detection.details:
                    value = detection.details[key]
                    display_name = DETAIL_INTERPRETATIONS_RU.get(key, key.replace("_", " ").title())
                    formatted = self._format_value(value)
                    lines.append(f"  • {display_name}: <code>{html.escape(formatted)}</code>")
                    shown_keys.add(key)

            # Остальные поля
            for key, value in detection.details.items():
                if key not in shown_keys:
                    display_name = DETAIL_INTERPRETATIONS_RU.get(key, key.replace("_", " ").title())
                    formatted = self._format_value(value)
                    lines.append(f"  • {display_name}: <code>{html.escape(formatted)}</code>")

        # Evidence
        if detection.evidence:
            lines.append("")
            lines.append("<b>🔍 Признаки:</b>")
            for evidence in detection.evidence[:3]:  # Ограничить до 3
                lines.append(f"  ⚠️ {html.escape(evidence)}")

        # Ссылки
        lines.append("")
        symbol_base = detection.symbol.replace("USDT", "")
        lines.append(
            f"<a href='https://www.binance.com/ru/futures/{detection.symbol}'>📊 Открыть график</a>"
        )

        return "\n".join(lines)

    def _format_value(self, value) -> str:
        """Форматировать значение для отображения."""
        if isinstance(value, float):
            if abs(value) > 1000:
                return f"{value:,.0f}"
            elif abs(value) > 1:
                return f"{value:.2f}"
            else:
                return f"{value:.4f}"
        elif isinstance(value, str) and value.replace('.', '').replace('-', '').isdigit():
            try:
                num = float(value)
                if abs(num) > 1000:
                    return f"{num:,.0f}"
                elif abs(num) > 1:
                    return f"{num:.2f}"
                else:
                    return f"{num:.6f}"
            except:
                return value
        return str(value)

    async def _send_message(self, text: str, parse_mode: str = "HTML", chat_id: str = None, bot_token: str = None, retry: int = 0) -> bool:
        """Отправить сообщение в Telegram с retry при rate limit."""
        if not self._session or self._session.closed:
            return False

        # Использовать переданный bot_token или дефолтный
        target_bot_token = bot_token or self._config.bot_token
        url = self.API_URL.format(token=target_bot_token)

        # Использовать переданный chat_id или дефолтный
        target_chat_id = chat_id or self._config.chat_id

        payload = {
            "chat_id": target_chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            async with self._session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    return True
                elif resp.status == 429 and retry < 2:
                    # Rate limit - подождать и retry
                    import json as json_lib
                    try:
                        data = json_lib.loads(await resp.text())
                        retry_after = data.get("parameters", {}).get("retry_after", 30)
                    except:
                        retry_after = 30
                    logger.warning("telegram_rate_limit", retry_after=retry_after, retry=retry+1)
                    await asyncio.sleep(retry_after + 1)
                    return await self._send_message(text, parse_mode, chat_id, bot_token, retry + 1)
                else:
                    error = await resp.text()
                    logger.warning(
                        "telegram_send_failed",
                        status=resp.status,
                        error=error[:200]
                    )
                    return False
        except Exception as e:
            logger.warning("telegram_request_error", error=str(e))
            return False

    async def _send_startup_message(self):
        """Отправить сообщение о запуске."""
        message = (
            "🟢 <b>BinanceFriend Screener Запущен</b>\n"
            "\n"
            f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            "📡 Мониторинг уязвимых пар на манипуляции...\n"
            "\n"
            "💡 <i>Нажмите кнопку под алертом для детального отчёта в ЛС</i>"
        )
        await self._send_message(message)

    async def _send_shutdown_message(self):
        """Отправить сообщение о завершении."""
        message = (
            "🔴 <b>BinanceFriend Screener Остановлен</b>\n"
            "\n"
            f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📊 Статистика: Отправлено {self._stats['sent']}, Ошибок {self._stats['failed']}\n"
            f"📋 Обработано callback'ов: {self._stats['callbacks_handled']}"
        )
        await self._send_message(message)

    async def send_status(self, vulnerable_count: int, monitored_count: int):
        """Отправить статус сканирования."""
        message = (
            "📊 <b>Сканирование завершено</b>\n"
            "\n"
            f"🔍 Найдено уязвимых пар: <b>{vulnerable_count}</b>\n"
            f"👁 Сейчас мониторим: <b>{monitored_count}</b>\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        await self._send_message(message)

    async def send_raw_message(self, message: str):
        """
        Отправить сырое сообщение в Telegram.

        Используется для торговых сигналов и других
        кастомных сообщений с HTML форматированием.

        Args:
            message: HTML-форматированное сообщение
        """
        await self._send_message(message)

    async def send_trade_signal(self, signal_text: str):
        """
        Отправить торговый сигнал в отдельный чат через отдельного бота.

        Использует signals_bot_token/signals_chat_id если указаны,
        иначе основной bot_token/chat_id.
        """
        # Использовать отдельный бот и чат для сигналов если указаны
        signals_bot = self._config.signals_bot_token or self._config.bot_token
        signals_chat = self._config.signals_chat_id or self._config.chat_id

        logger.info(
            "sending_trade_signal",
            chat_id=signals_chat,
            has_signals_bot=bool(self._config.signals_bot_token),
            text_length=len(signal_text),
        )

        success = await self._send_message(signal_text, chat_id=signals_chat, bot_token=signals_bot)

        if success:
            logger.info("trade_signal_sent_to_telegram", chat_id=signals_chat)
            self._stats["sent"] += 1
        else:
            logger.error("trade_signal_send_failed", chat_id=signals_chat)
            self._stats["failed"] += 1

    def get_stats(self) -> dict:
        """Получить статистику."""
        return self._stats.copy()

    # ==================== CALLBACK HANDLING ====================

    async def _callback_loop(self):
        """Цикл обработки callback'ов от inline-кнопок."""
        while self._running:
            try:
                await self._poll_updates()
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("callback_loop_error", error=str(e))
                await asyncio.sleep(5)

    async def _poll_updates(self):
        """Получить и обработать новые updates от Telegram."""
        if not self._session or self._session.closed:
            return

        url = f"https://api.telegram.org/bot{self._config.bot_token}/getUpdates"
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 5,
            "allowed_updates": json.dumps(["callback_query"]),
        }

        try:
            async with self._session.get(url, params=params, timeout=10) as resp:
                if resp.status != 200:
                    return

                data = await resp.json()
                if not data.get("ok"):
                    return

                for update in data.get("result", []):
                    self._last_update_id = update["update_id"]

                    if "callback_query" in update:
                        await self._handle_callback(update["callback_query"])

        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.debug("poll_updates_error", error=str(e))

    async def _handle_callback(self, callback_query: dict):
        """Обработать callback от inline-кнопки."""
        callback_id = callback_query["id"]
        callback_data = callback_query.get("data", "")
        user_id = callback_query["from"]["id"]
        user_name = callback_query["from"].get("first_name", "User")

        logger.info("callback_received", data=callback_data, user_id=user_id)

        # Формат данных: "alert:{alert_id}"
        if callback_data.startswith("alert:"):
            alert_id = callback_data.split(":", 1)[1]
            alert = self._alert_store.get_alert(alert_id)

            if alert:
                # Отправить детальный отчёт в ЛС пользователю
                report = self._format_detailed_report(alert)
                await self._send_message_to_user(user_id, report)
                await self._answer_callback(callback_id, "✅ Отчёт отправлен в ЛС")
                self._stats["callbacks_handled"] += 1
            else:
                await self._answer_callback(callback_id, "❌ Алерт устарел (TTL истёк)")
        else:
            await self._answer_callback(callback_id, "❓ Неизвестная команда")

    async def _answer_callback(self, callback_id: str, text: str):
        """Ответить на callback query."""
        url = f"https://api.telegram.org/bot{self._config.bot_token}/answerCallbackQuery"
        payload = {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False,
        }

        try:
            async with self._session.post(url, json=payload, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def _send_message_to_user(self, user_id: int, text: str) -> bool:
        """Отправить сообщение в ЛС пользователю."""
        if not self._session or self._session.closed:
            return False

        url = f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage"
        payload = {
            "chat_id": user_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with self._session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    return True
                else:
                    error = await resp.text()
                    logger.warning("send_to_user_failed", user_id=user_id, error=error[:100])
                    return False
        except Exception as e:
            logger.warning("send_to_user_error", error=str(e))
            return False

    async def _send_message_with_buttons(
        self,
        text: str,
        buttons: List[List[Dict[str, str]]],
        parse_mode: str = "HTML"
    ) -> bool:
        """Отправить сообщение с inline-кнопками."""
        if not self._session or self._session.closed:
            return False

        url = f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage"
        payload = {
            "chat_id": self._config.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": buttons,
            },
        }

        try:
            async with self._session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    return True
                else:
                    error = await resp.text()
                    logger.warning("send_with_buttons_failed", error=error[:200])
                    return False
        except Exception as e:
            logger.warning("send_with_buttons_error", error=str(e))
            return False

    # ==================== DETAILED REPORTS (RUSSIAN) ====================

    def _format_detailed_report(self, alert: AlertDetails) -> str:
        """Форматировать детальный отчёт на русском языке."""
        detection_type_ru = DETECTION_TYPES_RU.get(alert.detection_type, alert.detection_type)
        severity_ru = SEVERITY_RU.get(alert.severity, alert.severity)

        lines = [
            f"📋 <b>ДЕТАЛЬНЫЙ ОТЧЁТ ПО АЛЕРТУ</b>",
            "",
            f"🪙 <b>Символ:</b> <code>{alert.symbol}</code>",
            f"📌 <b>Тип:</b> {detection_type_ru}",
            f"⚡ <b>Severity:</b> {severity_ru}",
            f"📊 <b>Score:</b> {alert.score}/100 {'█' * (alert.score // 10)}{'░' * (10 - alert.score // 10)}",
            f"⏰ <b>Время:</b> {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        # Торговые данные - ИСПРАВЛЕНО: обрабатываем None
        lines.append("📈 <b>ТОРГОВЫЕ ДАННЫЕ:</b>")
        if alert.trades_count is not None:
            lines.append(f"  • Сделок: <code>{alert.trades_count}</code>")
        else:
            lines.append(f"  • Сделок: <code>N/A</code> (фьючерсные данные)")

        if alert.buy_percent is not None and alert.sell_percent is not None:
            lines.append(f"  • Покупки: <code>{alert.buy_percent:.1f}%</code>")
            lines.append(f"  • Продажи: <code>{alert.sell_percent:.1f}%</code>")
        else:
            lines.append(f"  • Покупки/Продажи: <code>N/A</code>")

        if alert.volume_5m > 0:
            vol_fmt = f"${alert.volume_5m:,.0f}" if alert.volume_5m > 1000 else f"${alert.volume_5m:.2f}"
            lines.append(f"  • Объём 5мин: <code>{vol_fmt}</code>")

        if alert.volume_ratio != 1.0:
            lines.append(f"  • Отношение к среднему: <code>{alert.volume_ratio:.1f}x</code>")

        lines.append("")

        # Данные по биржам
        if alert.exchange_data:
            lines.append("🏛 <b>ДАННЫЕ ПО БИРЖАМ:</b>")
            for ex_name, snap in alert.exchange_data.items():
                lines.append(f"")
                lines.append(f"  <b>{ex_name.upper()}</b>:")
                lines.append(f"    💰 Цена: <code>${snap.price:,.4f}</code>")

                if snap.volume_5m > 0:
                    vol_fmt = f"${snap.volume_5m:,.0f}" if snap.volume_5m > 1000 else f"${snap.volume_5m:.2f}"
                    lines.append(f"    📊 Объём: <code>{vol_fmt}</code>")

                if snap.oi is not None:
                    oi_fmt = f"${snap.oi:,.0f}" if snap.oi > 1000 else f"${snap.oi:.2f}"
                    lines.append(f"    📐 Open Interest: <code>{oi_fmt}</code>")

                if snap.oi_change_1h is not None:
                    sign = "+" if snap.oi_change_1h > 0 else ""
                    lines.append(f"    📈 OI за 1ч: <code>{sign}{snap.oi_change_1h:.2f}%</code>")

                if snap.funding is not None:
                    sign = "+" if snap.funding > 0 else ""
                    lines.append(f"    💸 Funding: <code>{sign}{snap.funding:.4f}%</code>")

                if snap.bid and snap.ask:
                    lines.append(f"    📗 Bid: <code>${snap.bid:,.4f}</code>")
                    lines.append(f"    📕 Ask: <code>${snap.ask:,.4f}</code>")

                if snap.spread_pct is not None:
                    lines.append(f"    ↔️ Спред: <code>{snap.spread_pct:.3f}%</code>")

            lines.append("")

        # Дополнительные детали (фильтруем дубликаты и мусор)
        if alert.details:
            # Пропускаем поля, уже показанные выше
            skip_keys = {'trades_count', 'buy_percent', 'sell_percent', 'volume_5m', 'volume_ratio'}

            # Показываем только полезные данные
            useful_details = {k: v for k, v in alert.details.items()
                             if k not in skip_keys and v is not None}

            if useful_details:
                lines.append("📎 <b>ДОПОЛНИТЕЛЬНЫЕ ДАННЫЕ:</b>")
                for key, value in useful_details.items():
                    display_name = DETAIL_INTERPRETATIONS_RU.get(key, key.replace("_", " ").title())

                    # Форматирование по типу поля
                    if key in ('buy_ratio', 'sell_ratio', 'repeat_ratio'):
                        # Ratio -> проценты
                        pct = float(value) * 100 if float(value) <= 1 else float(value)
                        formatted = f"{pct:.1f}%"
                    elif key in ('imbalance', 'book_imbalance'):
                        # Imbalance в процентах
                        formatted = f"{float(value) * 100:.1f}%"
                    elif key in ('spread_pct', 'price_change_5m_pct', 'price_change_1m_pct',
                                  'oi_change_1h_pct', 'oi_change_5m_pct', 'price_change_1h_pct'):
                        # Уже в процентах
                        sign = "+" if float(value) > 0 else ""
                        formatted = f"{sign}{float(value):.2f}%"
                    elif key in ('futures_funding', 'funding_rate_pct', 'funding_rate'):
                        # Funding rate в процентах (может быть очень маленьким)
                        sign = "+" if float(value) > 0 else ""
                        formatted = f"{sign}{float(value):.4f}%"
                    elif key in ('current_price', 'best_bid', 'best_ask'):
                        formatted = f"${float(value):,.6f}"
                    elif key in ('bid_volume', 'ask_volume', 'volume_5m'):
                        formatted = f"${float(value):,.0f}"
                    elif key == 'direction':
                        formatted = "📈 РОСТ" if value == "UP" else "📉 ПАДЕНИЕ" if value == "DOWN" else str(value)
                    elif isinstance(value, float):
                        if abs(value) > 1000:
                            formatted = f"{value:,.0f}"
                        elif abs(value) > 1:
                            formatted = f"{value:.2f}"
                        else:
                            formatted = f"{value:.4f}"
                    elif isinstance(value, int):
                        formatted = f"{value:,}"
                    else:
                        formatted = str(value)

                    lines.append(f"  • {display_name}: <code>{html.escape(formatted)}</code>")

                lines.append("")

        # Признаки манипуляции
        if alert.evidence:
            lines.append("🔍 <b>ПРИЗНАКИ МАНИПУЛЯЦИИ:</b>")
            for ev in alert.evidence:
                lines.append(f"  ⚠️ {html.escape(ev)}")
            lines.append("")

        # Интерпретация
        lines.append("💡 <b>ИНТЕРПРЕТАЦИЯ:</b>")
        lines.append(self._get_interpretation(alert))
        lines.append("")

        # Ссылки
        symbol_base = alert.symbol.replace("USDT", "")
        lines.append("🔗 <b>ССЫЛКИ:</b>")
        lines.append(f"  <a href='https://www.binance.com/ru/trade/{symbol_base}_USDT'>📈 Binance Spot</a>")
        lines.append(f"  <a href='https://www.binance.com/ru/futures/{alert.symbol}'>📊 Binance Futures</a>")
        lines.append(f"  <a href='https://www.coinglass.com/tv/{symbol_base}USDT'>📉 CoinGlass</a>")

        return "\n".join(lines)

    def _get_interpretation(self, alert: AlertDetails) -> str:
        """Получить текстовую интерпретацию алерта."""
        dt = alert.detection_type

        if "PUMP" in dt:
            if alert.volume_ratio > 5:
                return "🚀 Агрессивный памп с экстремальным объёмом. Возможен pump&dump. Высокий риск входа на текущих уровнях."
            else:
                return "📈 Координированные покупки. Может быть начало памп-движения или органический рост интереса."

        elif "DUMP" in dt:
            if alert.volume_ratio > 5:
                return "💥 Массированные продажи с высоким объёмом. Возможна паника или координированный сброс. Осторожно с лонгами."
            else:
                return "📉 Повышенное давление продавцов. Следите за уровнями поддержки."

        elif "VOLUME_SPIKE" in dt:
            # ИСПРАВЛЕНО: обрабатываем None для buy/sell_percent
            if alert.buy_percent is not None and alert.buy_percent > 60:
                return "📊 Резкий рост объёма с преобладанием покупок. Возможен интерес крупных игроков."
            elif alert.sell_percent is not None and alert.sell_percent > 60:
                return "📊 Резкий рост объёма с преобладанием продаж. Возможен выход крупных позиций."
            else:
                return "📊 Аномальный всплеск объёма. Активность выше нормы, причина неясна."

        elif "WASH_TRADING" in dt:
            return "🔄 Подозрение на wash trading - фиктивные сделки для создания иллюзии объёма. Реальная ликвидность может быть значительно ниже."

        elif "COORDINATED" in dt:
            return "👥 Признаки координированной торговли. Возможно несколько аккаунтов действуют согласованно."

        elif "ORDERBOOK_IMBALANCE" in dt:
            return "📚 Сильный дисбаланс в стакане заявок. Может указывать на предстоящее движение цены."

        elif "WIDE_SPREAD" in dt:
            return "↔️ Аномально широкий спред. Низкая ликвидность, высокий slippage при входе/выходе."

        elif "PRICE_VELOCITY" in dt:
            return "⚡ Резкое движение цены за короткий период. Возможна манипуляция или реакция на новость."

        return "⚠️ Обнаружена аномальная активность. Рекомендуется дополнительный анализ."
