# -*- coding: utf-8 -*-
"""
Детектор фазы накопления.
Адаптированная версия для исторической генерации сигналов.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, List

from .models import (
    AccumulationScore,
    SignalDirection,
    SignalConfidence,
    SignalConfig,
)

# Import state classes from state_builder
from state_builder import FuturesState, SymbolState


@dataclass
class AccumulationSignal:
    """Результат анализа накопления."""
    symbol: str
    score: AccumulationScore
    direction: SignalDirection
    confidence: SignalConfidence
    probability: int
    evidence: List[str]
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class DummyStateStore:
    """Stub for StateStore (cross-exchange data not available historically)."""
    def get_oi_distribution(self, symbol: str):
        return None

    def get_price_leader(self, symbol: str):
        return None


class AccumulationDetector:
    """
    Детектор фазы накопления позиции.
    Адаптирован для исторического анализа.
    """

    IMBALANCE_STRONG = 0.4
    IMBALANCE_MODERATE = 0.2
    VOLUME_RATIO_STRONG = 2.0
    VOLUME_RATIO_MODERATE = 1.5
    MIN_SPOT_VOLUME_USD = 1000
    MIN_FUTURES_VOLUME_USD = 5000

    def __init__(self, config: SignalConfig = None):
        """
        Simplified init for historical analysis.
        No monitors needed - we pass states directly.
        """
        self.config = config or SignalConfig()
        self.state = DummyStateStore()
        self._recent_detections: dict[str, List] = {}

    def add_detection(self, symbol: str, detection, current_time: datetime = None) -> None:
        """Добавить детекцию в кэш.

        Args:
            symbol: Trading pair
            detection: Detection dict
            current_time: Current historical time (MUST be provided for historical mode).
                          If None, falls back to datetime.now() for live mode.
        """
        if symbol not in self._recent_detections:
            self._recent_detections[symbol] = []

        # Use detection's own timestamp as the record timestamp
        det_ts = detection.get("timestamp", datetime.now(timezone.utc))
        if det_ts.tzinfo is None:
            det_ts = det_ts.replace(tzinfo=timezone.utc)

        self._recent_detections[symbol].append({
            "type": detection.get("detection_type", ""),
            "timestamp": det_ts,
            "severity": detection.get("severity", ""),
            "score": detection.get("score", 0),
            "details": detection.get("details", {}),
        })

        # FIX-S-1: Use current_time (historical) instead of datetime.now() (real-world clock).
        # In live mode current_time=None → falls back to now() which is correct.
        # In historical mode current_time=2026-02-01 10:30 → cutoff = 10:00 → keeps last 30m.
        if current_time is None:
            _now = datetime.now(timezone.utc)
        else:
            _now = current_time if current_time.tzinfo else current_time.replace(tzinfo=timezone.utc)

        cutoff = _now - timedelta(minutes=30)
        self._recent_detections[symbol] = [
            d for d in self._recent_detections[symbol]
            if d["timestamp"] > cutoff
        ]

    def analyze(
        self,
        symbol: str,
        futures_state: FuturesState,
        spot_state: SymbolState,
        skip_threshold: bool = False,
        min_score_override: Optional[int] = None
    ) -> Optional[AccumulationSignal]:
        """
        Проанализировать символ на признаки накопления.

        Modified signature: takes states directly instead of fetching from monitors.
        """
        if not futures_state or not futures_state.has_futures:
            return None

        score = self._calculate_score(symbol, futures_state, spot_state)

        # Определяем направление (строгие условия)
        direction = self._determine_direction(futures_state, spot_state, score)

        # Если направление не определено - условия не выполнены
        if direction is None:
            return None

        # ===== ФИЛЬТР: ПОДТВЕРЖДЕНИЕ ЦЕНОЙ (пункт 5) =====
        # Ослабленный фильтр - только против ОЧЕНЬ сильного тренда (>5%)
        if spot_state and spot_state.price_history and len(spot_state.price_history) >= 60:
            current_price = float(spot_state.last_price)
            price_1h_ago = float(spot_state.price_history[0])

            if price_1h_ago > 0:
                price_change_1h = ((current_price - price_1h_ago) / price_1h_ago) * 100

                # Только экстремальные движения блокируют
                if direction == SignalDirection.SHORT and price_change_1h > 5.0:
                    return None  # Очень сильный памп, опасно шортить

                if direction == SignalDirection.LONG and price_change_1h < -5.0:
                    return None  # Очень сильный дамп, опасно лонговать

        effective_threshold = self.config.min_accumulation_score
        if min_score_override is not None:
            effective_threshold = min_score_override

        if not skip_threshold and score.total < effective_threshold:
            return None

        confidence = self._determine_confidence(score.total)
        evidence = self._collect_evidence(symbol, futures_state, spot_state, score)
        probability = self._calculate_probability(score, futures_state, spot_state, direction)

        if not skip_threshold and probability < self.config.min_probability:
            return None

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
        futures_state: FuturesState,
        spot_state: SymbolState = None
    ) -> AccumulationScore:
        """Рассчитать скор накопления."""
        score = AccumulationScore()

        # OI FACTORS
        oi_change_1h = float(futures_state.oi_change_1h_pct)
        oi_change_5m = float(futures_state.oi_change_5m_pct)

        # Check if we have live data (non-zero hourly changes)
        has_live_oi_data = oi_change_1h != 0 or oi_change_5m != 0

        if has_live_oi_data:
            # LIVE mode - use hourly/5min changes (original logic)
            if oi_change_1h >= self.config.oi_growth_strong:
                score.oi_growth = 20
            elif oi_change_1h >= self.config.oi_growth_min:
                score.oi_growth = int(oi_change_1h)
            elif oi_change_1h >= 3:
                score.oi_growth = 5

            if oi_change_1h > 0 and oi_change_5m > 0:
                score.oi_stability = 5
        else:
            # HISTORICAL mode - use daily OI context from oi_history
            # Скоринг снижен для уменьшения шума (макс 20 очков вместо 25)
            oi_history = futures_state.oi_history
            if len(oi_history) >= 3:
                current_oi = float(oi_history[-1].open_interest)
                oi_3d_ago = float(oi_history[-3].open_interest)

                if oi_3d_ago > 0:
                    change_3d_pct = ((current_oi - oi_3d_ago) / oi_3d_ago) * 100
                else:
                    change_3d_pct = 0

                # oi_growth: сбалансированный скоринг (макс 15)
                if change_3d_pct >= 20:
                    score.oi_growth = 15  # очень сильное накопление
                elif change_3d_pct >= 15:
                    score.oi_growth = 12
                elif change_3d_pct >= 10:
                    score.oi_growth = 8
                elif change_3d_pct >= 5:
                    score.oi_growth = 4

                # oi_stability: сбалансированный (макс 10)
                consecutive_growth = 0
                for i in range(len(oi_history) - 1, 0, -1):
                    if float(oi_history[i].open_interest) > float(oi_history[i-1].open_interest):
                        consecutive_growth += 1
                    else:
                        break

                if consecutive_growth >= 5:
                    score.oi_stability = 10
                elif consecutive_growth >= 3:
                    score.oi_stability = 7
                elif consecutive_growth >= 2:
                    score.oi_stability = 4

        # FUNDING FACTORS (P09: Funding отриц = 60%, edge +10% → снижены веса)
        if futures_state.current_funding:
            funding_pct = float(futures_state.current_funding.funding_rate_percent)

            if funding_pct <= self.config.funding_cheap_threshold:
                score.funding_cheap = 7  # Было 15
            elif funding_pct <= 0:
                score.funding_cheap = 5  # Было 10
            elif funding_pct <= 0.01:
                score.funding_cheap = 3  # Было 5

            if funding_pct >= self.config.funding_extreme_threshold:
                score.extreme_funding_penalty = -15
            elif funding_pct >= 0.03:
                score.extreme_funding_penalty = -10

        if len(futures_state.funding_history) >= 3:
            recent = futures_state.funding_history[-3:]
            oldest = float(recent[0].funding_rate_percent)
            newest = float(recent[-1].funding_rate_percent)
            gradient = newest - oldest

            if gradient <= -0.02:
                score.funding_gradient = 10
            elif gradient <= -0.01:
                score.funding_gradient = 5

        # CROWD SENTIMENT
        if futures_state.current_ls_ratio:
            short_pct = float(futures_state.current_ls_ratio.short_account_pct)
            long_pct = float(futures_state.current_ls_ratio.long_account_pct)

            if short_pct >= self.config.crowd_extreme_short:
                score.crowd_bearish = 20
            elif short_pct >= self.config.crowd_short_threshold:
                score.crowd_bearish = 15
            elif short_pct >= 50:
                score.crowd_bearish = 5
            elif long_pct >= 70:
                score.crowd_bullish = 20
            elif long_pct >= 60:
                score.crowd_bullish = 15
            elif long_pct >= 55:
                score.crowd_bullish = 10

        # RECENT DETECTIONS
        recent = self._recent_detections.get(symbol, [])

        has_coordinated = any(
            "COORDINATED" in d["type"] and "BUY" in d["type"]
            for d in recent
        )
        if has_coordinated:
            score.coordinated_buying = 3  # Было 10, снижено (убыточен в 100% файлов бэктеста)

        has_volume_spike = any(
            "VOLUME_SPIKE" in d["type"]
            for d in recent
        )
        price_stable = abs(float(futures_state.price_change_1h_pct)) < 2.0

        if has_volume_spike and price_stable:
            _price_1h = float(futures_state.price_change_1h_pct)
            if _price_1h >= -0.5:
                score.volume_accumulation = 5  # Было 10, снижено (VOLUME_SPIKE_HIGH убыточен)
        elif has_volume_spike:
            score.volume_accumulation = 3  # Было 5, снижено

        # OI_SPIKE BONUS
        has_oi_spike = any("OI_SPIKE" in d["type"] for d in recent)
        if has_oi_spike:
            score.oi_spike_bonus = self.config.oi_spike_bonus_points

        # QUIET ACCUMULATION BONUS (P07: Volume Spike < 0.5 прибылен в 70%)
        if spot_state and float(spot_state.volume_spike_ratio) < 0.5:
            score.volume_accumulation += 8  # Тихое накопление = хороший знак (пропорционально 70%)
        # FOMO PENALTY (Volume Spike 2.0+ = 50%, 0 edge, поздний вход)
        elif spot_state and float(spot_state.volume_spike_ratio) > 2.0:
            score.volume_accumulation -= 3  # Штраф за поздний вход

        has_wash = any("WASH_TRADING" in d["type"] for d in recent)
        if has_wash:
            score.wash_trading_penalty = -25

        # ORDERBOOK FACTORS
        self._calculate_orderbook_score(score, spot_state, futures_state)

        return score

    def _calculate_orderbook_score(
        self,
        score: AccumulationScore,
        spot_state: SymbolState,
        futures_state: FuturesState
    ) -> None:
        """Calculate orderbook factors."""
        # SPOT ORDERBOOK
        if spot_state:
            spot_bid = float(spot_state.bid_volume_atr)
            spot_ask = float(spot_state.ask_volume_atr)
            _raw_spot_imb = spot_state.book_imbalance_atr
            spot_imbalance = float(_raw_spot_imb) if _raw_spot_imb is not None else 0.0
            spot_total = spot_bid + spot_ask

            if spot_total >= self.MIN_SPOT_VOLUME_USD:
                spot_penalty = 0

                if spot_ask > 0:
                    bid_ask_ratio = spot_bid / spot_ask
                    if bid_ask_ratio >= self.VOLUME_RATIO_STRONG:
                        score.spot_bid_pressure = 10
                    elif bid_ask_ratio >= self.VOLUME_RATIO_MODERATE:
                        score.spot_bid_pressure = 5
                    elif bid_ask_ratio < 0.5:
                        spot_penalty = -8

                if spot_bid > 0 and spot_ask > 0:
                    ask_bid_ratio = spot_ask / spot_bid
                    if ask_bid_ratio < 0.5:
                        score.spot_ask_weakness = 5
                    elif ask_bid_ratio < 0.7:
                        score.spot_ask_weakness = 3

                if spot_imbalance >= self.IMBALANCE_STRONG:
                    score.spot_imbalance_score = 5
                elif spot_imbalance >= self.IMBALANCE_MODERATE:
                    score.spot_imbalance_score = 3
                elif spot_imbalance <= -self.IMBALANCE_STRONG:
                    spot_penalty = min(spot_penalty, -8)

                score.orderbook_against_penalty += spot_penalty

        # FUTURES ORDERBOOK
        if futures_state:
            fut_bid = float(futures_state.futures_bid_volume_atr)
            fut_ask = float(futures_state.futures_ask_volume_atr)
            _raw_fut_imb = futures_state.futures_book_imbalance_atr
            fut_imbalance = float(_raw_fut_imb) if _raw_fut_imb is not None else 0.0
            fut_total = fut_bid + fut_ask

            if fut_total >= self.MIN_FUTURES_VOLUME_USD:
                fut_penalty = 0

                if fut_ask > 0:
                    bid_ask_ratio = fut_bid / fut_ask
                    if bid_ask_ratio >= self.VOLUME_RATIO_STRONG:
                        score.futures_bid_pressure = 10
                    elif bid_ask_ratio >= self.VOLUME_RATIO_MODERATE:
                        score.futures_bid_pressure = 5

                if fut_bid > 0 and fut_ask > 0:
                    ask_bid_ratio = fut_ask / fut_bid
                    if ask_bid_ratio < 0.5:
                        score.futures_ask_weakness = 5
                    elif ask_bid_ratio < 0.7:
                        score.futures_ask_weakness = 3

                if fut_imbalance >= self.IMBALANCE_STRONG:
                    score.futures_imbalance_score = 5
                elif fut_imbalance >= self.IMBALANCE_MODERATE:
                    score.futures_imbalance_score = 3
                elif fut_imbalance <= -self.IMBALANCE_STRONG:
                    fut_penalty = -8

                score.orderbook_against_penalty += fut_penalty

        # SPOT-FUTURES DIVERGENCE
        if spot_state and futures_state:
            _raw_s = spot_state.book_imbalance_atr
            _raw_f = futures_state.futures_book_imbalance_atr

            if _raw_s is not None and _raw_f is not None:
                spot_imb = float(_raw_s)
                fut_imb = float(_raw_f)

                if spot_imb > self.IMBALANCE_MODERATE and fut_imb > self.IMBALANCE_MODERATE:
                    score.orderbook_divergence = 5

    def _determine_direction(
        self,
        futures_state: FuturesState,
        spot_state: SymbolState = None,
        score: AccumulationScore = None
    ) -> Optional[SignalDirection]:
        """
        Determine signal direction with STRICT filters.
        Returns None if conditions not met (no weak signals).
        """
        # Получаем данные
        funding_pct = 0.0
        if futures_state.current_funding:
            funding_pct = float(futures_state.current_funding.funding_rate_percent)

        long_pct = 50.0
        short_pct = 50.0
        if futures_state.current_ls_ratio:
            long_pct = float(futures_state.current_ls_ratio.long_account_pct)
            short_pct = float(futures_state.current_ls_ratio.short_account_pct)

        # OI change за 3 дня (для исторических данных)
        oi_change_3d = 0.0
        oi_history = futures_state.oi_history
        if len(oi_history) >= 3:
            current_oi = float(oi_history[-1].open_interest)
            oi_3d_ago = float(oi_history[-3].open_interest)
            if oi_3d_ago > 0:
                oi_change_3d = ((current_oi - oi_3d_ago) / oi_3d_ago) * 100

        # ===== PRICE MOMENTUM (защита от тренда) =====
        # Блокируем ТОЛЬКО явно опасные ситуации:
        # - Шортить когда цена активно растёт = самоубийство
        # - Лонговать когда цена активно падает = самоубийство
        price_change_1h = 0.0
        if spot_state and spot_state.price_history and len(spot_state.price_history) >= 60:
            current_price = float(spot_state.last_price)
            price_1h_ago = float(spot_state.price_history[0])
            if price_1h_ago > 0:
                price_change_1h = ((current_price - price_1h_ago) / price_1h_ago) * 100

        # ===== 7-DAY PRICE TREND =====
        price_change_7d = 0.0
        if spot_state:
            price_change_7d = float(spot_state.price_change_7d_pct)

        # ===== MOMENTUM + CONTRARIAN TIMING =====

        # LONG: Uptrend + crowd bearish = покупаем откат
        # Работает идеально (100% win rate в бэктесте)
        uptrend = price_change_7d >= 5
        crowd_bearish_in_uptrend = short_pct >= 50
        long_conditions = uptrend and crowd_bearish_in_uptrend

        # SHORT: После СИЛЬНОГО ралли когда явная эйфория
        # Условия (все 4 обязательны):
        # 1. Цена выросла сильно (>= 20%) = сильно overbought
        # 2. Толпа экстремально в лонгах (>= 75%) = эйфория
        # 3. OI падает или стагнирует (< 0%) = smart money УЖЕ выходит
        # 4. Funding высокий (> 0.01%) = лонги дорого держать
        strong_rally = price_change_7d >= 20
        crowd_euphoric = long_pct >= 75
        smart_money_exiting = oi_change_3d < 0
        funding_expensive = funding_pct > 0.01
        short_conditions = strong_rally and crowd_euphoric and smart_money_exiting and funding_expensive

        # DECISION
        if short_conditions:
            return SignalDirection.SHORT
        elif long_conditions:
            return SignalDirection.LONG
        else:
            # Условия не выполнены - нет сигнала
            return None

    def _determine_confidence(self, total_score: int) -> SignalConfidence:
        """Determine confidence level."""
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
        futures_state: FuturesState,
        spot_state: SymbolState = None,
        direction: SignalDirection = None
    ) -> int:
        """Calculate signal probability."""
        if direction is None:
            direction = SignalDirection.LONG

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

        adjustments = 0

        oi_5m = float(futures_state.oi_change_5m_pct)
        funding_pct_val = float(futures_state.current_funding.funding_rate_percent) if futures_state.current_funding else 0
        long_pct_val = float(futures_state.current_ls_ratio.long_account_pct) if futures_state.current_ls_ratio else 50
        short_pct_val = float(futures_state.current_ls_ratio.short_account_pct) if futures_state.current_ls_ratio else 50

        if direction == SignalDirection.LONG:
            if oi_5m > 0:
                adjustments += 5
            if funding_pct_val < 0:
                adjustments += 5
            if short_pct_val > 55:
                adjustments += 5
        else:
            if oi_5m < -2:
                adjustments += 3
            if funding_pct_val >= 0.05:
                adjustments += 5
            if long_pct_val > 60:
                adjustments += 5

        # ORDERBOOK BONUS
        if spot_state:
            _sp_bid_v = float(spot_state.bid_volume_atr)
            _sp_ask_v = float(spot_state.ask_volume_atr)
            _sp_liquid = (_sp_bid_v + _sp_ask_v) >= self.MIN_SPOT_VOLUME_USD
            if _sp_liquid:
                _raw_sp = spot_state.book_imbalance_atr
                if _raw_sp is not None:
                    spot_imbalance = float(_raw_sp)
                    if spot_imbalance >= self.IMBALANCE_STRONG:
                        adjustments += 5
                    elif spot_imbalance >= self.IMBALANCE_MODERATE:
                        adjustments += 3

        _fut_bid_v = float(futures_state.futures_bid_volume_atr)
        _fut_ask_v = float(futures_state.futures_ask_volume_atr)
        _fut_liquid = (_fut_bid_v + _fut_ask_v) >= self.MIN_FUTURES_VOLUME_USD
        _raw_fut = futures_state.futures_book_imbalance_atr if _fut_liquid else None
        fut_imbalance = float(_raw_fut) if _raw_fut is not None else 0.0
        if fut_imbalance >= self.IMBALANCE_STRONG:
            adjustments += 5
        elif fut_imbalance >= self.IMBALANCE_MODERATE:
            adjustments += 3

        if spot_state:
            _sp_bid_v2 = float(spot_state.bid_volume_atr)
            _sp_ask_v2 = float(spot_state.ask_volume_atr)
            _sp_liquid2 = (_sp_bid_v2 + _sp_ask_v2) >= self.MIN_SPOT_VOLUME_USD
            _raw_sp2 = spot_state.book_imbalance_atr if _sp_liquid2 else None
            spot_imb = float(_raw_sp2) if _raw_sp2 is not None else 0.0
            if spot_imb > 0.1 and fut_imbalance > 0.1:
                adjustments += 3
            elif (spot_imb > 0.1 and fut_imbalance < -0.1) or (spot_imb < -0.1 and fut_imbalance > 0.1):
                adjustments -= 5

        if score.orderbook_total >= 20:
            adjustments += 5
        elif score.orderbook_total >= 10:
            adjustments += 3

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
        futures_state: FuturesState,
        spot_state: SymbolState,
        score: AccumulationScore
    ) -> List[str]:
        """Collect evidence for the signal."""
        evidence = []

        if score.oi_growth >= 10:
            oi_change_1h = float(futures_state.oi_change_1h_pct)
            if oi_change_1h != 0:
                evidence.append(
                    f"OI grew +{oi_change_1h:.1f}% in 1h"
                )
            elif len(futures_state.oi_history) >= 3:
                # Historical mode - show 3-day change
                current_oi = float(futures_state.oi_history[-1].open_interest)
                oi_3d_ago = float(futures_state.oi_history[-3].open_interest)
                if oi_3d_ago > 0:
                    change_3d = ((current_oi - oi_3d_ago) / oi_3d_ago) * 100
                    evidence.append(
                        f"OI grew +{change_3d:.1f}% over 3 days (daily data)"
                    )

        if score.funding_cheap >= 10 and futures_state.current_funding:
            funding_pct = float(futures_state.current_funding.funding_rate_percent)
            evidence.append(
                f"Funding: {funding_pct:.3f}% (longs are cheap)"
            )

        if score.crowd_bearish >= 15 and futures_state.current_ls_ratio:
            short_pct = float(futures_state.current_ls_ratio.short_account_pct)
            evidence.append(
                f"Crowd: {short_pct:.1f}% in shorts (contrarian signal)"
            )

        if score.spot_bid_pressure >= 5 and spot_state:
            spot_bid = float(spot_state.bid_volume_atr)
            spot_ask = float(spot_state.ask_volume_atr)
            ratio = spot_bid / spot_ask if spot_ask > 0 else 0
            evidence.append(
                f"SPOT: Bid wall ${spot_bid:,.0f} vs Ask ${spot_ask:,.0f} ({ratio:.1f}x)"
            )

        if score.coordinated_buying > 0:
            evidence.append("COORDINATED_BUYING detection")

        if score.volume_accumulation > 0:
            evidence.append("Volume spike without price move (quiet accumulation)")

        if score.wash_trading_penalty < 0:
            evidence.append("WARNING: Wash trading detected")

        return evidence

    def get_recent_detections(
        self, symbol: str, minutes: int = 30, current_time: datetime = None
    ) -> List[dict]:
        """Get recent detections for symbol.

        Args:
            symbol: Trading pair
            minutes: How far back to look
            current_time: Reference time (for historical analysis). If None, uses now().
        """
        ref_time = current_time if current_time else datetime.now(timezone.utc)
        cutoff = ref_time - timedelta(minutes=minutes)
        return [
            d for d in self._recent_detections.get(symbol, [])
            if d["timestamp"] > cutoff
        ]
