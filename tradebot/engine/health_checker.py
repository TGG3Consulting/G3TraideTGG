# -*- coding: utf-8 -*-
"""
Health Checker - Мониторинг состояния бота.

Позволяет понять жив ли бот и работает ли корректно:
- Периодический heartbeat (файл + timestamp)
- Статистика работы
- Проверка критических компонентов
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Статус здоровья бота."""
    is_healthy: bool
    timestamp: datetime
    uptime_seconds: float
    last_cycle_time: Optional[datetime]
    cycles_completed: int
    websocket_connected: bool
    open_positions_count: int
    errors_last_hour: int
    circuit_breaker_state: str
    details: Dict[str, Any] = field(default_factory=dict)


class HealthChecker:
    """
    Health Checker для мониторинга состояния TradeBot.

    Функции:
    1. Периодический heartbeat файл (для внешнего мониторинга)
    2. Статистика работы (cycles, errors, latency)
    3. Проверка критических компонентов (WS, circuit breaker)
    4. Метод get_health() для программной проверки
    """

    def __init__(
        self,
        heartbeat_file: str = "tradebot_heartbeat.json",
        heartbeat_interval: int = 30,  # секунд
        max_cycle_age: int = 600,      # 10 минут - если дольше, бот завис
    ):
        """
        Args:
            heartbeat_file: Путь к файлу heartbeat
            heartbeat_interval: Интервал обновления heartbeat (секунд)
            max_cycle_age: Максимальный возраст последнего цикла (секунд)
        """
        self._heartbeat_file = Path(heartbeat_file)
        self._heartbeat_interval = heartbeat_interval
        self._max_cycle_age = max_cycle_age

        self._started_at: Optional[datetime] = None
        self._last_cycle_time: Optional[datetime] = None
        self._cycles_completed: int = 0

        # Счётчики ошибок (за последний час)
        self._errors: List[datetime] = []

        # Внешние статусы (обновляются извне)
        self._websocket_connected: bool = False
        self._circuit_breaker_state: str = "CLOSED"
        self._open_positions_count: int = 0

        # Background task
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running: bool = False

        # Callbacks для получения актуальных данных
        self._get_positions_count: Optional[Callable[[], int]] = None
        self._get_circuit_state: Optional[Callable[[], str]] = None
        self._get_ws_connected: Optional[Callable[[], bool]] = None

    def start(self) -> None:
        """Запустить health checker."""
        self._started_at = datetime.now(timezone.utc)
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"HealthChecker started (heartbeat every {self._heartbeat_interval}s)")

    async def stop(self) -> None:
        """Остановить health checker."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Удаляем heartbeat файл при остановке
        if self._heartbeat_file.exists():
            try:
                self._heartbeat_file.unlink()
            except Exception:
                pass

        logger.info("HealthChecker stopped")

    def set_callbacks(
        self,
        get_positions_count: Optional[Callable[[], int]] = None,
        get_circuit_state: Optional[Callable[[], str]] = None,
        get_ws_connected: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Установить callbacks для получения актуальных данных."""
        self._get_positions_count = get_positions_count
        self._get_circuit_state = get_circuit_state
        self._get_ws_connected = get_ws_connected

    def record_cycle_completed(self) -> None:
        """Записать завершение торгового цикла."""
        self._last_cycle_time = datetime.now(timezone.utc)
        self._cycles_completed += 1

    def record_error(self) -> None:
        """Записать ошибку."""
        self._errors.append(datetime.now(timezone.utc))
        # Очищаем старые (старше часа)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        self._errors = [e for e in self._errors if e >= cutoff]

    def update_websocket_status(self, connected: bool) -> None:
        """Обновить статус WebSocket."""
        self._websocket_connected = connected

    def update_circuit_state(self, state: str) -> None:
        """Обновить состояние circuit breaker."""
        self._circuit_breaker_state = state

    def update_positions_count(self, count: int) -> None:
        """Обновить количество открытых позиций."""
        self._open_positions_count = count

    def get_health(self) -> HealthStatus:
        """
        Получить текущий статус здоровья.

        Returns:
            HealthStatus с полной информацией
        """
        now = datetime.now(timezone.utc)

        # Обновляем данные через callbacks если есть
        if self._get_positions_count:
            try:
                self._open_positions_count = self._get_positions_count()
            except Exception:
                pass

        if self._get_circuit_state:
            try:
                self._circuit_breaker_state = self._get_circuit_state()
            except Exception:
                pass

        if self._get_ws_connected:
            try:
                self._websocket_connected = self._get_ws_connected()
            except Exception:
                pass

        # Подсчёт ошибок за час
        cutoff = now - timedelta(hours=1)
        errors_last_hour = sum(1 for e in self._errors if e >= cutoff)

        # Uptime
        uptime = (now - self._started_at).total_seconds() if self._started_at else 0

        # Проверка здоровья
        is_healthy = self._check_is_healthy(now, errors_last_hour)

        return HealthStatus(
            is_healthy=is_healthy,
            timestamp=now,
            uptime_seconds=uptime,
            last_cycle_time=self._last_cycle_time,
            cycles_completed=self._cycles_completed,
            websocket_connected=self._websocket_connected,
            open_positions_count=self._open_positions_count,
            errors_last_hour=errors_last_hour,
            circuit_breaker_state=self._circuit_breaker_state,
            details={
                "heartbeat_file": str(self._heartbeat_file),
                "max_cycle_age": self._max_cycle_age,
                "started_at": self._started_at.isoformat() if self._started_at else None,
            }
        )

    def _check_is_healthy(self, now: datetime, errors_last_hour: int) -> bool:
        """Проверить здоров ли бот."""
        # Circuit breaker открыт - нездоров
        if self._circuit_breaker_state == "OPEN":
            return False

        # Слишком много ошибок
        if errors_last_hour > 50:
            return False

        # Последний цикл слишком давно (бот завис?)
        if self._last_cycle_time:
            cycle_age = (now - self._last_cycle_time).total_seconds()
            if cycle_age > self._max_cycle_age:
                return False

        return True

    async def _heartbeat_loop(self) -> None:
        """Background loop для обновления heartbeat файла."""
        while self._running:
            try:
                await self._write_heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat write failed: {e}")

            await asyncio.sleep(self._heartbeat_interval)

    async def _write_heartbeat(self) -> None:
        """Записать heartbeat файл."""
        health = self.get_health()

        data = {
            "timestamp": health.timestamp.isoformat(),
            "is_healthy": health.is_healthy,
            "uptime_seconds": health.uptime_seconds,
            "last_cycle_time": health.last_cycle_time.isoformat() if health.last_cycle_time else None,
            "cycles_completed": health.cycles_completed,
            "websocket_connected": health.websocket_connected,
            "open_positions_count": health.open_positions_count,
            "errors_last_hour": health.errors_last_hour,
            "circuit_breaker_state": health.circuit_breaker_state,
        }

        # Атомарная запись (write to temp, then rename)
        temp_file = self._heartbeat_file.with_suffix(".tmp")
        try:
            temp_file.write_text(json.dumps(data, indent=2))
            temp_file.replace(self._heartbeat_file)
        except Exception as e:
            logger.error(f"Failed to write heartbeat: {e}")
            if temp_file.exists():
                temp_file.unlink()


def check_bot_health(heartbeat_file: str = "tradebot_heartbeat.json") -> Optional[Dict]:
    """
    Внешняя проверка здоровья бота (для мониторинга).

    Читает heartbeat файл и возвращает статус.

    Returns:
        Dict с данными или None если файл не найден/устарел
    """
    path = Path(heartbeat_file)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())

        # Проверяем не устарел ли heartbeat (>5 минут)
        timestamp = datetime.fromisoformat(data["timestamp"])
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()

        if age > 300:  # 5 минут
            data["is_healthy"] = False
            data["health_warning"] = f"Heartbeat is {int(age)}s old"

        return data

    except Exception as e:
        return {"error": str(e), "is_healthy": False}
