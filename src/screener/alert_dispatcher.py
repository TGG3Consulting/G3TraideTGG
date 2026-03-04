# -*- coding: utf-8 -*-
"""
Alert Dispatcher - отправка алертов в Binance API.
"""

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import aiohttp
import structlog

from config.settings import settings
from .models import Detection, AlertSeverity


logger = structlog.get_logger(__name__)


@dataclass
class AlertConfig:
    """
    Конфигурация для отправки алертов.

    Значения по умолчанию загружаются из config/config.yaml через settings.dispatcher.*
    """
    # URL который даст Binance
    binance_api_url: str = ""

    # API credentials (когда дадут)
    api_key: str = ""
    api_secret: str = ""

    # Минимальный уровень для отправки (из config.yaml)
    min_severity: AlertSeverity = None

    # Батчинг (из config.yaml)
    batch_size: int = None
    batch_interval_seconds: int = None

    # Retry (из config.yaml)
    max_retries: int = None
    retry_delay_seconds: int = None

    # Local logging (из config.yaml)
    log_to_file: bool = None
    log_file_path: str = None

    def __post_init__(self):
        """Загрузить defaults из settings если не указаны."""
        if self.min_severity is None:
            self.min_severity = AlertSeverity[settings.dispatcher.min_severity]
        if self.batch_size is None:
            self.batch_size = settings.dispatcher.batch_size
        if self.batch_interval_seconds is None:
            self.batch_interval_seconds = settings.dispatcher.batch_interval_sec
        if self.max_retries is None:
            self.max_retries = settings.dispatcher.max_retries
        if self.retry_delay_seconds is None:
            self.retry_delay_seconds = settings.dispatcher.retry_delay_sec
        if self.log_to_file is None:
            self.log_to_file = settings.dispatcher.log_to_file
        if self.log_file_path is None:
            self.log_file_path = settings.dispatcher.log_file_path


@dataclass
class DispatchStats:
    """Статистика отправки алертов."""
    sent: int = 0
    failed: int = 0
    queued: int = 0
    batches_sent: int = 0
    last_send_time: Optional[datetime] = None
    last_error: Optional[str] = None


