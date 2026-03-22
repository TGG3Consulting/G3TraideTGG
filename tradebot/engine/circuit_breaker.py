# -*- coding: utf-8 -*-
"""
Circuit Breaker - Автоматическая остановка при критических ошибках.

При серии критических ошибок бот должен остановиться, а не продолжать
торговать в неисправном состоянии.

Состояния:
- CLOSED: Нормальная работа (circuit замкнут, ток течёт)
- OPEN: Остановлен (circuit разомкнут, защита сработала)
- HALF_OPEN: Тестовый режим после cooldown
"""

import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from threading import Lock
from typing import Optional, Callable, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Состояние Circuit Breaker."""
    CLOSED = "CLOSED"      # Нормальная работа
    OPEN = "OPEN"          # Остановлен (защита сработала)
    HALF_OPEN = "HALF_OPEN"  # Тестовый режим


class ErrorSeverity(Enum):
    """Серьёзность ошибки."""
    WARNING = "WARNING"     # Не влияет на circuit breaker
    ERROR = "ERROR"         # Считается, но не критична
    CRITICAL = "CRITICAL"   # Критическая - сразу или быстро открывает circuit


@dataclass
class CircuitError:
    """Записанная ошибка."""
    timestamp: datetime
    severity: ErrorSeverity
    error_type: str
    message: str


class CircuitBreaker:
    """
    Circuit Breaker для защиты торгового бота.

    При накоплении критических ошибок переходит в OPEN состояние
    и останавливает торговлю.

    Критические ошибки:
    - AUTH_ERROR: Проблема с API ключами
    - IP_BAN: IP заблокирован
    - RATE_LIMIT_SEVERE: Жёсткое превышение лимитов
    - SYSTEM_ERROR: Системные ошибки (out of memory, etc.)
    """

    # Типы критических ошибок
    CRITICAL_ERROR_TYPES = {
        "AUTH_ERROR",
        "IP_BAN",
        "INVALID_API_KEY",
        "SIGNATURE_ERROR",
        "RATE_LIMIT_SEVERE",
        "SYSTEM_ERROR",
        "WEBSOCKET_FATAL",
    }

    def __init__(
        self,
        failure_threshold: int = 3,      # Ошибок для OPEN
        critical_threshold: int = 1,     # Критических для мгновенного OPEN
        cooldown_seconds: int = 300,     # 5 минут cooldown
        error_window_seconds: int = 60,  # Окно для подсчёта ошибок
        on_circuit_open: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            failure_threshold: Количество ошибок за окно для срабатывания
            critical_threshold: Количество критических ошибок для мгновенного срабатывания
            cooldown_seconds: Время до перехода в HALF_OPEN
            error_window_seconds: Окно для подсчёта ошибок
            on_circuit_open: Callback при открытии circuit (для уведомлений)
        """
        self._state = CircuitState.CLOSED
        self._lock = Lock()

        self._failure_threshold = failure_threshold
        self._critical_threshold = critical_threshold
        self._cooldown_seconds = cooldown_seconds
        self._error_window_seconds = error_window_seconds

        self._errors: List[CircuitError] = []
        self._last_failure_time: Optional[datetime] = None
        self._opened_at: Optional[datetime] = None
        self._open_reason: str = ""

        self._on_circuit_open = on_circuit_open

        # Статистика
        self._stats = {
            "total_errors": 0,
            "critical_errors": 0,
            "circuit_opens": 0,
            "last_open_time": None,
            "last_open_reason": "",
        }

    @property
    def state(self) -> CircuitState:
        """Текущее состояние."""
        with self._lock:
            self._check_cooldown()
            return self._state

    @property
    def is_closed(self) -> bool:
        """True если circuit замкнут (нормальная работа)."""
        return self.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """True если circuit разомкнут (остановлен)."""
        return self.state == CircuitState.OPEN

    def _check_cooldown(self) -> None:
        """Проверить не пора ли перейти в HALF_OPEN."""
        if self._state != CircuitState.OPEN:
            return

        if self._opened_at is None:
            return

        elapsed = datetime.now(timezone.utc) - self._opened_at
        if elapsed >= timedelta(seconds=self._cooldown_seconds):
            self._state = CircuitState.HALF_OPEN
            logger.info(
                f"Circuit Breaker: OPEN -> HALF_OPEN after {self._cooldown_seconds}s cooldown"
            )

    def record_error(
        self,
        error_type: str,
        message: str,
        severity: ErrorSeverity = ErrorSeverity.ERROR,
    ) -> bool:
        """
        Записать ошибку.

        Args:
            error_type: Тип ошибки (AUTH_ERROR, RATE_LIMIT, etc.)
            message: Сообщение об ошибке
            severity: Серьёзность

        Returns:
            True если circuit остался замкнут
            False если circuit открылся
        """
        with self._lock:
            now = datetime.now(timezone.utc)

            # Определяем серьёзность автоматически для известных типов
            if error_type in self.CRITICAL_ERROR_TYPES:
                severity = ErrorSeverity.CRITICAL

            error = CircuitError(
                timestamp=now,
                severity=severity,
                error_type=error_type,
                message=message,
            )
            self._errors.append(error)

            # Обновляем статистику
            self._stats["total_errors"] += 1
            if severity == ErrorSeverity.CRITICAL:
                self._stats["critical_errors"] += 1

            # Очищаем старые ошибки
            cutoff = now - timedelta(seconds=self._error_window_seconds)
            self._errors = [e for e in self._errors if e.timestamp >= cutoff]

            # Проверяем нужно ли открыть circuit
            if self._should_open_circuit():
                self._open_circuit(f"{error_type}: {message}")
                return False

            return True

    def _should_open_circuit(self) -> bool:
        """Проверить нужно ли открыть circuit."""
        if self._state == CircuitState.OPEN:
            return False  # Уже открыт

        # Считаем критические ошибки
        critical_count = sum(
            1 for e in self._errors if e.severity == ErrorSeverity.CRITICAL
        )
        if critical_count >= self._critical_threshold:
            return True

        # Считаем все ошибки (ERROR + CRITICAL)
        error_count = sum(
            1 for e in self._errors
            if e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL)
        )
        if error_count >= self._failure_threshold:
            return True

        return False

    def _open_circuit(self, reason: str) -> None:
        """Открыть circuit (остановить работу)."""
        self._state = CircuitState.OPEN
        self._opened_at = datetime.now(timezone.utc)
        self._open_reason = reason

        self._stats["circuit_opens"] += 1
        self._stats["last_open_time"] = self._opened_at.isoformat()
        self._stats["last_open_reason"] = reason

        logger.critical(
            f"Circuit Breaker OPENED: {reason}. "
            f"Trading stopped. Cooldown: {self._cooldown_seconds}s"
        )

        # Callback для уведомления
        if self._on_circuit_open:
            try:
                self._on_circuit_open(reason)
            except Exception as e:
                logger.error(f"Circuit open callback failed: {e}")

    def record_success(self) -> None:
        """Записать успешную операцию (для HALF_OPEN -> CLOSED)."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._errors.clear()
                logger.info("Circuit Breaker: HALF_OPEN -> CLOSED (success)")

    def force_open(self, reason: str) -> None:
        """Принудительно открыть circuit."""
        with self._lock:
            self._open_circuit(f"FORCED: {reason}")

    def force_close(self) -> None:
        """Принудительно закрыть circuit (сбросить защиту)."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._errors.clear()
            self._opened_at = None
            self._open_reason = ""
            logger.warning("Circuit Breaker FORCE CLOSED by manual reset")

    def get_stats(self) -> dict:
        """Получить статистику."""
        with self._lock:
            return {
                **self._stats,
                "state": self._state.value,
                "errors_in_window": len(self._errors),
                "opened_at": self._opened_at.isoformat() if self._opened_at else None,
                "open_reason": self._open_reason,
            }
