# -*- coding: utf-8 -*-
"""
Хранилище деталей алертов для Telegram callback'ов.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Any
import asyncio
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ExchangeSnapshot:
    """Снимок данных с биржи."""
    exchange: str
    price: float
    volume_5m: float = 0.0
    oi: Optional[float] = None
    oi_change_1h: Optional[float] = None
    funding: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread_pct: Optional[float] = None


@dataclass
class AlertDetails:
    """Детали алерта для развёрнутого отчёта."""
    alert_id: str
    symbol: str
    timestamp: datetime
    detection_type: str
    severity: str
    score: int

    # Сырые данные - ИСПРАВЛЕНО: None вместо дефолтных значений
    # None означает "данные недоступны", не "0 сделок" или "50/50"
    trades_count: Optional[int] = None
    buy_percent: Optional[float] = None
    sell_percent: Optional[float] = None
    volume_5m: float = 0.0
    volume_ratio: float = 1.0

    # Источник детекции: 'spot' или 'futures'
    source: str = "spot"

    # Данные по биржам
    exchange_data: Dict[str, ExchangeSnapshot] = field(default_factory=dict)

    # Признаки манипуляции
    evidence: List[str] = field(default_factory=list)

    # Дополнительные детали
    details: Dict[str, Any] = field(default_factory=dict)

    # TTL
    expires_at: datetime = field(default_factory=lambda: datetime.now() + timedelta(hours=1))


class AlertDetailsStore:
    """
    Хранилище деталей алертов с автоматической очисткой по TTL.
    """

    def __init__(self, ttl_hours: int = 1):
        self._alerts: Dict[str, AlertDetails] = {}
        self._ttl = timedelta(hours=ttl_hours)
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """Запустить фоновую очистку."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("alert_details_store_started")

    async def stop(self):
        """Остановить хранилище."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("alert_details_store_stopped", total_alerts=len(self._alerts))

    def save_alert(self, details: AlertDetails) -> str:
        """
        Сохранить детали алерта.

        Returns:
            alert_id для callback
        """
        details.expires_at = datetime.now() + self._ttl
        self._alerts[details.alert_id] = details
        logger.debug("alert_saved", alert_id=details.alert_id, symbol=details.symbol)
        return details.alert_id

    def get_alert(self, alert_id: str) -> Optional[AlertDetails]:
        """Получить детали алерта по ID."""
        alert = self._alerts.get(alert_id)
        if alert and alert.expires_at > datetime.now():
            return alert
        return None

    def create_alert_from_detection(self, detection, exchange_data: Dict[str, Any] = None) -> AlertDetails:
        """
        Создать AlertDetails из Detection.

        Args:
            detection: объект Detection или FuturesDetection
            exchange_data: данные с бирж (опционально)
        """
        alert_id = str(uuid.uuid4())[:8]

        # Извлечь данные из detection.details
        details_dict = detection.details or {}

        # Определяем источник: futures или spot
        # FuturesDetection имеет типы начинающиеся с определённых паттернов
        futures_types = {
            'WHALE_ACCUMULATION', 'OI_SPIKE', 'MASS_EXIT', 'OI_DROP',
            'FUNDING_EXTREME', 'EXTREME_LONG', 'EXTREME_SHORT',
            'WEAK_PUMP', 'WEAK_DUMP', 'DIVERGENCE', 'GRADIENT',
            'STEALTH', 'POSITIONING'
        }
        is_futures = any(ft in detection.detection_type for ft in futures_types)
        source = "futures" if is_futures else "spot"

        # ИСПРАВЛЕНО: buy/sell ratio - используем None если данных нет
        buy_pct = None
        sell_pct = None

        buy_ratio = details_dict.get('buy_ratio')
        if buy_ratio is not None:
            buy_pct = float(buy_ratio) * 100 if float(buy_ratio) <= 1 else float(buy_ratio)
            sell_pct = 100.0 - buy_pct
        else:
            # Проверяем buy_percent напрямую
            raw_buy = details_dict.get('buy_percent')
            if raw_buy is not None:
                buy_pct = float(raw_buy)
                sell_pct = 100.0 - buy_pct
            # Если нет данных - оставляем None (не 50/50)

        # TG-4 FIX: sell_ratio только если sell_pct ещё не рассчитан из buy_percent
        sell_ratio = details_dict.get('sell_ratio')
        if sell_ratio is not None and sell_pct is None:
            sell_pct = float(sell_ratio) * 100 if float(sell_ratio) <= 1 else float(sell_ratio)

        # ИСПРАВЛЕНО: trades_count - None если данных нет, не 0
        trades_count_raw = details_dict.get('trades_count')
        trades_count = int(trades_count_raw) if trades_count_raw is not None else None

        volume_5m = float(details_dict.get('volume_5m', details_dict.get('volume_usd', 0)) or 0)
        volume_ratio = float(details_dict.get('volume_ratio', details_dict.get('spike_ratio', 1.0)) or 1.0)

        # Округлить Decimal и float значения в details для предотвращения 18 знаков
        cleaned_details = {}
        keys_to_round_2 = {'bid_volume', 'ask_volume', 'volume_5m', 'volume_ratio', 'spike_ratio', 'current_oi_usd', 'oi_usd'}
        keys_to_round_4 = {'imbalance', 'spread_pct', 'buy_ratio', 'sell_ratio', 'book_imbalance'}
        keys_to_round_6 = {'current_price', 'best_bid', 'best_ask', 'mark_price'}

        for key, value in details_dict.items():
            if isinstance(value, Decimal):
                value = float(value)
            if isinstance(value, float):
                if key in keys_to_round_2:
                    cleaned_details[key] = round(value, 2)
                elif key in keys_to_round_4:
                    cleaned_details[key] = round(value, 4)
                elif key in keys_to_round_6:
                    cleaned_details[key] = round(value, 6)
                elif key.endswith('_pct'):
                    cleaned_details[key] = round(value, 2)
                else:
                    # Для остальных float округляем до 4 знаков
                    cleaned_details[key] = round(value, 4) if abs(value) < 1000 else round(value, 2)
            else:
                cleaned_details[key] = value

        alert = AlertDetails(
            alert_id=alert_id,
            symbol=detection.symbol,
            timestamp=detection.timestamp,
            detection_type=detection.detection_type,
            severity=detection.severity.name if hasattr(detection.severity, 'name') else str(detection.severity),
            score=detection.score,
            # ИСПРАВЛЕНО: используем None если данных нет
            trades_count=trades_count,
            buy_percent=round(buy_pct, 1) if buy_pct is not None else None,
            sell_percent=round(sell_pct, 1) if sell_pct is not None else None,
            volume_5m=round(volume_5m, 2),
            volume_ratio=round(volume_ratio, 2),
            evidence=detection.evidence or [],
            details=cleaned_details,
            source=source,
        )

        # Добавить данные бирж если есть
        if exchange_data:
            for ex_name, ex_data in exchange_data.items():
                # ИСПРАВЛЕНО: Funding rate стандартизирован
                # Контракт: входное значение ВСЕГДА raw fraction (0.0001 = 0.01%)
                # Мы конвертируем в проценты для отображения
                funding_raw = ex_data.get('funding')
                funding_pct = None
                if funding_raw is not None:
                    funding_val = float(funding_raw)
                    # Конвертируем в проценты: 0.0001 * 100 = 0.01%
                    funding_pct = funding_val * 100
                    # Валидация: типичный funding rate между -1% и +1%
                    # Если значение за пределами -5% до +5%, логируем предупреждение
                    if abs(funding_pct) > 5.0:
                        logger.warning(
                            "unusual_funding_rate",
                            exchange=ex_name,
                            raw_value=funding_val,
                            percent_value=funding_pct,
                            symbol=detection.symbol
                        )
                        # Ограничиваем до разумного диапазона для отображения
                        funding_pct = max(-5.0, min(5.0, funding_pct))
                    funding_pct = round(funding_pct, 4)

                alert.exchange_data[ex_name] = ExchangeSnapshot(
                    exchange=ex_name,
                    price=round(float(ex_data.get('price', 0) or 0), 6),
                    volume_5m=round(float(ex_data.get('volume_5m', 0) or 0), 2),
                    oi=round(float(ex_data.get('oi') or 0), 2) if ex_data.get('oi') else None,
                    oi_change_1h=round(float(ex_data.get('oi_change_1h') or 0), 2) if ex_data.get('oi_change_1h') else None,
                    funding=funding_pct,
                    bid=round(float(ex_data.get('bid') or 0), 6) if ex_data.get('bid') else None,
                    ask=round(float(ex_data.get('ask') or 0), 6) if ex_data.get('ask') else None,
                    spread_pct=round(float(ex_data.get('spread_pct') or 0), 4) if ex_data.get('spread_pct') else None,
                )

        self.save_alert(alert)
        return alert

    async def _cleanup_loop(self):
        """Фоновая очистка устаревших алертов."""
        while self._running:
            try:
                await asyncio.sleep(300)  # Каждые 5 минут
                self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("cleanup_error", error=str(e))

    def _cleanup_expired(self):
        """Удалить устаревшие алерты."""
        now = datetime.now()
        expired = [
            alert_id for alert_id, alert in self._alerts.items()
            if alert.expires_at <= now
        ]
        for alert_id in expired:
            del self._alerts[alert_id]

        if expired:
            logger.debug("alerts_cleaned", count=len(expired), remaining=len(self._alerts))

    def get_stats(self) -> dict:
        """Статистика хранилища."""
        return {
            "total_alerts": len(self._alerts),
            "oldest": min((a.timestamp for a in self._alerts.values()), default=None),
        }


# Глобальный экземпляр
_store: Optional[AlertDetailsStore] = None


def get_alert_store() -> AlertDetailsStore:
    """Получить глобальное хранилище."""
    global _store
    if _store is None:
        _store = AlertDetailsStore()
    return _store
