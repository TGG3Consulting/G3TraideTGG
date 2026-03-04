# -*- coding: utf-8 -*-
"""
Detection Engine - движок детекции манипуляций.

Пороги загружаются из config/settings.py (settings.spot.*)
"""

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
import structlog

from config.settings import settings
from .models import (
    SymbolState,
    Detection,
    AlertSeverity,
    VolumeBaseline,
)


logger = structlog.get_logger(__name__)


class DetectionEngine:
    """
    Движок детекции манипуляций.

    Анализирует состояние пар и выявляет подозрительную активность:
    - Volume spikes (аномальный объём)
    - Price velocity (слишком быстрое движение цены)
    - Order book manipulation (перекос стакана, spoofing)
    - Trade patterns (wash trading, coordinated buying)
    - Pump sequences (комбинация факторов)

    Использование:
        engine = DetectionEngine()
        detections = engine.analyze(symbol_state)
    """

    # ═══════════════════════════════════════════════════════════════════
    # ПОРОГИ ЗАГРУЖАЮТСЯ ИЗ config/settings.py (settings.spot.*)
    # Для изменения — редактировать config/config.yaml
    # ═══════════════════════════════════════════════════════════════════

    # =========================================================================
    # SMART DEDUPLICATION
    # - Полный дубль (все параметры 1 в 1) → 5 минут
    # - Тот же тип, но другие параметры → 3 секунды
    # =========================================================================

    DEDUP_EXACT_MATCH_SEC = 300   # 5 минут для полного дубля
    DEDUP_SAME_TYPE_SEC = 3       # 3 секунды для того же типа с другими параметрами

    def __init__(self, historical_baselines: Optional[dict[str, VolumeBaseline]] = None):
        """
        Args:
            historical_baselines: Исторические базлайны для точной детекции
        """
        self._baselines = historical_baselines or {}
        # {(symbol, detection_type): (timestamp, fingerprint)}
        self._recent_detections: dict[tuple[str, str], tuple[datetime, str]] = {}

    def analyze(self, state: SymbolState) -> list[Detection]:
        """
        Полный анализ состояния пары.

        Args:
            state: Текущее состояние пары

        Returns:
            Список детекций (может быть пустым если всё нормально)
        """
        detections = []

        # Запуск всех детекторов
        detections.extend(self._detect_volume_spike(state))
        detections.extend(self._detect_price_velocity(state))
        detections.extend(self._detect_orderbook_manipulation(state))
        detections.extend(self._detect_trade_patterns(state))
        detections.extend(self._detect_pump_sequence(state))

        # Фильтрация дубликатов
        detections = self._deduplicate(state.symbol, detections)

        # Обогатить детекции общими данными из state
        for det in detections:
            self._enrich_detection(det, state)

        if detections:
            logger.info(
                "detections_found",
                symbol=state.symbol,
                count=len(detections),
                types=[d.detection_type for d in detections]
            )

        return detections

    def _enrich_detection(self, detection: Detection, state: SymbolState) -> None:
        """Добавить общие данные из SymbolState в Detection."""
        details = detection.details

        # ========== КЛЮЧЕВЫЕ МЕТРИКИ (ВСЕГДА ДОБАВЛЯЕМ) ==========

        # Количество сделок
        if 'trades_count' not in details:
            details['trades_count'] = len(state.trades_5m)

        # Buy/Sell ratio - ИСПРАВЛЕНО: обрабатываем None
        buy_ratio_raw = state.buy_ratio_5m
        if buy_ratio_raw is not None:
            buy_ratio = float(buy_ratio_raw)
            if 'buy_ratio' not in details:
                details['buy_ratio'] = round(buy_ratio, 4)
            if 'sell_ratio' not in details:
                details['sell_ratio'] = round(1 - buy_ratio, 4)
        else:
            # Нет данных о сделках - не устанавливаем дефолтные 50%
            if 'buy_ratio' not in details:
                details['buy_ratio'] = None
            if 'sell_ratio' not in details:
                details['sell_ratio'] = None

        # Объём
        if 'volume_5m' not in details:
            details['volume_5m'] = round(float(state.volume_5m), 2)

        if 'volume_ratio' not in details and state.volume_spike_ratio > 0:
            details['volume_ratio'] = round(float(state.volume_spike_ratio), 2)

        # Цена
        if 'current_price' not in details and state.last_price > 0:
            details['current_price'] = round(float(state.last_price), 6)

        if 'price_change_5m_pct' not in details:
            details['price_change_5m_pct'] = round(float(state.price_change_5m_pct), 2)

        # Стакан (ATR-based)
        if 'bid_volume_atr' not in details and state.bid_volume_atr > 0:
            details['bid_volume_atr'] = round(float(state.bid_volume_atr), 2)
        if 'ask_volume_atr' not in details and state.ask_volume_atr > 0:
            details['ask_volume_atr'] = round(float(state.ask_volume_atr), 2)
        if 'imbalance' not in details and (state.bid_volume_atr + state.ask_volume_atr) > 0:
            # FIX-IMBALANCE-1: None = не добавляем в details
            _imb = state.book_imbalance_atr
            if _imb is not None:
                details['imbalance'] = round(float(_imb), 4)
        if 'atr_pct' not in details and state.atr_1h_pct > 0:
            details['atr_pct'] = round(float(state.atr_1h_pct), 2)
        if 'spread_pct' not in details and state.spread_pct > 0:
            details['spread_pct'] = round(float(state.spread_pct), 4)
        # Legacy стакан для совместимости
        if 'bid_volume' not in details and state.bid_volume_20 > 0:
            details['bid_volume'] = round(float(state.bid_volume_20), 2)
        if 'ask_volume' not in details and state.ask_volume_20 > 0:
            details['ask_volume'] = round(float(state.ask_volume_20), 2)

        # ========== ОКРУГЛЕНИЕ СУЩЕСТВУЮЩИХ ЗНАЧЕНИЙ ==========

        keys_to_round_2 = ['bid_volume', 'ask_volume', 'volume_5m', 'volume_ratio', 'spike_ratio']
        keys_to_round_4 = ['imbalance', 'spread_pct', 'buy_ratio', 'sell_ratio']
        keys_to_round_6 = ['current_price', 'best_bid', 'best_ask']

        for key in keys_to_round_2:
            if key in details and isinstance(details[key], (Decimal, float)):
                details[key] = round(float(details[key]), 2)

        for key in keys_to_round_4:
            if key in details and isinstance(details[key], (Decimal, float)):
                details[key] = round(float(details[key]), 4)

        for key in keys_to_round_6:
            if key in details and isinstance(details[key], (Decimal, float)):
                details[key] = round(float(details[key]), 6)

        # Конвертируем оставшиеся Decimal в float
        for key, value in list(details.items()):
            if isinstance(value, Decimal):
                details[key] = round(float(value), 6)

    def _detect_volume_spike(self, state: SymbolState) -> list[Detection]:
        """Детекция аномального объёма."""
        detections = []

        ratio = state.volume_spike_ratio

        if ratio <= 0:
            return detections

        if ratio > settings.spot.volume_spike_critical:
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.CRITICAL,
                detection_type="VOLUME_SPIKE_EXTREME",
                score=95,
                details={
                    "volume_5m": state.volume_5m,
                    "avg_volume_5m": state.avg_volume_1h / 12 if state.avg_volume_1h else Decimal("0"),
                    "spike_ratio": ratio,
                },
                evidence=[
                    f"Volume {ratio:.1f}x above average",
                    f"5-min volume: ${state.volume_5m:,.0f}",
                    f"Average 5-min: ${state.avg_volume_1h / 12:,.0f}" if state.avg_volume_1h else "No baseline",
                ]
            ))
        elif ratio > settings.spot.volume_spike_alert:
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.ALERT,
                detection_type="VOLUME_SPIKE_HIGH",
                score=75,
                details={"spike_ratio": ratio},
                evidence=[f"Volume {ratio:.1f}x above average"]
            ))
        elif ratio > settings.spot.volume_spike_warning:
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.WARNING,
                detection_type="VOLUME_SPIKE",
                score=50,
                details={"spike_ratio": ratio},
                evidence=[f"Volume {ratio:.1f}x above average"]
            ))

        return detections

    def _detect_price_velocity(self, state: SymbolState) -> list[Detection]:
        """Детекция аномальной скорости изменения цены."""
        detections = []

        price_change_1m = abs(state.price_change_1m_pct)
        price_change_5m = abs(state.price_change_5m_pct)

        # Критический: >25% за 5 минут
        if price_change_5m > settings.spot.price_velocity_5m_critical:
            direction = "UP" if state.price_change_5m_pct > 0 else "DOWN"
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.CRITICAL,
                detection_type="PRICE_VELOCITY_EXTREME",
                score=95,
                details={
                    "price_change_5m_pct": state.price_change_5m_pct,
                    "direction": direction,
                    "current_price": state.last_price,
                },
                evidence=[
                    f"Price moved {price_change_5m:.1f}% {direction} in 5 minutes",
                    f"Current price: {state.last_price}",
                ]
            ))
        # Alert: >5% за минуту
        elif price_change_1m > settings.spot.price_velocity_1m_alert:
            direction = "UP" if state.price_change_1m_pct > 0 else "DOWN"
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.ALERT,
                detection_type="PRICE_VELOCITY_HIGH",
                score=80,
                details={
                    "price_change_1m_pct": state.price_change_1m_pct,
                    "direction": direction,
                },
                evidence=[f"Price moved {price_change_1m:.1f}% {direction} in 1 minute"]
            ))
        elif price_change_5m > settings.spot.price_velocity_5m_alert:
            direction = "UP" if state.price_change_5m_pct > 0 else "DOWN"
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.ALERT,
                detection_type="PRICE_VELOCITY_HIGH",
                score=70,
                details={
                    "price_change_5m_pct": state.price_change_5m_pct,
                    "direction": direction,
                },
                evidence=[f"Price moved {price_change_5m:.1f}% {direction} in 5 minutes"]
            ))

        return detections

    def _detect_orderbook_manipulation(self, state: SymbolState) -> list[Detection]:
        """Детекция манипуляции стаканом (использует ATR-based depth)."""
        detections = []

        # FIX-IMBALANCE-1: None = нет данных для детекции
        _raw_imbalance = state.book_imbalance_atr
        if _raw_imbalance is None:
            return detections

        # Используем ATR-based imbalance вместо legacy top-20
        imbalance = abs(_raw_imbalance)
        spread = state.spread_pct

        # Сильный перекос стакана
        if imbalance > settings.spot.imbalance_alert:
            side = "BUY" if _raw_imbalance > 0 else "SELL"
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.ALERT,
                detection_type="ORDERBOOK_IMBALANCE",
                score=70,
                details={
                    # ATR-based данные
                    "imbalance": round(float(_raw_imbalance), 4),
                    "bid_volume_atr": round(float(state.bid_volume_atr), 2),
                    "ask_volume_atr": round(float(state.ask_volume_atr), 2),
                    "atr_pct": round(float(state.atr_1h_pct), 2),
                    "dominant_side": side,
                    # Legacy для совместимости
                    "bid_volume": round(float(state.bid_volume_20), 2),
                    "ask_volume": round(float(state.ask_volume_20), 2),
                },
                evidence=[
                    f"SPOT Order book {imbalance:.0%} imbalanced to {side}",
                    f"ATR depth ±{float(state.atr_1h_pct):.1f}%",
                    f"Bid: ${float(state.bid_volume_atr):,.0f} | Ask: ${float(state.ask_volume_atr):,.0f}",
                ]
            ))
        elif imbalance > settings.spot.imbalance_warning:
            side = "BUY" if _raw_imbalance > 0 else "SELL"
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.WARNING,
                detection_type="ORDERBOOK_IMBALANCE_ELEVATED",
                score=50,
                details={
                    "imbalance": round(float(_raw_imbalance), 4),
                    "dominant_side": side,
                    "atr_pct": round(float(state.atr_1h_pct), 2),
                },
                evidence=[f"SPOT Order book {imbalance:.0%} imbalanced to {side} (ATR ±{float(state.atr_1h_pct):.1f}%)"]
            ))

        # Аномально широкий спред (ликвидность убрали)
        if spread > settings.spot.spread_critical:
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.ALERT,
                detection_type="WIDE_SPREAD_CRITICAL",
                score=65,
                details={
                    "spread_pct": spread,
                    "best_bid": state.best_bid,
                    "best_ask": state.best_ask,
                },
                evidence=[
                    f"Spread widened to {spread:.2f}%",
                    f"Bid: {state.best_bid}, Ask: {state.best_ask}",
                ]
            ))
        elif spread > settings.spot.spread_warning:
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.WARNING,
                detection_type="WIDE_SPREAD",
                score=50,
                details={"spread_pct": spread},
                evidence=[f"Spread widened to {spread:.2f}%"]
            ))

        return detections

    def _detect_trade_patterns(self, state: SymbolState) -> list[Detection]:
        """Детекция подозрительных паттернов трейдов."""
        detections = []

        trades = state.trades_5m
        if len(trades) < settings.spot.min_trades_for_pattern:
            return detections

        # 1. Wash trading: много трейдов одинакового размера
        quantities = [float(t.qty) for t in trades]
        # Round to avoid floating point issues
        qty_counts = Counter([round(q, 8) for q in quantities])
        most_common_qty, count = qty_counts.most_common(1)[0]
        repeat_ratio = count / len(trades)

        if repeat_ratio > settings.spot.wash_trade_critical:
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.CRITICAL,
                detection_type="WASH_TRADING_LIKELY",
                score=90,
                details={
                    "repeat_ratio": repeat_ratio,
                    "repeated_quantity": most_common_qty,
                    "repeat_count": count,
                    "total_trades": len(trades),
                },
                evidence=[
                    f"{repeat_ratio:.0%} of trades have identical size",
                    f"Quantity {most_common_qty} repeated {count} times",
                    f"Total {len(trades)} trades in 5 minutes",
                ]
            ))
        elif repeat_ratio > settings.spot.wash_trade_threshold:
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.ALERT,
                detection_type="WASH_TRADING_SUSPECTED",
                score=75,
                details={
                    "repeat_ratio": repeat_ratio,
                    "repeated_quantity": most_common_qty,
                },
                evidence=[
                    f"{repeat_ratio:.0%} of trades have same size ({most_common_qty})",
                ]
            ))

        # 2. Coordinated buying/selling: слишком много в одну сторону
        # ИСПРАВЛЕНО: проверяем что buy_ratio не None
        buy_ratio_raw = state.buy_ratio_5m

        # Если buy_ratio доступен, проверяем coordinated trading
        if buy_ratio_raw is not None:
            buy_ratio = float(buy_ratio_raw)

            if buy_ratio > settings.spot.coordinated_extreme:
                detections.append(Detection(
                    symbol=state.symbol,
                    timestamp=datetime.now(),
                    severity=AlertSeverity.ALERT,
                    detection_type="COORDINATED_BUYING",
                    score=80,
                    details={
                        "buy_ratio": buy_ratio,
                        "buy_count": int(buy_ratio * len(trades)),
                        "sell_count": int((1 - buy_ratio) * len(trades)),
                    },
                    evidence=[
                        f"{buy_ratio:.0%} of trades are aggressive BUYs",
                        "Possible coordinated pump attempt",
                    ]
                ))
            elif buy_ratio > settings.spot.coordinated_buy_threshold:
                detections.append(Detection(
                    symbol=state.symbol,
                    timestamp=datetime.now(),
                    severity=AlertSeverity.WARNING,
                    detection_type="ONE_SIDED_BUYING",
                    score=60,
                    details={"buy_ratio": buy_ratio},
                    evidence=[f"{buy_ratio:.0%} of trades are BUYs"]
                ))
            elif buy_ratio < (1 - settings.spot.coordinated_extreme):
                detections.append(Detection(
                    symbol=state.symbol,
                    timestamp=datetime.now(),
                    severity=AlertSeverity.ALERT,
                    detection_type="COORDINATED_SELLING",
                    score=80,
                    details={"sell_ratio": 1 - buy_ratio},
                    evidence=[
                        f"{1 - buy_ratio:.0%} of trades are aggressive SELLs",
                        "Possible coordinated dump",
                    ]
                ))
            elif buy_ratio < 1 - settings.spot.coordinated_buy_threshold:
                detections.append(Detection(
                    symbol=state.symbol,
                    timestamp=datetime.now(),
                    severity=AlertSeverity.WARNING,
                    detection_type="ONE_SIDED_SELLING",
                    score=60,
                    details={"sell_ratio": 1 - buy_ratio},
                    evidence=[f"{1 - buy_ratio:.0%} of trades are SELLs"]
                ))

        # 3. Rapid fire: слишком быстрые трейды
        if len(trades) >= 2:
            time_diffs = [
                trades[i].time - trades[i - 1].time
                for i in range(1, len(trades))
            ]
            avg_interval = statistics.mean(time_diffs)

            if avg_interval < settings.spot.rapid_fire_alert_ms:
                detections.append(Detection(
                    symbol=state.symbol,
                    timestamp=datetime.now(),
                    severity=AlertSeverity.ALERT,
                    detection_type="RAPID_FIRE_TRADES",
                    score=70,
                    details={
                        "avg_interval_ms": avg_interval,
                        "trade_count_5m": len(trades),
                    },
                    evidence=[
                        f"Average {avg_interval:.0f}ms between trades",
                        f"{len(trades)} trades in 5 minutes",
                        "Possible bot activity",
                    ]
                ))
            elif avg_interval < settings.spot.rapid_fire_warning_ms:
                detections.append(Detection(
                    symbol=state.symbol,
                    timestamp=datetime.now(),
                    severity=AlertSeverity.WARNING,
                    detection_type="HIGH_FREQUENCY_TRADES",
                    score=55,
                    details={"avg_interval_ms": avg_interval},
                    evidence=[f"Average {avg_interval:.0f}ms between trades"]
                ))

        return detections

    def _detect_pump_sequence(self, state: SymbolState) -> list[Detection]:
        """
        Детекция полной последовательности pump/dump.
        Комбинация: volume spike + price velocity + orderbook imbalance (ATR-based)
        """
        detections = []

        # Все условия должны сработать одновременно
        volume_spike = state.volume_spike_ratio > settings.spot.pump_volume_multiplier
        price_move = abs(state.price_change_5m_pct) > settings.spot.pump_price_change
        # FIX-IMBALANCE-1: None = imbalance условие не выполнено
        _raw_imb = state.book_imbalance_atr
        imbalance = abs(_raw_imb) > settings.spot.pump_imbalance if _raw_imb is not None else False

        # Дополнительные факторы - ИСПРАВЛЕНО: обрабатываем None
        buy_ratio = state.buy_ratio_5m
        one_sided = False
        if buy_ratio is not None:
            one_sided = (buy_ratio > settings.spot.pump_one_sided or
                         buy_ratio < (1 - settings.spot.pump_one_sided))

        if volume_spike and price_move and imbalance:
            direction = "PUMP" if state.price_change_5m_pct > 0 else "DUMP"

            score = 90
            if one_sided:
                score = 98  # Очень высокая уверенность

            # ИСПРАВЛЕНО: Округляем все значения и обрабатываем None для buy_ratio
            buy_ratio = state.buy_ratio_5m
            buy_ratio_display = f"{float(buy_ratio):.0%}" if buy_ratio is not None else "N/A"

            # FIX-IMBALANCE-1: _raw_imb гарантированно не None здесь (проверено выше)
            detections.append(Detection(
                symbol=state.symbol,
                timestamp=datetime.now(),
                severity=AlertSeverity.CRITICAL,
                detection_type=f"ACTIVE_{direction}",
                score=score,
                details={
                    "direction": direction,
                    "volume_spike_ratio": round(float(state.volume_spike_ratio), 2),
                    "price_change_5m_pct": round(float(state.price_change_5m_pct), 2),
                    "book_imbalance": round(float(_raw_imb), 4),
                    "bid_volume_atr": round(float(state.bid_volume_atr), 2),
                    "ask_volume_atr": round(float(state.ask_volume_atr), 2),
                    "atr_pct": round(float(state.atr_1h_pct), 2),
                    "buy_ratio": round(float(buy_ratio), 4) if buy_ratio is not None else None,
                    "current_price": round(float(state.last_price), 6),
                    "volume_5m": round(float(state.volume_5m), 2),
                },
                evidence=[
                    f"🚨 ACTIVE {direction} DETECTED",
                    f"Volume: {float(state.volume_spike_ratio):.1f}x normal",
                    f"Price: {float(state.price_change_5m_pct):+.1f}% in 5min",
                    f"SPOT Book: {abs(float(_raw_imb)):.0%} imbalanced (ATR ±{float(state.atr_1h_pct):.1f}%)",
                    f"Trade flow: {buy_ratio_display} buys",
                ]
            ))

        return detections

    def _compute_fingerprint(self, detection: Detection) -> str:
        """
        Вычислить уникальный fingerprint детекции на основе всех параметров.
        """
        data = {
            "symbol": detection.symbol,
            "type": detection.detection_type,
            "score": detection.score,
            "details": detection.details,
        }
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.md5(serialized.encode()).hexdigest()[:16]

    def _is_duplicate(self, detection: Detection) -> bool:
        """
        Умная проверка дубликатов.

        - Полный дубль (все параметры совпадают) → 5 минут
        - Тот же тип, но другие параметры → 3 секунды
        """
        key = (detection.symbol, detection.detection_type)
        last_record = self._recent_detections.get(key)

        if last_record is None:
            return False

        last_time, last_fingerprint = last_record
        elapsed = (datetime.now() - last_time).total_seconds()
        current_fingerprint = self._compute_fingerprint(detection)

        # Полный дубль → 5 минут
        if current_fingerprint == last_fingerprint:
            if elapsed < self.DEDUP_EXACT_MATCH_SEC:
                logger.debug(
                    "spot_dedup_exact_match",
                    symbol=detection.symbol,
                    type=detection.detection_type,
                    elapsed=f"{elapsed:.1f}s",
                )
                return True
        # Тот же тип, другие параметры → 3 секунды
        else:
            if elapsed < self.DEDUP_SAME_TYPE_SEC:
                logger.debug(
                    "spot_dedup_same_type",
                    symbol=detection.symbol,
                    type=detection.detection_type,
                    elapsed=f"{elapsed:.1f}s",
                )
                return True

        return False

    def _record_detection(self, detection: Detection):
        """Записать детекцию для дедупликации."""
        key = (detection.symbol, detection.detection_type)
        fingerprint = self._compute_fingerprint(detection)
        self._recent_detections[key] = (datetime.now(), fingerprint)

    def _deduplicate(
        self,
        symbol: str,
        detections: list[Detection]
    ) -> list[Detection]:
        """Умная дедупликация детекций."""
        new_detections = []

        for d in detections:
            if not self._is_duplicate(d):
                new_detections.append(d)
                self._record_detection(d)

        # Очистить старые записи (>1 час)
        cutoff = datetime.now() - timedelta(hours=1)
        self._recent_detections = {
            k: (t, fp) for k, (t, fp) in self._recent_detections.items()
            if t > cutoff
        }

        return new_detections

    def set_baseline(self, symbol: str, baseline: VolumeBaseline):
        """Установить baseline для символа."""
        self._baselines[symbol] = baseline

    def get_baseline(self, symbol: str) -> Optional[VolumeBaseline]:
        """Получить baseline для символа."""
        return self._baselines.get(symbol)
