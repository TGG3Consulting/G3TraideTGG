# -*- coding: utf-8 -*-
"""
Логгер торговых сигналов для бектестинга.

Сохраняет максимально богатую информацию о каждом сигнале:
- Сам сигнал со всеми параметрами
- Состояние рынка в момент сигнала
- Детекции, которые привели к сигналу
- Конфигурация, использованная для генерации
"""

import json
import os
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Optional, Any, TYPE_CHECKING
import structlog

from .models import TradeSignal, AccumulationScore

if TYPE_CHECKING:
    from src.screener.futures_monitor import FuturesState
    from src.screener.models import SymbolState, Detection
    from src.cross_exchange.state_store import StateStore


logger = structlog.get_logger(__name__)


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder для Decimal и datetime."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


class SignalLogger:
    """
    Логгер торговых сигналов в JSONL формат.

    Каждая строка в файле - полный снимок состояния рынка
    в момент генерации сигнала + сам сигнал.
    """

    def __init__(self, log_path: str = "logs/signals.jsonl"):
        """
        Args:
            log_path: Путь к файлу логов
        """
        self._log_path = Path(log_path)
        self._file = None
        self._started = False

    def start(self) -> None:
        """Открыть файл для записи."""
        try:
            logger.info("signal_logger_starting", path=str(self._log_path))
            os.makedirs(self._log_path.parent, exist_ok=True)
            # FIX-B-4: ротация при превышении 100MB
            _log_path = str(self._log_path)
            if os.path.exists(_log_path) and os.path.getsize(_log_path) > 100 * 1024 * 1024:
                _backup = _log_path.replace(".jsonl", "_backup.jsonl")
                if os.path.exists(_backup):
                    os.remove(_backup)
                os.rename(_log_path, _backup)
            self._file = open(self._log_path, "a", encoding="utf-8")
            self._started = True
            logger.info("signal_logger_started", path=str(self._log_path), started=self._started)
        except Exception as e:
            logger.error("signal_logger_start_failed", error=str(e), path=str(self._log_path))
            self._started = False

    def stop(self) -> None:
        """Закрыть файл."""
        if self._file:
            self._file.close()
            self._file = None
        self._started = False
        logger.info("signal_logger_stopped")

    def log_signal(
        self,
        signal: TradeSignal,
        futures_state: Optional["FuturesState"] = None,
        spot_state: Optional["SymbolState"] = None,
        state_store: Optional["StateStore"] = None,
        trigger_detection: Optional["Detection"] = None,
        accumulation_score: Optional[AccumulationScore] = None,
        config_snapshot: Optional[dict] = None,
    ) -> bool:
        """
        Записать сигнал с полным контекстом.

        Args:
            signal: Торговый сигнал
            futures_state: Состояние фьючерсов
            spot_state: Состояние спота
            state_store: Кросс-биржевые данные
            trigger_detection: Детекция-триггер
            accumulation_score: Детальный скор накопления
            config_snapshot: Конфигурация генератора

        Returns:
            True если сигнал записан успешно, False если ошибка
        """
        if not self._started or not self._file:
            logger.error(
                "signal_logger_not_started",
                started=self._started,
                file_open=bool(self._file),
                signal_id=signal.signal_id,
                symbol=signal.symbol,
            )
            return False

        try:
            record = self._build_record(
                signal=signal,
                futures_state=futures_state,
                spot_state=spot_state,
                state_store=state_store,
                trigger_detection=trigger_detection,
                accumulation_score=accumulation_score,
                config_snapshot=config_snapshot,
            )

            line = json.dumps(record, cls=DecimalEncoder, ensure_ascii=False)
            self._file.write(line + "\n")
            self._file.flush()

            logger.info(
                "signal_logged",
                signal_id=signal.signal_id,
                symbol=signal.symbol,
            )
            return True

        except Exception as e:
            logger.error(
                "signal_log_failed",
                error=str(e),
                signal_id=signal.signal_id,
                symbol=signal.symbol,
            )
            return False

    def _build_record(
        self,
        signal: TradeSignal,
        futures_state: Optional["FuturesState"],
        spot_state: Optional["SymbolState"],
        state_store: Optional["StateStore"],
        trigger_detection: Optional["Detection"],
        accumulation_score: Optional[AccumulationScore],
        config_snapshot: Optional[dict],
    ) -> dict:
        """Собрать полную запись для логирования."""
        record = {
            # Мета
            "log_version": "1.0",
            "logged_at": datetime.now().isoformat(),

            # Сигнал
            "signal": signal.to_dict(),

            # Дополнительные поля сигнала
            "signal_extra": {
                "links": signal.links,
            },
        }

        # Скор накопления (детальная разбивка)
        if accumulation_score:
            record["accumulation_score"] = accumulation_score.to_dict()

        # Состояние фьючерсов
        if futures_state:
            record["futures_snapshot"] = self._snapshot_futures(futures_state)

        # Состояние спота
        if spot_state:
            record["spot_snapshot"] = self._snapshot_spot(spot_state)

        # Кросс-биржевые данные
        if state_store:
            record["cross_exchange"] = self._snapshot_cross_exchange(
                state_store, signal.symbol
            )

        # Детекция-триггер
        if trigger_detection:
            record["trigger_detection"] = self._snapshot_detection(trigger_detection)

        # Конфигурация
        if config_snapshot:
            record["config"] = config_snapshot

        return record

    def _snapshot_futures(self, state: "FuturesState") -> dict:
        """Снимок состояния фьючерсов."""
        snapshot = {
            "symbol": state.symbol,
            "last_update": state.last_update.isoformat() if state.last_update else None,

            # OI
            "oi": None,
            "oi_changes": {
                "1m_pct": float(state.oi_change_1m_pct),
                "5m_pct": float(state.oi_change_5m_pct),
                "1h_pct": float(state.oi_change_1h_pct),
            },

            # Funding
            "funding": None,

            # Long/Short ratio
            "ls_ratio": None,

            # Price changes
            "price_changes": {
                "5m_pct": float(state.price_change_5m_pct),
                "1h_pct": float(state.price_change_1h_pct),
            },

            # История OI (последние 12 точек = 1 час)
            "oi_history": [],

            # История funding (последние 8 = 1 день)
            "funding_history": [],
        }

        # Текущий OI
        if state.current_oi:
            snapshot["oi"] = {
                "value": float(state.current_oi.open_interest),
                "value_usd": float(state.current_oi.open_interest_usd),
                "timestamp": state.current_oi.timestamp.isoformat(),
            }

        # Текущий funding
        if state.current_funding:
            f = state.current_funding
            snapshot["funding"] = {
                "rate": float(f.funding_rate),
                "rate_pct": float(f.funding_rate_percent),
                "mark_price": float(f.mark_price),
                "funding_time": f.funding_time,
            }

        # Текущий L/S ratio
        if state.current_ls_ratio:
            ls = state.current_ls_ratio
            snapshot["ls_ratio"] = {
                "long_account_pct": float(ls.long_account_pct),
                "short_account_pct": float(ls.short_account_pct),
                "long_short_ratio": float(ls.long_short_ratio),
                "timestamp": ls.timestamp.isoformat(),
            }

        # История OI (для анализа трендов)
        for oi in state.oi_history[-12:]:
            snapshot["oi_history"].append({
                "value": float(oi.open_interest),
                "timestamp": oi.timestamp.isoformat(),
            })

        # История funding
        for f in state.funding_history[-8:]:
            snapshot["funding_history"].append({
                "rate_pct": float(f.funding_rate_percent),
                "timestamp": f.timestamp.isoformat(),
            })

        return snapshot

    def _snapshot_spot(self, state: "SymbolState") -> dict:
        """Снимок состояния спота."""
        return {
            "symbol": state.symbol,
            "last_update": state.last_update.isoformat() if state.last_update else None,

            # Цены
            "price": {
                "last": float(state.last_price),
                "bid": float(state.best_bid),
                "ask": float(state.best_ask),
                "mid": float(state.mid_price),
                "spread_pct": float(state.spread_pct),
            },

            # Изменения цены
            "price_changes": {
                "1m_pct": float(state.price_change_1m_pct),
                "5m_pct": float(state.price_change_5m_pct),
                "1h_pct": float(state.price_change_1h_pct),
            },

            # Объёмы
            "volume": {
                "1m": float(state.volume_1m),
                "5m": float(state.volume_5m),
                "1h": float(state.volume_1h),
                "avg_1h": float(state.avg_volume_1h),
                "spike_ratio": float(state.volume_spike_ratio),
            },

            # Orderbook
            "orderbook": {
                "bid_volume_20": float(state.bid_volume_20),
                "ask_volume_20": float(state.ask_volume_20),
                "imbalance": float(state.book_imbalance),
            },

            # Трейды
            "trades": {
                "count_1m": state.trade_count_1m,
                "count_5m": state.trade_count_5m,
                "buy_ratio_5m": float(state.buy_ratio_5m) if state.buy_ratio_5m else None,
            },

            # История цен (последние 60 точек)
            "price_history": [float(p) for p in state.price_history[-60:]],
        }

    def _snapshot_cross_exchange(
        self,
        state_store: "StateStore",
        symbol: str
    ) -> dict:
        """Снимок кросс-биржевых данных."""
        snapshot = {
            "exchanges": [],
            "price_spread": {},
            "funding_divergence": {},
            "oi_distribution": {},
        }

        try:
            # Доступные биржи
            snapshot["exchanges"] = state_store.get_exchanges_for_symbol(symbol) or []

            # Спред цен между биржами
            price_spread = state_store.get_price_spread(symbol)
            if price_spread:
                snapshot["price_spread"] = {k: float(v) for k, v in price_spread.items()}

            # Дивергенция funding
            funding_div = state_store.get_funding_divergence(symbol)
            if funding_div:
                snapshot["funding_divergence"] = {
                    k: float(v) if isinstance(v, (Decimal, float)) else v
                    for k, v in funding_div.items()
                }

            # Распределение OI по биржам
            oi_dist = state_store.get_oi_distribution(symbol)
            if oi_dist:
                snapshot["oi_distribution"] = {
                    k: float(v) if isinstance(v, (Decimal, float)) else v
                    for k, v in oi_dist.items()
                }

        except Exception as e:
            logger.debug("cross_exchange_snapshot_error", error=str(e))

        return snapshot

    def _snapshot_detection(self, detection: "Detection") -> dict:
        """Снимок детекции-триггера."""
        return {
            "type": detection.detection_type,
            "timestamp": detection.timestamp.isoformat(),
            "severity": detection.severity.value if hasattr(detection.severity, 'value') else str(detection.severity),
            "score": detection.score,
            "evidence": detection.evidence[:10],  # Макс 10 пунктов
            "details": self._safe_dict(detection.details),
        }

    def _safe_dict(self, d: Any) -> dict:
        """Преобразовать dict с Decimal в сериализуемый формат."""
        if not isinstance(d, dict):
            return {}

        result = {}
        for k, v in d.items():
            if isinstance(v, Decimal):
                result[k] = float(v)
            elif isinstance(v, Enum):  # FIX-B-1: Enum не сериализуется в JSON
                result[k] = v.value
            elif isinstance(v, datetime):
                result[k] = v.isoformat()
            elif isinstance(v, dict):
                result[k] = self._safe_dict(v)
            elif isinstance(v, (list, tuple)):
                result[k] = [
                    float(x) if isinstance(x, Decimal) else x
                    for x in v
                ]
            else:
                result[k] = v
        return result
