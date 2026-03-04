# -*- coding: utf-8 -*-
"""
Детектор фазы накопления.

Использует СУЩЕСТВУЮЩИЕ данные из BinanceFriend для определения
когда крупный игрок (кит) набирает позицию перед движением.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List, TYPE_CHECKING
import structlog

from .models import (
    AccumulationScore,
    SignalDirection,
    SignalConfidence,
    SignalConfig,
)

if TYPE_CHECKING:
    from src.screener.futures_monitor import FuturesMonitor, FuturesState
    from src.screener.realtime_monitor import RealTimeMonitor
    from src.cross_exchange.state_store import StateStore
    from src.screener.models import SymbolState, Detection

logger = structlog.get_logger(__name__)


@dataclass
class AccumulationSignal:
    """Результат анализа накопления."""
    symbol: str
    score: AccumulationScore
    direction: SignalDirection
    confidence: SignalConfidence
    probability: int               # 0-100
    evidence: List[str]
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class AccumulationDetector:
    """
    Детектор фазы накопления позиции.

    Анализирует:
    1. OI dynamics (растёт ли Open Interest стабильно)
    2. Funding rate (дешёвые ли лонги/шорты)
    3. Crowd sentiment (толпа против тренда)
    4. Volume patterns (объём без движения цены)
    5. Cross-exchange signals (куда мигрируют киты)
    6. ORDERBOOK (SPOT + FUTURES) - bid/ask pressure, imbalance

    Использует СУЩЕСТВУЮЩИЕ компоненты BinanceFriend.
    """

    # Пороги для orderbook scoring
    IMBALANCE_STRONG = 0.4      # |imbalance| > 0.4 = сильный перекос
    IMBALANCE_MODERATE = 0.2   # |imbalance| > 0.2 = умеренный перекос
    VOLUME_RATIO_STRONG = 2.0  # bid/ask > 2.0 = сильное давление
    VOLUME_RATIO_MODERATE = 1.5  # bid/ask > 1.5 = умеренное давление

    # Минимальные объёмы для учёта orderbook (USD)
    # Стакан с объёмом меньше этого - игнорируется как неликвидный
    MIN_SPOT_VOLUME_USD = 1000      # $1000 мин для SPOT
    MIN_FUTURES_VOLUME_USD = 5000   # $5000 мин для FUTURES

    def __init__(
        self,
        futures_monitor: "FuturesMonitor",
        state_store: "StateStore",
        realtime_monitor: "RealTimeMonitor" = None,
        config: SignalConfig = None,
    ):
        """
        Args:
            futures_monitor: Существующий FuturesMonitor с OI/Funding/LS данными
            state_store: Существующий StateStore с cross-exchange данными
            realtime_monitor: Существующий RealTimeMonitor со SPOT orderbook
            config: Конфигурация сигналов
        """
        self.futures = futures_monitor
        self.state = state_store
        self.realtime = realtime_monitor
        self.config = config or SignalConfig()

        # Кэш последних детекций для анализа
        self._recent_detections: dict[str, List] = {}

    def add_detection(self, symbol: str, detection) -> None:
        """
        Добавить детекцию в кэш для анализа.

        Вызывается из screener при каждой детекции.
        """
        if symbol not in self._recent_detections:
            self._recent_detections[symbol] = []

        self._recent_detections[symbol].append({
            "type": detection.detection_type,
            "timestamp": detection.timestamp,
            "severity": detection.severity,
            "score": detection.score,
            "details": getattr(detection, 'details', {}),
        })

        # Очистка старых (>30 минут)
        cutoff = datetime.now() - timedelta(minutes=30)
        self._recent_detections[symbol] = [
            d for d in self._recent_detections[symbol]
            if d["timestamp"] > cutoff
        ]

    def analyze(
        self,
        symbol: str,
        skip_threshold: bool = False,
        min_score_override: Optional[int] = None  # FIX-D-1: минимальный порог для trigger детекций
    ) -> Optional[AccumulationSignal]:
        """
        Проанализировать символ на признаки накопления.

        Args:
            symbol: Торговая пара
            skip_threshold: Если True, вернёт сигнал даже при низком скоре
                           (используется для trigger-based сигналов чтобы
                            получить orderbook scoring)

        Returns:
            AccumulationSignal если обнаружено накопление, иначе None
        """
        # 1. Получить данные из FuturesMonitor
        futures_state = self.futures.get_state(symbol)
        if not futures_state or not futures_state.has_futures:
            logger.info("accumulation_no_futures_state", symbol=symbol)
            return None

        # 2. Получить SPOT state для orderbook
        spot_state = None
        if self.realtime:
            spot_state = self.realtime.get_state(symbol)

        # 3. Рассчитать скор накопления (включая orderbook)
        score = self._calculate_score(symbol, futures_state, spot_state)

        logger.info(
            "accumulation_score_calculated",
            symbol=symbol,
            total_score=score.total,
            threshold=self.config.min_accumulation_score,
            oi_growth=score.oi_growth,
            funding_cheap=score.funding_cheap,
            crowd_bearish=score.crowd_bearish,
            orderbook_total=score.orderbook_total,
        )

        # FIX-E-1: LONG накопление невозможно при падающем OI
        # Если OI падает — это выход из позиций, не вход
        _oi_1h = float(futures_state.oi_change_1h_pct)
        _direction_hint = self._determine_direction(futures_state, spot_state, score)
        if _direction_hint == SignalDirection.LONG and _oi_1h < -1.0:
            logger.info(
                "accumulation_long_rejected_falling_oi",
                symbol=symbol,
                oi_change_1h=_oi_1h,
                score=score.total,
            )
            return None

        # FIX-AUDIT-11: предупреждение для SHORT при растущем OI
        # SHORT при OI +5%+ рискован — позиции открываются, не закрываются
        if _direction_hint == SignalDirection.SHORT and _oi_1h > 5.0:
            logger.warning(
                "accumulation_short_warning_rising_oi",
                symbol=symbol,
                oi_change_1h=_oi_1h,
                score=score.total,
            )

        # 4. Проверить порог (если не skip_threshold)
        # FIX-D-1: min_score_override позволяет установить минимальный порог для trigger детекций
        effective_threshold = self.config.min_accumulation_score
        if min_score_override is not None:
            effective_threshold = min_score_override

        if not skip_threshold and score.total < effective_threshold:
            logger.info(
                "accumulation_score_below_threshold",
                symbol=symbol,
                score=score.total,
                threshold=effective_threshold,  # FIX-D-1: логируем фактический порог
                oi_growth=score.oi_growth,
                funding_cheap=score.funding_cheap,
                crowd_bearish=score.crowd_bearish,
                orderbook_total=score.orderbook_total,
            )
            return None

        # 5. Определить направление (с учётом orderbook)
        direction = self._determine_direction(futures_state, spot_state, score)

        # 6. Определить confidence
        confidence = self._determine_confidence(score.total)

        # 7. Собрать evidence (включая orderbook)
        evidence = self._collect_evidence(symbol, futures_state, spot_state, score)

        # 8. Рассчитать probability (включая orderbook)
        # FIX-AUDIT-10: передаём direction для direction-aware adjustments
        probability = self._calculate_probability(score, futures_state, spot_state, direction)

        if not skip_threshold and probability < self.config.min_probability:
            logger.info(
                "accumulation_probability_below_threshold",
                symbol=symbol,
                probability=probability,
                threshold=self.config.min_probability,
                score=score.total,
            )
            return None

        logger.info(
            "accumulation_detected",
            symbol=symbol,
            score=score.total,
            direction=direction.value,
            probability=probability,
        )

        return AccumulationSignal(
            symbol=symbol,
            score=score,
            direction=direction,
            confidence=confidence,
            probability=probability,
            evidence=evidence,
        )

    def _calculate_score(
        self,
        symbol: str,
        futures_state: "FuturesState",
        spot_state: "SymbolState" = None
    ) -> AccumulationScore:
        """Рассчитать скор накопления на основе всех данных."""
        score = AccumulationScore()

        # =====================================================================
        # 1. OI ФАКТОРЫ (из FuturesMonitor)
        # =====================================================================
        oi_change_1h = float(futures_state.oi_change_1h_pct)
        oi_change_5m = float(futures_state.oi_change_5m_pct)

        # OI Growth: рост OI = накопление позиций
        if oi_change_1h >= self.config.oi_growth_strong:
            score.oi_growth = 20
        elif oi_change_1h >= self.config.oi_growth_min:
            score.oi_growth = int(oi_change_1h)  # 5-15 баллов
        elif oi_change_1h >= 3:
            score.oi_growth = 5

        # FIX-AUDIT-12: OI Stability только при подтверждённом росте
        # elif убран: oi_change_5m >= -1 выполняется в ~70% времени (false stability)
        if oi_change_1h > 0 and oi_change_5m > 0:
            score.oi_stability = 5

        # =====================================================================
        # 2. FUNDING ФАКТОРЫ (P09: Funding отриц = 60%, edge +10% → снижены веса)
        # =====================================================================
        if futures_state.current_funding:
            funding_pct = float(futures_state.current_funding.funding_rate_percent)

            # Funding Cheap: отрицательный funding = лонги дешёвые
            if funding_pct <= self.config.funding_cheap_threshold:
                score.funding_cheap = 7  # Было 15
            elif funding_pct <= 0:
                score.funding_cheap = 5  # Было 10
            elif funding_pct <= 0.01:
                score.funding_cheap = 3  # Было 5

            # Extreme Funding Penalty: если уже extreme - поздно входить
            if funding_pct >= self.config.funding_extreme_threshold:
                score.extreme_funding_penalty = -15
            elif funding_pct >= 0.03:
                score.extreme_funding_penalty = -10

            # FIX-AUDIT-1: crowd_bullish УДАЛЁН из funding секции
            # crowd_bullish должен выставляться ТОЛЬКО из L/S ratio (секция CROWD SENTIMENT)
            # Funding rate 0.02% — норма рынка, не экстремальный crowd sentiment
            # При funding=0.01% и long_pct=70% старый код давал 10 вместо 20

        # Funding Gradient: падающий funding = накопление
        if len(futures_state.funding_history) >= 3:
            recent = futures_state.funding_history[-3:]
            oldest = float(recent[0].funding_rate_percent)
            newest = float(recent[-1].funding_rate_percent)
            gradient = newest - oldest

            if gradient <= -0.02:  # Сильное падение
                score.funding_gradient = 10
            elif gradient <= -0.01:
                score.funding_gradient = 5

        # =====================================================================
        # 3. CROWD SENTIMENT (из FuturesMonitor)
        # =====================================================================
        if futures_state.current_ls_ratio:
            short_pct = float(futures_state.current_ls_ratio.short_account_pct)
            long_pct = float(futures_state.current_ls_ratio.long_account_pct)

            # FIX-O-2: crowd_bearish и crowd_bullish взаимоисключающие
            # Толпа не может быть одновременно bullish и bearish
            if short_pct >= self.config.crowd_extreme_short:
                score.crowd_bearish = 20   # contrarian LONG
            elif short_pct >= self.config.crowd_short_threshold:
                score.crowd_bearish = 15
            elif short_pct >= 50:
                score.crowd_bearish = 5
            elif long_pct >= 70:
                score.crowd_bullish = 20   # contrarian SHORT
            elif long_pct >= 60:
                score.crowd_bullish = 15
            elif long_pct >= 55:
                score.crowd_bullish = 10

        # =====================================================================
        # 4. RECENT DETECTIONS (из кэша)
        # =====================================================================
        recent = self._recent_detections.get(symbol, [])

        # Coordinated Buying
        has_coordinated = any(
            "COORDINATED" in d["type"] and "BUY" in d["type"]
            for d in recent
        )
        if has_coordinated:
            score.coordinated_buying = 3  # Было 10, снижено (убыточен в 100% файлов бэктеста)

        # Volume без движения цены = accumulation
        has_volume_spike = any(
            "VOLUME_SPIKE" in d["type"]
            for d in recent
        )
        price_stable = abs(float(futures_state.price_change_1h_pct)) < 2.0

        if has_volume_spike and price_stable:
            # FIX-AUDIT-7: падение цены при объёме = distribution, не accumulation
            _price_1h = float(futures_state.price_change_1h_pct)
            if _price_1h >= -0.5:  # не падает больше 0.5% — накопление
                score.volume_accumulation = 5  # Было 10, снижено (VOLUME_SPIKE_HIGH убыточен)
            # else: цена падала при объёме — это distribution, баллов не давать
        elif has_volume_spike:
            score.volume_accumulation = 3  # Было 5, снижено

        # OI_SPIKE BONUS
        has_oi_spike = any("OI_SPIKE" in d["type"] for d in recent)
        if has_oi_spike:
            score.oi_spike_bonus = self.config.oi_spike_bonus_points

        # QUIET ACCUMULATION BONUS (P07: Volume Spike < 0.5 прибылен в 70%)
        if spot_state and hasattr(spot_state, 'volume_spike_ratio') and float(spot_state.volume_spike_ratio) < 0.5:
            score.volume_accumulation += 8  # Тихое накопление = хороший знак (пропорционально 70%)
        # FOMO PENALTY (Volume Spike 2.0+ = 50%, 0 edge, поздний вход)
        elif spot_state and hasattr(spot_state, 'volume_spike_ratio') and float(spot_state.volume_spike_ratio) > 2.0:
            score.volume_accumulation -= 3  # Штраф за поздний вход

        # Wash Trading Penalty
        has_wash = any(
            "WASH_TRADING" in d["type"]
            for d in recent
        )
        if has_wash:
            score.wash_trading_penalty = -25  # FIX-9: при пороге 65 штраф -10 незаметен

        # =====================================================================
        # 5. CROSS-EXCHANGE ФАКТОРЫ (из StateStore)
        # =====================================================================
        try:
            # FIX-AUDIT-8: OI Distribution с пониженными весами
            # 60%+ OI на одной бирже — часто структурная характеристика актива
            # (ETH всегда 60%+ на Binance), а не сигнал миграции
            # Снижаем вес: было 10/5, стало 5/3
            oi_dist = self.state.get_oi_distribution(symbol)
            if oi_dist:
                values = [v for k, v in oi_dist.items() if not k.startswith("_")]
                if values:
                    max_share = max(values)
                    if max_share >= 60:
                        score.cross_oi_migration = 5
                    elif max_share >= 50:
                        score.cross_oi_migration = 3

            # Price Leader: кто ведёт цену
            leader = self.state.get_price_leader(symbol)
            if leader:
                score.cross_price_lead = 5

        except Exception as e:
            logger.info("cross_exchange_score_error", symbol=symbol, error=str(e))

        # =====================================================================
        # 6. ORDERBOOK ФАКТОРЫ (SPOT + FUTURES)
        # =====================================================================
        self._calculate_orderbook_score(score, spot_state, futures_state)

        return score

    def _calculate_orderbook_score(
        self,
        score: AccumulationScore,
        spot_state: "SymbolState",
        futures_state: "FuturesState"
    ) -> None:
        """
        Рассчитать orderbook факторы для AccumulationScore.

        Логика для LONG сигнала (накопление перед пампом):
        - Сильный bid wall = покупатели готовы поддерживать цену
        - Слабые asks = мало сопротивления росту
        - Положительный imbalance = больше bid чем ask

        Для SHORT - всё наоборот (но мы в основном детектим накопление = LONG).
        """
        # ========== SPOT ORDERBOOK ==========
        if spot_state:
            spot_bid = float(spot_state.bid_volume_atr)
            spot_ask = float(spot_state.ask_volume_atr)
            # FIX-IMBALANCE-1: None = нет данных, используем 0.0 (нейтрально)
            _raw_spot_imb = spot_state.book_imbalance_atr
            spot_imbalance = float(_raw_spot_imb) if _raw_spot_imb is not None else 0.0
            spot_total = spot_bid + spot_ask

            # Только если объём достаточный (иначе стакан неликвидный)
            if spot_total >= self.MIN_SPOT_VOLUME_USD:
                # FIX-AUDIT-2: один penalty source per orderbook
                # Было: bid_ask_ratio < 0.5 И imbalance <= -0.4 оба давали -8
                # При медвежьем стакане это -16 за один факт (двойной counting)
                spot_penalty = 0

                # 1. Bid Pressure: сильный bid wall
                if spot_ask > 0:
                    bid_ask_ratio = spot_bid / spot_ask
                    if bid_ask_ratio >= self.VOLUME_RATIO_STRONG:
                        score.spot_bid_pressure = 10
                    elif bid_ask_ratio >= self.VOLUME_RATIO_MODERATE:
                        score.spot_bid_pressure = 5
                    elif bid_ask_ratio < 0.5:
                        spot_penalty = -8  # запоминаем, не применяем сразу

                # 2. Ask Weakness: слабые продавцы (относительно bid)
                if spot_bid > 0 and spot_ask > 0:
                    ask_bid_ratio = spot_ask / spot_bid
                    if ask_bid_ratio < 0.5:
                        score.spot_ask_weakness = 5
                    elif ask_bid_ratio < 0.7:
                        score.spot_ask_weakness = 3

                # 3. Imbalance Score
                if spot_imbalance >= self.IMBALANCE_STRONG:
                    score.spot_imbalance_score = 5
                elif spot_imbalance >= self.IMBALANCE_MODERATE:
                    score.spot_imbalance_score = 3
                elif spot_imbalance <= -self.IMBALANCE_STRONG:
                    spot_penalty = min(spot_penalty, -8)  # max penalty, не сумма

                # Применяем penalty один раз
                score.orderbook_against_penalty += spot_penalty

        # ========== FUTURES ORDERBOOK ==========
        if futures_state:
            fut_bid = float(futures_state.futures_bid_volume_atr)
            fut_ask = float(futures_state.futures_ask_volume_atr)
            # FIX-IMBALANCE-1: None = нет данных, используем 0.0 (нейтрально)
            _raw_fut_imb = futures_state.futures_book_imbalance_atr
            fut_imbalance = float(_raw_fut_imb) if _raw_fut_imb is not None else 0.0
            fut_total = fut_bid + fut_ask

            # Только если объём достаточный
            if fut_total >= self.MIN_FUTURES_VOLUME_USD:
                # FIX-AUDIT-2: один penalty для futures orderbook
                fut_penalty = 0

                # 1. Bid Pressure
                if fut_ask > 0:
                    bid_ask_ratio = fut_bid / fut_ask
                    if bid_ask_ratio >= self.VOLUME_RATIO_STRONG:
                        score.futures_bid_pressure = 10
                    elif bid_ask_ratio >= self.VOLUME_RATIO_MODERATE:
                        score.futures_bid_pressure = 5

                # 2. Ask Weakness
                if fut_bid > 0 and fut_ask > 0:
                    ask_bid_ratio = fut_ask / fut_bid
                    if ask_bid_ratio < 0.5:
                        score.futures_ask_weakness = 5
                    elif ask_bid_ratio < 0.7:
                        score.futures_ask_weakness = 3

                # 3. Imbalance Score
                if fut_imbalance >= self.IMBALANCE_STRONG:
                    score.futures_imbalance_score = 5
                elif fut_imbalance >= self.IMBALANCE_MODERATE:
                    score.futures_imbalance_score = 3
                elif fut_imbalance <= -self.IMBALANCE_STRONG:
                    fut_penalty = -8

                # Применяем penalty один раз
                score.orderbook_against_penalty += fut_penalty

        # FIX-E-4: аномальная разница объёмов spot vs futures
        if spot_state:
            _sp_total = float(spot_state.bid_volume_atr) + float(spot_state.ask_volume_atr)
            _ft_total = float(futures_state.futures_bid_volume_atr) + float(futures_state.futures_ask_volume_atr)
            if _sp_total > 0 and _ft_total / _sp_total > 5.0:
                # Futures в 5+ раз больше spot — аномалия, снижаем futures scoring
                score.futures_bid_pressure = min(score.futures_bid_pressure, 5)
                score.futures_ask_weakness = min(score.futures_ask_weakness, 3)
                score.futures_imbalance_score = min(score.futures_imbalance_score, 3)
                logger.debug(
                    "orderbook_futures_spot_ratio_anomaly",
                    symbol=getattr(futures_state, 'symbol', 'unknown'),  # FIX-F-2: реальный символ
                    futures_total=_ft_total,
                    spot_total=_sp_total,
                    ratio=_ft_total/_sp_total,
                )

        # ========== SPOT-FUTURES DIVERGENCE ==========
        # FIX-IMBALANCE-1: divergence только если оба источника имеют данные
        if spot_state and futures_state:
            _raw_s = spot_state.book_imbalance_atr
            _raw_f = futures_state.futures_book_imbalance_atr

            if _raw_s is not None and _raw_f is not None:
                spot_imb = float(_raw_s)
                fut_imb = float(_raw_f)

                # Оба положительные и сильные = подтверждение
                if spot_imb > self.IMBALANCE_MODERATE and fut_imb > self.IMBALANCE_MODERATE:
                    score.orderbook_divergence = 5
                # Divergence: один positive, другой negative = осторожность
                elif (spot_imb > 0.1 and fut_imb < -0.1) or (spot_imb < -0.1 and fut_imb > 0.1):
                    score.orderbook_divergence = 0  # Не добавляем и не вычитаем

    def _determine_direction(
        self,
        futures_state: "FuturesState",
        spot_state: "SymbolState" = None,
        score: AccumulationScore = None
    ) -> SignalDirection:
        """
        Определить направление сигнала на основе всех данных.

        Приоритет факторов:
        1. Orderbook (если сильный перекос) - 40%
        2. Futures (funding + OI + L/S ratio) - 40%
        3. Default LONG (накопление обычно перед пампом) - 20%
        """
        short_signals = 0
        long_signals = 0

        # ========== FUTURES SIGNALS ==========
        if futures_state.current_funding:
            funding_pct = float(futures_state.current_funding.funding_rate_percent)

            # FIX-AUDIT-4: структурированный комбо-бонус вместо двойного if
            # Было: оба if истинны при funding>=0.03 и OI<-5 → short_signals += 3
            # Теперь: базовый +1 за funding, +1 бонус за комбо
            if funding_pct >= 0.03:
                short_signals += 1
                if float(futures_state.oi_change_1h_pct) < -5:
                    short_signals += 1  # комбо: дорогие лонги + выход позиций

            # Negative funding = longs are cheap = LONG
            if funding_pct < -0.01:
                long_signals += 1

        if futures_state.current_ls_ratio:
            long_pct = float(futures_state.current_ls_ratio.long_account_pct)
            short_pct = float(futures_state.current_ls_ratio.short_account_pct)

            # Экстремально много лонгов + падающий OI = dump
            if long_pct >= 70 and float(futures_state.oi_change_1h_pct) < -5:
                short_signals += 2

            # FIX-R-3: 65%+ явный перекос → достаточно без второго фактора
            # 60-65% → нужен второй фактор (funding или orderbook)
            if long_pct >= 65:
                short_signals += 2
            elif long_pct >= 60:
                short_signals += 1

            # FIX-A-2: crowd bearish не перевешивает прямой медвежий стакан
            _ob_bearish = False
            if spot_state:
                _spot_bid = float(spot_state.bid_volume_atr)
                _spot_ask = float(spot_state.ask_volume_atr)
                _spot_total = _spot_bid + _spot_ask
                if _spot_total >= self.MIN_SPOT_VOLUME_USD:
                    # FIX-IMBALANCE-1: None = нет данных, не считаем bearish
                    _raw_imb = spot_state.book_imbalance_atr
                    if _raw_imb is not None and float(_raw_imb) <= -self.IMBALANCE_STRONG:
                        _ob_bearish = True
            if not _ob_bearish and short_pct >= 55:
                long_signals += 2

        # ========== ORDERBOOK SIGNALS ==========
        # SPOT orderbook (только если достаточный объём!)
        if spot_state:
            spot_bid = float(spot_state.bid_volume_atr)
            spot_ask = float(spot_state.ask_volume_atr)
            spot_total = spot_bid + spot_ask

            # Игнорируем неликвидный стакан
            if spot_total >= self.MIN_SPOT_VOLUME_USD:
                # FIX-IMBALANCE-1: None = не добавляем сигналы
                _raw = spot_state.book_imbalance_atr
                if _raw is not None:
                    spot_imbalance = float(_raw)
                    if spot_imbalance >= self.IMBALANCE_STRONG:
                        long_signals += 2  # Bids dominant = LONG
                    elif spot_imbalance <= -self.IMBALANCE_STRONG:
                        short_signals += 2  # Asks dominant = SHORT

        # FUTURES orderbook (только если достаточный объём!)
        if futures_state:
            fut_bid = float(futures_state.futures_bid_volume_atr)
            fut_ask = float(futures_state.futures_ask_volume_atr)
            fut_total = fut_bid + fut_ask

            # Игнорируем неликвидный стакан
            if fut_total >= self.MIN_FUTURES_VOLUME_USD:
                # FIX-IMBALANCE-1: None = не добавляем сигналы
                _raw = futures_state.futures_book_imbalance_atr
                if _raw is not None:
                    fut_imbalance = float(_raw)
                    if fut_imbalance >= self.IMBALANCE_STRONG:
                        long_signals += 2
                    elif fut_imbalance <= -self.IMBALANCE_STRONG:
                        short_signals += 2

        # ========== SCORE-BASED SIGNALS ==========
        if score:
            # Если orderbook сильно против (penalty < -5), это сигнал в другую сторону
            if score.orderbook_against_penalty <= -5:
                short_signals += 1

            # FIX-N-2: crowd_bullish score = SHORT signal
            if score.crowd_bullish >= 15:
                short_signals += 1

        # ========== DECISION ==========
        # FIX-N-4: SHORT при перевесе >= 2 (было 3)
        if short_signals >= 2 and short_signals > long_signals:
            return SignalDirection.SHORT

        # Default LONG (накопление = pump incoming)
        return SignalDirection.LONG

    def _determine_confidence(self, total_score: int) -> SignalConfidence:
        """Определить уровень уверенности по скору."""
        if total_score >= self.config.confidence_very_high:
            return SignalConfidence.VERY_HIGH
        elif total_score >= self.config.confidence_high:
            return SignalConfidence.HIGH
        elif total_score >= self.config.confidence_medium:
            return SignalConfidence.MEDIUM
        else:
            return SignalConfidence.LOW

    def _calculate_probability(
        self,
        score: AccumulationScore,
        futures_state: "FuturesState",
        spot_state: "SymbolState" = None,
        direction: SignalDirection = None  # FIX-AUDIT-10
    ) -> int:
        """
        Рассчитать вероятность успеха сигнала.

        Включает orderbook факторы:
        - Сильный bid wall подтверждает LONG
        - Оба стакана (SPOT + FUTURES) согласны = бонус
        """
        # FIX-AUDIT-10: default to LONG for backward compatibility
        if direction is None:
            direction = SignalDirection.LONG
        # Base probability from score
        # FIX-6: нелинейная шкала, score.total+10 не является вероятностью
        _s = score.total
        if _s < 50:
            base = 45
        elif _s < 65:
            base = 55
        elif _s < 75:
            base = 62
        elif _s < 85:
            base = 70
        elif _s < 95:
            base = 78
        else:
            base = 85

        # Adjustments
        adjustments = 0

        # FIX-O-3: бонусы для обоих направлений
        oi_5m = float(futures_state.oi_change_5m_pct)
        funding_pct_val = float(futures_state.current_funding.funding_rate_percent) if futures_state.current_funding else 0
        long_pct_val = float(futures_state.current_ls_ratio.long_account_pct) if futures_state.current_ls_ratio else 50
        short_pct_val = float(futures_state.current_ls_ratio.short_account_pct) if futures_state.current_ls_ratio else 50

        # FIX-AUDIT-10: direction-aware adjustments
        # Бонусы только если соответствуют направлению сигнала
        if direction == SignalDirection.LONG:
            # OI momentum: рост OI подтверждает LONG
            if oi_5m > 0:
                adjustments += 5
            # Funding: дешёвые лонги подтверждают LONG
            if funding_pct_val < 0:
                adjustments += 5
            # Crowd: толпа в шортах подтверждает contrarian LONG
            if short_pct_val > 55:
                adjustments += 5
        else:  # SHORT
            # OI momentum: падающий OI подтверждает SHORT (выход лонгов)
            if oi_5m < -2:
                adjustments += 3
            # Funding: экстремальный funding подтверждает SHORT
            if funding_pct_val >= 0.05:
                adjustments += 5
            # Crowd: толпа в лонгах подтверждает contrarian SHORT
            if long_pct_val > 60:
                adjustments += 5

        # ========== ORDERBOOK BONUS ==========
        # SPOT orderbook подтверждает
        if spot_state:
            _sp_bid_v = float(spot_state.bid_volume_atr)
            _sp_ask_v = float(spot_state.ask_volume_atr)
            _sp_liquid = (_sp_bid_v + _sp_ask_v) >= self.MIN_SPOT_VOLUME_USD  # FIX-A-3
            if _sp_liquid:
                # FIX-IMBALANCE-1: None = не добавляем бонус
                _raw_sp = spot_state.book_imbalance_atr
                if _raw_sp is not None:
                    spot_imbalance = float(_raw_sp)
                    if spot_imbalance >= self.IMBALANCE_STRONG:
                        adjustments += 5
                    elif spot_imbalance >= self.IMBALANCE_MODERATE:
                        adjustments += 3

        # FUTURES orderbook подтверждает (только если стакан ликвидный)  # FIX-7
        _fut_bid_v = float(futures_state.futures_bid_volume_atr)
        _fut_ask_v = float(futures_state.futures_ask_volume_atr)
        _fut_liquid = (_fut_bid_v + _fut_ask_v) >= self.MIN_FUTURES_VOLUME_USD
        # FIX-IMBALANCE-1: None = 0.0 (нейтрально)
        _raw_fut = futures_state.futures_book_imbalance_atr if _fut_liquid else None
        fut_imbalance = float(_raw_fut) if _raw_fut is not None else 0.0
        if fut_imbalance >= self.IMBALANCE_STRONG:
            adjustments += 5
        elif fut_imbalance >= self.IMBALANCE_MODERATE:
            adjustments += 3

        # Оба стакана согласны = дополнительный бонус
        if spot_state:
            _sp_bid_v2 = float(spot_state.bid_volume_atr)
            _sp_ask_v2 = float(spot_state.ask_volume_atr)
            _sp_liquid2 = (_sp_bid_v2 + _sp_ask_v2) >= self.MIN_SPOT_VOLUME_USD
            # FIX-IMBALANCE-1: None = 0.0 (нейтрально)
            _raw_sp2 = spot_state.book_imbalance_atr if _sp_liquid2 else None
            spot_imb = float(_raw_sp2) if _raw_sp2 is not None else 0.0
            if spot_imb > 0.1 and fut_imbalance > 0.1:
                adjustments += 3  # Confirmation bonus
            elif (spot_imb > 0.1 and fut_imbalance < -0.1) or (spot_imb < -0.1 and fut_imbalance > 0.1):
                adjustments -= 5  # Divergence penalty

        # Сильный orderbook score = бонус
        if score.orderbook_total >= 20:
            adjustments += 5
        elif score.orderbook_total >= 10:
            adjustments += 3

        # FIX-D-3: Probability не может быть выше чем позволяет Confidence
        # Confidence определяется по score.total, probability должна соответствовать
        _confidence = self._determine_confidence(score.total)
        _max_prob_by_confidence = {
            SignalConfidence.LOW: 55,
            SignalConfidence.MEDIUM: 70,
            SignalConfidence.HIGH: 85,
            SignalConfidence.VERY_HIGH: 95,
        }
        max_prob = _max_prob_by_confidence.get(_confidence, 95)
        return min(max_prob, max(0, base + adjustments))

    def _collect_evidence(
        self,
        symbol: str,
        futures_state: "FuturesState",
        spot_state: "SymbolState",
        score: AccumulationScore
    ) -> List[str]:
        """Собрать доказательства для сигнала."""
        evidence = []

        # OI evidence
        if score.oi_growth >= 10:
            evidence.append(
                f"OI вырос +{futures_state.oi_change_1h_pct:.1f}% за час (FuturesMonitor)"
            )

        # Funding evidence
        if score.funding_cheap >= 10 and futures_state.current_funding:
            funding_pct = float(futures_state.current_funding.funding_rate_percent)
            evidence.append(
                f"Funding: {funding_pct:.3f}% (лонги дешёвые)"
            )

        # Crowd evidence
        if score.crowd_bearish >= 15 and futures_state.current_ls_ratio:
            short_pct = float(futures_state.current_ls_ratio.short_account_pct)
            evidence.append(
                f"Толпа: {short_pct:.1f}% в шортах (contrarian signal)"
            )

        # ========== ORDERBOOK EVIDENCE ==========
        # SPOT orderbook
        if score.spot_bid_pressure >= 5:
            if spot_state:
                spot_bid = float(spot_state.bid_volume_atr)
                spot_ask = float(spot_state.ask_volume_atr)
                ratio = spot_bid / spot_ask if spot_ask > 0 else 0
                evidence.append(
                    f"SPOT: Bid wall ${spot_bid:,.0f} vs Ask ${spot_ask:,.0f} ({ratio:.1f}x)"
                )

        if score.spot_imbalance_score >= 3:
            if spot_state:
                # FIX-IMBALANCE-1: None = не добавляем evidence
                _raw = spot_state.book_imbalance_atr
                if _raw is not None:
                    imb = float(_raw)
                    side = "покупатели доминируют" if imb > 0 else "продавцы доминируют"
                    evidence.append(f"SPOT imbalance: {imb:+.1%} ({side})")

        # FUTURES orderbook
        if score.futures_bid_pressure >= 5:
            fut_bid = float(futures_state.futures_bid_volume_atr)
            fut_ask = float(futures_state.futures_ask_volume_atr)
            ratio = fut_bid / fut_ask if fut_ask > 0 else 0
            evidence.append(
                f"FUTURES: Bid wall ${fut_bid:,.0f} vs Ask ${fut_ask:,.0f} ({ratio:.1f}x)"
            )

        if score.futures_imbalance_score >= 3:
            # FIX-IMBALANCE-1: None = не добавляем evidence
            _raw = futures_state.futures_book_imbalance_atr
            if _raw is not None:
                imb = float(_raw)
                side = "покупатели доминируют" if imb > 0 else "продавцы доминируют"
                evidence.append(f"FUTURES imbalance: {imb:+.1%} ({side})")

        # Orderbook divergence
        if score.orderbook_divergence >= 5:
            evidence.append("SPOT + FUTURES стаканы согласны (подтверждение)")

        # Detection evidence
        if score.coordinated_buying > 0:
            evidence.append("COORDINATED_BUYING детекция (группа покупает)")

        if score.volume_accumulation > 0:
            evidence.append("Volume spike без движения цены (тихое накопление)")

        # Cross-exchange evidence
        if score.cross_oi_migration > 0:
            evidence.append("OI концентрируется на одной бирже (киты там)")

        if score.cross_price_lead > 0:
            leader = self.state.get_price_leader(symbol)
            if leader:
                evidence.append(f"{leader.upper()} ведёт цену (CrossExchange)")

        # Negative evidence
        if score.wash_trading_penalty < 0:
            evidence.append("⚠️ Обнаружен wash trading (осторожно!)")

        if score.orderbook_against_penalty < 0:
            evidence.append("⚠️ Стакан показывает давление продавцов")

        return evidence

    def get_recent_detections(self, symbol: str, minutes: int = 30) -> List[dict]:
        """Получить недавние детекции для символа."""
        cutoff = datetime.now() - timedelta(minutes=minutes)
        return [
            d for d in self._recent_detections.get(symbol, [])
            if d["timestamp"] > cutoff
        ]