class AlertDispatcher:
    """
    Отправка алертов в Binance API.

    Функции:
    - Батчит алерты для эффективности
    - Подписывает запросы (когда будет API)
    - Логирует локально если API не настроен
    - Retry при ошибках

    Использование:
        config = AlertConfig(binance_api_url="...", api_key="...", api_secret="...")
        dispatcher = AlertDispatcher(config)
        await dispatcher.start()
        await dispatcher.dispatch(detection)
        ...
        await dispatcher.stop()
    """

    def __init__(self, config: AlertConfig):
        self._config = config
        self._queue: asyncio.Queue[Detection] = asyncio.Queue()
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._batch_task: Optional[asyncio.Task] = None
        self._stats = DispatchStats()
        self._log_file = None

    async def start(self):
        """Запустить диспетчер."""
        self._running = True
        self._session = aiohttp.ClientSession()

        # Открыть лог файл
        if self._config.log_to_file:
            try:
                import os
                os.makedirs(os.path.dirname(self._config.log_file_path), exist_ok=True)
                self._log_file = open(self._config.log_file_path, "a", encoding="utf-8")
            except Exception as e:
                logger.warning("failed_to_open_log_file", error=str(e))

        # Запустить батч sender
        self._batch_task = asyncio.create_task(self._batch_sender())

        logger.info(
            "alert_dispatcher_started",
            api_configured=bool(self._config.binance_api_url),
            log_to_file=self._config.log_to_file,
        )

    async def stop(self):
        """Остановить диспетчер."""
        logger.info("stopping_alert_dispatcher")
        self._running = False

        # Отменить batch task
        if self._batch_task:
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass

        # Закрыть сессию
        if self._session:
            await self._session.close()
            self._session = None

        # Закрыть лог файл
        if self._log_file:
            self._log_file.close()
            self._log_file = None

        logger.info("alert_dispatcher_stopped", stats=self.get_stats())

    async def dispatch(self, detection: Detection):
        """
        Добавить алерт в очередь на отправку.

        Args:
            detection: Детекция для отправки
        """
        # Фильтр по severity
        if detection.severity.value < self._config.min_severity.value:
            return

        await self._queue.put(detection)
        self._stats.queued += 1

    def get_stats(self) -> dict:
        """Получить статистику."""
        return {
            "sent": self._stats.sent,
            "failed": self._stats.failed,
            "queued": self._stats.queued,
            "batches_sent": self._stats.batches_sent,
            "last_send_time": self._stats.last_send_time.isoformat() if self._stats.last_send_time else None,
            "last_error": self._stats.last_error,
        }

    async def _batch_sender(self):
        """Фоновая задача: батчевая отправка алертов."""
        while self._running:
            batch: list[Detection] = []

            try:
                # Собираем батч
                while len(batch) < self._config.batch_size:
                    try:
                        detection = await asyncio.wait_for(
                            self._queue.get(),
                            timeout=self._config.batch_interval_seconds
                        )
                        batch.append(detection)
                    except asyncio.TimeoutError:
                        break

                if batch:
                    await self._send_batch(batch)

            except asyncio.CancelledError:
                # Отправить оставшееся перед выходом
                if batch:
                    await self._send_batch(batch)
                break
            except Exception as e:
                logger.error("batch_sender_error", error=str(e))
                self._stats.last_error = str(e)
                await asyncio.sleep(1)

    async def _send_batch(self, detections: list[Detection]):
        """Отправить батч алертов."""
        # Логируем локально
        if self._log_file:
            for d in detections:
                try:
                    payload = d.to_alert_payload()
                    # Add cross-exchange flag
                    if d.detection_type.startswith("CROSS_"):
                        payload["is_cross_exchange"] = True
                        payload["exchanges"] = d.details.get("exchanges", [])
                    line = json.dumps(payload, ensure_ascii=False)
                    self._log_file.write(line + "\n")
                    self._log_file.flush()
                except Exception as e:
                    logger.warning("failed_to_write_log", error=str(e))

        # Отправляем в API (если настроен)
        if self._config.binance_api_url and self._config.api_key:
            success = await self._send_to_api(detections)
            if success:
                self._stats.sent += len(detections)
                self._stats.batches_sent += 1
                self._stats.last_send_time = datetime.now()
            else:
                self._stats.failed += len(detections)
        else:
            # Только локальное логирование
            self._stats.sent += len(detections)
            logger.info(
                "alerts_logged_locally",
                count=len(detections),
                symbols=[d.symbol for d in detections]
            )

    async def _send_to_api(self, detections: list[Detection]) -> bool:
        """
        Отправить алерты в Binance API.

        Returns:
            True если успешно, False при ошибке
        """
        payload = {
            "alerts": [d.to_alert_payload() for d in detections],
            "source": "binancefriend_screener_v1",
            "timestamp": datetime.now().isoformat(),
            "count": len(detections),
        }

        # Подпись запроса
        timestamp = int(time.time() * 1000)
        payload_str = json.dumps(payload, sort_keys=True)
        signature = self._sign_request(payload_str, timestamp)

        headers = {
            "X-API-KEY": self._config.api_key,
            "X-TIMESTAMP": str(timestamp),
            "X-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

        for attempt in range(self._config.max_retries):
            try:
                # TG-3 FIX: Используем ClientTimeout для корректной работы
                async with self._session.post(
                    self._config.binance_api_url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        logger.info("alerts_sent_to_api", count=len(detections))
                        return True
                    else:
                        error = await resp.text()
                        logger.warning(
                            "api_error",
                            status=resp.status,
                            error=error,
                            attempt=attempt + 1
                        )
                        self._stats.last_error = f"{resp.status}: {error}"

            except Exception as e:
                logger.warning(
                    "api_request_failed",
                    error=str(e),
                    attempt=attempt + 1
                )
                self._stats.last_error = str(e)

            # Retry delay
            if attempt < self._config.max_retries - 1:
                await asyncio.sleep(self._config.retry_delay_seconds * (attempt + 1))

        return False

    def _sign_request(self, payload: str, timestamp: int) -> str:
        """Подписать запрос HMAC-SHA256."""
        message = f"{timestamp}{payload}"
        signature = hmac.new(
            self._config.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _format_cross_exchange_alert(self, detection: Detection) -> str:
        """
        Format cross-exchange detection for display.

        Args:
            detection: Detection with cross-exchange data

        Returns:
            Formatted alert string
        """
        lines = []

        # Check if this is a cross-exchange detection
        if detection.detection_type.startswith("CROSS_"):
            lines.append("🔄 CROSS-EXCHANGE ALERT")
            lines.append(f"Type: {detection.detection_type}")
            lines.append(f"Symbol: {detection.symbol}")
            lines.append(f"Severity: {detection.severity.name}")
            lines.append(f"Score: {detection.score}%")

            # Extract exchanges
            exchanges = detection.details.get("exchanges", [])
            if exchanges:
                lines.append(f"Exchanges: {', '.join(exchanges)}")

            # Description
            description = detection.details.get("description", "")
            if description:
                lines.append(f"Details: {description}")

            # Recommended action
            action = detection.details.get("recommended_action", "")
            if action:
                lines.append(f"Action: {action}")

        else:
            # Standard detection formatting
            lines.append(f"🚨 ALERT: {detection.symbol}")
            lines.append(f"Type: {detection.detection_type}")
            lines.append(f"Severity: {detection.severity.name}")
            lines.append(f"Score: {detection.score}%")

        return "\n".join(lines)

    def format_alert(self, detection: Detection) -> str:
        """
        Format any detection for display.

        Args:
            detection: Detection to format

        Returns:
            Formatted alert string
        """
        if detection.detection_type.startswith("CROSS_"):
            return self._format_cross_exchange_alert(detection)
        else:
            # Use default formatting
            lines = [
                f"🚨 {detection.detection_type}",
                f"Symbol: {detection.symbol}",
                f"Severity: {detection.severity.name}",
                f"Score: {detection.score}%",
            ]
            for evidence in detection.evidence[:3]:  # Limit to 3 evidences
                lines.append(f"• {evidence}")
            return "\n".join(lines)
