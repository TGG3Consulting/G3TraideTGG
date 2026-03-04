# -*- coding: utf-8 -*-
"""
Генератор торговых сигналов.

Использует все детекторы и данные BinanceFriend для генерации
полноценных торговых сигналов с entry/SL/TP.
"""

import uuid
from datetime import datetime, timedelta  # FIX-12: timedelta вынесен на уровень модуля
from decimal import Decimal
from typing import Optional, List, TYPE_CHECKING
import structlog

from .models import (
    TradeSignal,
    SignalDirection,
    SignalType,
    SignalConfidence,
    SignalConfig,
)
from .accumulation_detector import AccumulationDetector, AccumulationSignal
from .risk_calculator import RiskCalculator

if TYPE_CHECKING:
    from src.screener.futures_monitor import FuturesMonitor
    from src.cross_exchange.state_store import StateStore
    from src.screener.realtime_monitor import RealTimeMonitor
    from src.screener.models import Detection

logger = structlog.get_logger(__name__)


class SignalGenerator:
    """
    Генератор торговых сигналов.

    Анализирует детекции и данные, генерирует торговые сигналы.

    Использует:
    - FuturesMonitor: OI, Funding, L/S Ratio
    - StateStore: Cross-exchange данные
    - RealTimeMonitor: Spot данные, orderbook
    - Детекции: WHALE_ACCUMULATION, COORDINATED_BUYING, etc.
    """

    def __init__(
        self,
        futures_monitor: "FuturesMonitor",
        state_store: "StateStore",
        realtime_monitor: "RealTimeMonitor",
        config: SignalConfig = None,
    ):
        """
        Args:
            futures_monitor: Существующий FuturesMonitor
            state_store: Существующий StateStore
            realtime_monitor: Существующий RealTimeMonitor
            config: Конфигурация сигналов
        """
        self.futures = futures_monitor
        self.state = state_store
        self.realtime = realtime_monitor
        self.config = config or SignalConfig()

        # Sub-components
        self.accumulation_detector = AccumulationDetector(
            futures_monitor=futures_monitor,
            state_store=state_store,
            realtime_monitor=realtime_monitor,  # Передаём для orderbook
            config=self.config,
        )
        self.risk_calculator = RiskCalculator(config=self.config)

        # Кэш сигналов для дедупликации
        self._recent_signals: dict[str, datetime] = {}

        # Signal triggers mapping
        # NOTE: Futures detections get "FUTURES_" prefix in screener.py:493
        self._signal_triggers = {
            # ============ SPOT DETECTIONS (from detection_engine.py) ============
            # Active pump/dump — прямые триггеры
            "ACTIVE_PUMP": SignalType.BREAKOUT,
            "ACTIVE_DUMP": SignalType.BREAKOUT,

            # Volume spikes — важные сигналы
            "VOLUME_SPIKE_EXTREME": SignalType.BREAKOUT,
            "VOLUME_SPIKE_HIGH": SignalType.ACCUMULATION,

            # Price velocity — резкие движения
            "PRICE_VELOCITY_EXTREME": SignalType.BREAKOUT,
            "PRICE_VELOCITY_HIGH": SignalType.BREAKOUT,

            # Coordinated activity
            "COORDINATED_BUYING": SignalType.ACCUMULATION,
            "COORDINATED_SELLING": SignalType.ACCUMULATION,
            "ONE_SIDED_BUYING": SignalType.ACCUMULATION,
            "ONE_SIDED_SELLING": SignalType.ACCUMULATION,

            # Orderbook imbalance (SPOT)
            "ORDERBOOK_IMBALANCE": SignalType.ACCUMULATION,

            # ============ FUTURES DETECTIONS (with FUTURES_ prefix) ============
            # Orderbook imbalance (FUTURES)
            "FUTURES_ORDERBOOK_IMBALANCE": SignalType.ACCUMULATION,
            # Accumulation signals (LONG bias)
            "FUTURES_WHALE_ACCUMULATION_CRITICAL": SignalType.ACCUMULATION,
            "FUTURES_WHALE_ACCUMULATION_STEALTH": SignalType.ACCUMULATION,
            "FUTURES_OI_SPIKE_HIGH": SignalType.ACCUMULATION,
            "FUTURES_OI_SPIKE": SignalType.ACCUMULATION,

            # Exit/Drop signals (SHORT bias)
            "FUTURES_MASS_EXIT_DETECTED": SignalType.BREAKOUT,
            "FUTURES_OI_DROP": SignalType.BREAKOUT,

            # Squeeze signals
            "FUTURES_EXTREME_SHORT_POSITIONING": SignalType.SQUEEZE_SETUP,
            "FUTURES_FUNDING_EXTREME_SHORT": SignalType.SQUEEZE_SETUP,
            "FUTURES_EXTREME_LONG_POSITIONING": SignalType.SQUEEZE_SETUP,
            "FUTURES_FUNDING_EXTREME_LONG": SignalType.SQUEEZE_SETUP,

            # Divergence signals
            "FUTURES_WEAK_PUMP_DIVERGENCE": SignalType.DIVERGENCE,
            "FUTURES_WEAK_DUMP_DIVERGENCE": SignalType.DIVERGENCE,

            # Funding gradient
            "FUTURES_FUNDING_GRADIENT_SPIKE": SignalType.ACCUMULATION,
            "FUTURES_FUNDING_GRADIENT_DROP": SignalType.ACCUMULATION,

            # Cross-exchange signals
            "CX-001_PRICE_DIVERGENCE": SignalType.CROSS_EXCHANGE,
            "CX-003_FUNDING_ARBITRAGE": SignalType.CROSS_EXCHANGE,
            "CX-004_OI_MIGRATION": SignalType.CROSS_EXCHANGE,
        }

    def on_detection(self, detection: "Detection") -> Optional[TradeSignal]:
        """
        Обработать детекцию и проверить нужен ли торговый сигнал.

        Вызывается из screener при каждой детекции.

        Args:
            detection: Детекция из DetectionEngine или FuturesMonitor

        Returns:
            TradeSignal если условия выполнены, иначе None
        """
        symbol = detection.symbol
        detection_type = detection.detection_type

        # Blacklist check - токсичные монеты дают 80%+ убытков
        if symbol in self.config.symbol_blacklist:
            logger.debug("signal_blacklisted_symbol", symbol=symbol)
            return None

        # Blocked hours check - часы 10-12 UTC убыточны в 67% файлов
        if detection.timestamp.hour in self.config.blocked_hours_utc:
            logger.debug("signal_blocked_hour", symbol=symbol, hour=detection.timestamp.hour)
            return None

        # Blocked weekdays check (0=Mon, 1=Tue, ..., 6=Sun)
        if detection.timestamp.weekday() in self.config.blocked_weekdays:
            logger.debug("signal_blocked_weekday", symbol=symbol, weekday=detection.timestamp.weekday())
            return None

        logger.info(
            "signal_check_started",
            symbol=symbol,
            detection_type=detection_type,
            score=detection.score,
        )

        # 1. Добавить в кэш для AccumulationDetector
        self.accumulation_detector.add_detection(symbol, detection)

        # FIX-P-1: кулдаун включён — 1 час между сигналами на символ
        if self._is_recent_signal(symbol):
            logger.debug("signal_cooldown_active", symbol=symbol)
            return None

        # 3. Проверить является ли это trigger detection
        if detection_type not in self._signal_triggers:
            # Не trigger, но проверим накопление
            logger.info("signal_not_trigger_checking_accumulation", symbol=symbol, detection_type=detection_type)
            signal = self._check_accumulation_signal(symbol)
            if signal:
                self._record_signal(symbol)
            return signal

        signal_type = self._signal_triggers[detection_type]

        # 4. Проверить качество детекции
        # Минимум 60 для trigger детекций (было 70, потом 65)
        min_trigger_score = 60
        if detection.score < min_trigger_score:
            logger.info(
                "signal_skipped_low_score",
                symbol=symbol,
                score=detection.score,
                min_required=min_trigger_score,
            )
            return None

        # 5. Получить accumulation данные (включая orderbook scoring)
        # FIX-D-1: Для trigger детекций используем min_score_override=50 вместо skip_threshold
        # Это гарантирует минимальное качество сигнала при trigger-based детекциях
        accumulation = self.accumulation_detector.analyze(symbol, min_score_override=50)

        # FIX-H-1: не генерируем trigger-сигналы без данных накопления
        if accumulation is None:
            logger.info("signal_blocked_no_accumulation_data", symbol=symbol, detection_type=detection_type)
            return None

        logger.info(
            "signal_trigger_passed",
            symbol=symbol,
            detection_type=detection_type,
            score=detection.score,
            signal_type=signal_type.value,
            accumulation_score=accumulation.score.total if accumulation else 0,
            orderbook_score=accumulation.score.orderbook_total if accumulation else 0,
        )

        # 6. Сгенерировать сигнал (с accumulation данными)
        signal = self._generate_signal(
            symbol=symbol,
            signal_type=signal_type,
            accumulation=accumulation,  # Передаём accumulation с orderbook scoring
            trigger_detection=detection,
        )

        if signal:
            self._record_signal(symbol)
            logger.info(
                "signal_generated_from_trigger",
                symbol=symbol,
                signal_id=signal.signal_id,
                direction=signal.direction.value,
            )
        else:
            logger.warning(
                "signal_generation_returned_none",
                symbol=symbol,
                detection_type=detection_type,
            )

        return signal

    def check_for_signal(
        self,
        symbol: str,
        trigger_detection: Optional["Detection"] = None
    ) -> Optional[TradeSignal]:
        """
        Проверить символ на возможность торгового сигнала.

        Можно вызывать напрямую или через on_detection.
        """
        # FIX-P-1: кулдаун
        if self._is_recent_signal(symbol):
            return None

        # Проверить накопление
        signal = self._check_accumulation_signal(symbol, trigger_detection)

        if signal:
            self._record_signal(symbol)

        return signal

    def _check_accumulation_signal(
        self,
        symbol: str,
        trigger_detection: Optional["Detection"] = None
    ) -> Optional[TradeSignal]:
        """Проверить сигнал накопления."""
        # Получить анализ накопления
        accumulation = self.accumulation_detector.analyze(symbol)
        if not accumulation:
            return None

        return self._generate_signal(
            symbol=symbol,
            signal_type=SignalType.ACCUMULATION,
            accumulation=accumulation,
            trigger_detection=trigger_detection,
        )

    def _generate_signal(
        self,
        symbol: str,
        signal_type: SignalType,
        accumulation: Optional[AccumulationSignal] = None,
        trigger_detection: Optional["Detection"] = None,
    ) -> Optional[TradeSignal]:
        """Сгенерировать торговый сигнал."""
        try:
            # 1. Получить текущие данные
            futures_state = self.futures.get_state(symbol)
            spot_state = self.realtime.get_state(symbol)

            if not futures_state:
                logger.warning("signal_no_futures_state", symbol=symbol)
                return None

            # Max volume spike - перенесено в accumulation_detector как штраф -3 (50% = 0 edge)

            # 2. Определить текущую цену
            current_price = Decimal("0")
            if spot_state and spot_state.last_price > 0:
                current_price = spot_state.last_price
            elif futures_state.current_funding:
                current_price = futures_state.current_funding.mark_price

            if current_price <= 0:
                logger.warning("signal_no_current_price", symbol=symbol)
                return None

            # 3. Определить направление
            # ВАЖНО: accumulation.direction учитывает ВСЕ факторы (SPOT + FUTURES + funding + crowd)
            # Детекция ORDERBOOK_IMBALANCE может быть только от одного стакана,
            # поэтому НЕ должна перевешивать комплексный анализ
            direction = SignalDirection.LONG

            if accumulation:
                # Accumulation учитывает ОБА стакана + все futures данные
                direction = accumulation.direction
            elif trigger_detection:
                # Fallback только если нет accumulation
                direction = self._direction_from_detection(trigger_detection)

            # 4. Рассчитать риск-уровни
            _valid_hours_map = {  # FIX-J-2: перемещено до calculate()
                SignalType.BREAKOUT: 4,
                SignalType.SQUEEZE_SETUP: 8,
                SignalType.ACCUMULATION: 24,
                SignalType.DIVERGENCE: 12,
                SignalType.CROSS_EXCHANGE: 6,
            }
            _valid_hours = _valid_hours_map.get(signal_type, self.config.default_valid_hours)

            risk_levels = self.risk_calculator.calculate(
                symbol=symbol,
                direction=direction,
                current_price=current_price,
                spot_state=spot_state,
                futures_state=futures_state,
                valid_hours=_valid_hours,  # FIX-J-2
                accumulation_score=accumulation.score.total,  # FIX-M-1
            )

            # Проверить R:R
            if risk_levels.risk_reward_ratio < self.config.min_risk_reward:
                logger.info(
                    "signal_rejected_low_rr",
                    symbol=symbol,
                    rr=risk_levels.risk_reward_ratio,
                    min_rr=self.config.min_risk_reward
                )
                return None

            # 5. Собрать evidence
            evidence = []
            if accumulation:
                evidence.extend(accumulation.evidence)
            if trigger_detection:
                evidence.append(f"Триггер: {trigger_detection.detection_type}")
                for e in trigger_detection.evidence[:3]:
                    evidence.append(e)

            # 6. Собрать детали
            details = self._collect_details(symbol, futures_state, spot_state)

            # Добавить orderbook scoring из accumulation
            if accumulation:
                details["accumulation_score"] = accumulation.score.total
                details["orderbook_score"] = accumulation.score.orderbook_total

            # 7. Определить confidence и probability
            # FIX-H-2: убран fallback MEDIUM/65 — accumulation всегда должен быть (см. FIX-H-1)
            if accumulation:
                confidence = accumulation.confidence
                probability = accumulation.probability
            else:
                # Этот путь не должен выполняться после FIX-H-1
                logger.error("signal_missing_accumulation_unexpected", symbol=symbol)
                return None

            # 8. Создать сценарии
            scenarios = self._create_scenarios(direction, risk_levels)

            # 9. Создать ссылки
            base_symbol = symbol.replace("USDT", "")
            links = {
                "binance_futures": f"https://www.binance.com/ru/futures/{symbol}",
                "tradingview": f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}.P",
                "coinglass": f"https://www.coinglass.com/tv/{base_symbol}_USDT",
            }

            # 10. Trigger detections
            triggers = []
            if trigger_detection:
                triggers.append(trigger_detection.detection_type)

            # FIX-J-2: _valid_hours_map перемещён выше (до calculate())

            signal = TradeSignal(
                signal_id=str(uuid.uuid4()).replace("-", "")[:12],  # FIX-C-5: 12 символов, коллизия при 1M+ сигналов
                symbol=symbol,
                timestamp=datetime.now(),
                direction=direction,
                signal_type=signal_type,
                confidence=confidence,
                probability=probability,
                entry_zone_low=risk_levels.entry_zone_low,
                entry_zone_high=risk_levels.entry_zone_high,
                entry_limit=risk_levels.entry_limit,
                current_price=current_price,
                stop_loss=risk_levels.stop_loss,
                stop_loss_pct=risk_levels.stop_loss_pct,
                take_profits=risk_levels.take_profits,
                risk_reward_ratio=risk_levels.risk_reward_ratio,
                valid_hours=_valid_hours,  # FIX-13
                evidence=evidence,
                details=details,
                scenarios=scenarios,
                trigger_detections=triggers,
                links=links,
            )

            logger.info(
                "trade_signal_generated",
                symbol=symbol,
                direction=direction.value,
                signal_type=signal_type.value,
                probability=probability,
                rr=risk_levels.risk_reward_ratio,
            )

            return signal

        except Exception as e:
            logger.error("signal_generation_error", symbol=symbol, error=str(e))
            return None

    def _direction_from_detection(self, detection: "Detection") -> SignalDirection:
        """Определить направление по типу детекции."""
        detection_type = detection.detection_type

        # ORDERBOOK_IMBALANCE (SPOT и FUTURES): направление зависит от dominant_side
        if "ORDERBOOK_IMBALANCE" in detection_type:
            dominant_side = detection.details.get("dominant_side", "BUY")
            if dominant_side == "SELL":
                return SignalDirection.SHORT
            return SignalDirection.LONG

        # SHORT signals (includes both spot and futures-prefixed versions)
        short_triggers = [
            # Spot
            "ACTIVE_DUMP",
            "COORDINATED_SELLING",
            "ONE_SIDED_SELLING",
            # Futures (with FUTURES_ prefix)
            "FUTURES_WEAK_PUMP_DIVERGENCE",
            "FUTURES_EXTREME_LONG_POSITIONING",
            "FUTURES_FUNDING_EXTREME_LONG",
            "FUTURES_MASS_EXIT_DETECTED",
            "FUTURES_OI_DROP",
        ]
        if any(t in detection_type for t in short_triggers):
            return SignalDirection.SHORT

        # Default LONG
        return SignalDirection.LONG

    def _collect_details(
        self,
        symbol: str,
        futures_state,
        spot_state
    ) -> dict:
        """Собрать детали для сигнала."""
        details = {}

        # ========== FUTURES DATA ==========
        if futures_state:
            details["oi_change_1h"] = f"{float(futures_state.oi_change_1h_pct):+.1f}%"
            details["oi_change_5m"] = f"{float(futures_state.oi_change_5m_pct):+.1f}%"

            if futures_state.current_funding:
                details["funding"] = f"{float(futures_state.current_funding.funding_rate_percent):.4f}%"

            if futures_state.current_ls_ratio:
                details["long_pct"] = f"{float(futures_state.current_ls_ratio.long_account_pct):.1f}%"
                details["short_pct"] = f"{float(futures_state.current_ls_ratio.short_account_pct):.1f}%"

            # FUTURES ORDERBOOK (ATR-based)
            if futures_state.futures_bid_volume_atr > 0 or futures_state.futures_ask_volume_atr > 0:
                details["futures_bid_volume_atr"] = float(futures_state.futures_bid_volume_atr)
                details["futures_ask_volume_atr"] = float(futures_state.futures_ask_volume_atr)
                # FIX-IMBALANCE-1: None → null в JSON
                _fut_imb = futures_state.futures_book_imbalance_atr
                details["futures_imbalance_atr"] = float(_fut_imb) if _fut_imb is not None else None
                details["futures_atr_pct"] = float(futures_state.futures_atr_1h_pct)

        # ========== SPOT DATA ==========
        if spot_state:
            details["volume_ratio"] = f"{float(spot_state.volume_spike_ratio):.1f}x"
            details["spread"] = f"{float(spot_state.spread_pct):.3f}%"

            # SPOT ORDERBOOK (ATR-based)
            if spot_state.bid_volume_atr > 0 or spot_state.ask_volume_atr > 0:
                details["spot_bid_volume_atr"] = float(spot_state.bid_volume_atr)
                details["spot_ask_volume_atr"] = float(spot_state.ask_volume_atr)
                # FIX-IMBALANCE-1: None → null в JSON
                _spot_imb = spot_state.book_imbalance_atr
                details["spot_imbalance_atr"] = float(_spot_imb) if _spot_imb is not None else None
                details["spot_atr_pct"] = float(spot_state.atr_1h_pct)

            # FIX-A-1: Legacy для совместимости — уже внутри if spot_state
            # FIX-IMBALANCE-1: None → "N/A"
            _spot_imb_legacy = spot_state.book_imbalance_atr
            details["book_imbalance"] = f"{float(_spot_imb_legacy):+.2f}" if _spot_imb_legacy is not None else "N/A"

        # Cross-exchange
        try:
            price_spread = self.state.get_price_spread(symbol)
            if price_spread:
                max_spread = max(price_spread.values())
                details["cross_spread"] = f"{float(max_spread):.3f}%"

            funding_div = self.state.get_funding_divergence(symbol)
            if funding_div and "_spread" in funding_div:
                details["funding_spread"] = f"{float(funding_div['_spread']):.4f}%"
        except Exception as e:  # FIX-B-2: логируем вместо молчаливого проглатывания
            logger.debug("collect_details_cross_exchange_error", symbol=symbol, error=str(e))

        return details

    def _create_scenarios(
        self,
        direction: SignalDirection,
        risk_levels
    ) -> dict:
        """Создать сценарии для сигнала."""
        if direction == SignalDirection.LONG:
            return {
                "pump_started": f"Памп начался (цена > {risk_levels.entry_zone_high}): → Передвинуть SL на {risk_levels.entry_zone_high}",
                "sideways": "Боковик > 6 часов: → Держать если OI растёт, иначе выйти",
                "invalidation": "Отмена сетапа: → Закрыть если OI упал -5% или Funding > +0.05%",
            }
        else:
            return {
                "dump_started": f"Дамп начался (цена < {risk_levels.entry_zone_low}): → Передвинуть SL на {risk_levels.entry_zone_low}",
                "sideways": "Боковик > 6 часов: → Держать если OI падает, иначе выйти",
                "invalidation": "Отмена сетапа: → Закрыть если OI вырос +5% или Funding < -0.03%",
            }

    def _is_recent_signal(self, symbol: str) -> bool:
        """Проверить был ли недавний сигнал для символа."""
        last_signal = self._recent_signals.get(symbol)
        if not last_signal:
            return False

        # Минимум 1 час между сигналами
        return (datetime.now() - last_signal) < timedelta(hours=1)

    def _record_signal(self, symbol: str) -> None:
        """Записать время сигнала."""
        self._recent_signals[symbol] = datetime.now()

        # Очистка старых
        cutoff = datetime.now() - timedelta(hours=24)
        self._recent_signals = {
            s: t for s, t in self._recent_signals.items()
            if t > cutoff
        }
