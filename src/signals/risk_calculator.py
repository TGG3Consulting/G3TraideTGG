# -*- coding: utf-8 -*-
"""
Калькулятор риска для торговых сигналов.

Рассчитывает:
- Stop Loss на основе волатильности и уровней
- Take Profit уровни с R:R ratio
- Зону входа
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import List, Optional, TYPE_CHECKING
import structlog

from .models import TakeProfit, SignalDirection, SignalConfig

if TYPE_CHECKING:
    from src.screener.models import SymbolState
    from src.screener.futures_monitor import FuturesState

logger = structlog.get_logger(__name__)


@dataclass
class RiskLevels:
    """Рассчитанные уровни риска."""
    entry_zone_low: Decimal
    entry_zone_high: Decimal
    entry_limit: Decimal
    stop_loss: Decimal
    stop_loss_pct: float
    take_profits: List[TakeProfit]
    risk_reward_ratio: float


class RiskCalculator:
    """
    Калькулятор уровней риска.

    Использует:
    - Текущую цену и спред
    - Исторические уровни поддержки/сопротивления
    - Волатильность (ATR-подобная метрика)
    - Orderbook для определения walls и оптимального SL/TP

    ORDERBOOK INTEGRATION:
    - SL размещается за bid wall (для LONG) или ask wall (для SHORT)
    - TP учитывает resistance levels из orderbook
    """

    # Минимальный объём wall чтобы считать его значимым (в USD)
    MIN_WALL_VOLUME_USD = 50000

    def __init__(self, config: SignalConfig = None):
        self.config = config or SignalConfig()

    def calculate(
        self,
        symbol: str,
        direction: SignalDirection,
        current_price: Decimal,
        spot_state: Optional["SymbolState"] = None,
        futures_state: Optional["FuturesState"] = None,
        valid_hours: int = 4,  # FIX-J-1: время жизни сигнала влияет на SL
        accumulation_score: int = 70,  # FIX-M-1: для масштабирования TP
    ) -> RiskLevels:
        """
        Рассчитать уровни риска для сигнала.

        Args:
            symbol: Торговая пара
            direction: LONG или SHORT
            current_price: Текущая цена
            spot_state: Данные из RealTimeMonitor (опционально)
            futures_state: Данные из FuturesMonitor (опционально)

        Returns:
            RiskLevels с entry/SL/TP
        """
        # 1. Определить волатильность
        volatility_pct = self._estimate_volatility(spot_state, futures_state, valid_hours=valid_hours)

        # 2. Определить зону входа (с учётом orderbook)
        entry_zone_low, entry_zone_high, entry_limit = self._calculate_entry_zone(
            direction, current_price, volatility_pct, spot_state, futures_state
        )

        # 3. Определить Stop Loss (с учётом orderbook)
        stop_loss, sl_pct = self._calculate_stop_loss(
            direction, entry_limit, volatility_pct, spot_state, futures_state,
            valid_hours=valid_hours  # FIX-J-1
        )

        # 4. Рассчитать Take Profits
        take_profits = self._calculate_take_profits(
            direction, entry_limit, stop_loss,
            accumulation_score=accumulation_score,  # FIX-M-1
        )

        # 5. Рассчитать Risk:Reward
        risk_reward = self._calculate_risk_reward(
            direction, entry_limit, stop_loss, take_profits
        )

        return RiskLevels(
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            entry_limit=entry_limit,
            stop_loss=stop_loss,
            stop_loss_pct=sl_pct,
            take_profits=take_profits,
            risk_reward_ratio=risk_reward,
        )

    def _estimate_volatility(
        self,
        spot_state: Optional["SymbolState"],
        futures_state: Optional["FuturesState"],
        valid_hours: int = 4,
    ) -> float:
        """
        Оценить волатильность актива.

        FIX-ATR-RAW: использует atr_1h_pct_raw (без clamp) для точного SL/TP.

        Приоритет источников:
        0. Дневной ATR (для сигналов >= 4h)
        1. RAW ATR из spot_state (atr_1h_pct_raw)
        2. RAW ATR из futures_state (futures_atr_1h_pct_raw)
        3. Историческая волатильность из price_history
        4. Default 5%
        """
        volatility = 5.0  # Default 5%

        # ========== ПРИОРИТЕТ 0: ДНЕВНОЙ ATR для сигналов >= 4h ==========
        # Источник: для свинг-трейдинга стоп ставится через ATR таймфрейма сигнала.
        # 24h сигнал = дневной ATR, а не часовой.
        if valid_hours >= 4:
            if spot_state and getattr(spot_state, 'atr_daily_is_real', False):
                daily_atr = float(spot_state.atr_daily_pct)
                if daily_atr > 0:
                    logger.debug("volatility_from_daily_atr",
                                 atr_pct=daily_atr, valid_hours=valid_hours)
                    return max(0.5, min(20.0, daily_atr))
            if futures_state and getattr(futures_state, 'futures_atr_daily_is_real', False):
                daily_atr = float(getattr(futures_state, 'futures_atr_daily_pct', 0))
                if daily_atr > 0:
                    logger.debug("volatility_from_futures_daily_atr",
                                 atr_pct=daily_atr, valid_hours=valid_hours)
                    return max(0.5, min(20.0, daily_atr))

        # ========== ПРИОРИТЕТ 1: RAW ATR из spot_state ==========
        # FIX-ATR-RAW: читаем raw значение для SL/TP
        if spot_state and getattr(spot_state, 'atr_is_real', False):
            raw_atr = float(getattr(spot_state, 'atr_1h_pct_raw', 0))
            if raw_atr > 0:
                volatility = raw_atr
                logger.debug("volatility_from_spot_atr_raw", atr_pct=volatility)
            else:
                # Fallback на clamped если raw не заполнен
                volatility = float(spot_state.atr_1h_pct)
                logger.debug("volatility_from_spot_atr_clamped", atr_pct=volatility)

        # ========== ПРИОРИТЕТ 2: RAW ATR из futures_state ==========
        elif futures_state and getattr(futures_state, 'futures_atr_is_real', False):
            raw_atr = float(getattr(futures_state, 'futures_atr_1h_pct_raw', 0))
            if raw_atr > 0:
                volatility = raw_atr
                logger.debug("volatility_from_futures_atr_raw", atr_pct=volatility)
            else:
                volatility = float(futures_state.futures_atr_1h_pct)
                logger.debug("volatility_from_futures_atr_clamped", atr_pct=volatility)

        # ========== FALLBACK: историческая волатильность ==========
        elif spot_state and spot_state.price_history and len(spot_state.price_history) >= 10:
            prices = [float(p) for p in spot_state.price_history[-60:]]
            if prices:
                high = max(prices)
                low = min(prices)
                avg = sum(prices) / len(prices)
                if avg > 0:
                    volatility = (high - low) / avg * 100

        # FIX-AUDIT-5: корректировка ТОЛЬКО если ATR не реальный
        # При реальном ATR текущее движение уже учтено в расчёте
        atr_is_real = (
            (spot_state and getattr(spot_state, 'atr_is_real', False)) or
            (futures_state and getattr(futures_state, 'futures_atr_is_real', False))
        )
        if not atr_is_real and spot_state:
            price_change_1h = abs(float(spot_state.price_change_1h_pct)) if hasattr(spot_state, 'price_change_1h_pct') else 0
            if price_change_1h > 5:
                volatility = max(volatility, price_change_1h * 1.5)

        # OI change корректировка
        if futures_state:
            oi_change = abs(float(futures_state.oi_change_1h_pct))
            if oi_change > 10:
                volatility *= 1.2

        # FIX-ATR-RAW: clamp для SL/TP — минимум 0.5% (ниже бессмысленно для торговли)
        return max(0.5, min(20.0, volatility))

    def _calculate_entry_zone(
        self,
        direction: SignalDirection,
        current_price: Decimal,
        volatility_pct: float,
        spot_state: Optional["SymbolState"],
        futures_state: Optional["FuturesState"] = None
    ) -> tuple[Decimal, Decimal, Decimal]:
        """
        Рассчитать зону входа с учётом orderbook.

        Зона входа = текущая цена ± небольшой % для лимитного ордера.
        Orderbook корректирует entry при наличии сильных walls.
        """
        # Зона = 1-2% от текущей цены в зависимости от волатильности
        zone_pct = Decimal(str(min(2.0, volatility_pct / 3)))

        # FIX-AUDIT-3: симметричная зона вокруг entry_limit
        # Было: асимметрия 4x (zone/200 vs zone/100) без обоснования
        entry_limit = current_price
        half_zone = zone_pct / 200  # ±half_zone от entry
        entry_zone_low = entry_limit * (1 - half_zone)
        entry_zone_high = entry_limit * (1 + half_zone)

        # ========== ORDERBOOK ADJUSTMENTS ==========
        # FIX-IMBALANCE-2: Optional[Decimal] → четыре случая combined_imbalance
        spot_imbalance: Optional[Decimal] = None
        futures_imbalance: Optional[Decimal] = None

        if spot_state:
            spot_imbalance = spot_state.book_imbalance_atr  # может быть None

        if futures_state:
            futures_imbalance = futures_state.futures_book_imbalance_atr  # может быть None

        # FIX-IMBALANCE-2: четыре случая combined_imbalance
        if spot_imbalance is None and futures_imbalance is None:
            combined_imbalance = Decimal("0")  # нет данных — нейтрально, entry не корректируется
        elif spot_imbalance is not None and futures_imbalance is None:
            combined_imbalance = spot_imbalance  # только spot данные
        elif spot_imbalance is None and futures_imbalance is not None:
            combined_imbalance = futures_imbalance  # только futures данные
        else:
            combined_imbalance = (spot_imbalance + futures_imbalance) / 2  # оба есть — среднее

        if direction == SignalDirection.LONG:
            # Сильный bid wall (imbalance > 0.3) = можно входить агрессивнее
            if combined_imbalance > Decimal("0.4"):
                # Очень сильная поддержка - входим по рынку
                entry_limit = current_price
            elif combined_imbalance > Decimal("0.2"):
                # Умеренная поддержка - entry ближе к текущей цене
                entry_limit = entry_zone_low + (entry_zone_high - entry_zone_low) * Decimal("0.5")
        else:
            # SHORT: сильный ask wall = можно шортить агрессивнее
            if combined_imbalance < Decimal("-0.4"):
                entry_limit = current_price
            elif combined_imbalance < Decimal("-0.2"):
                entry_limit = entry_zone_high - (entry_zone_high - entry_zone_low) * Decimal("0.5")

        return (
            self._round_price(entry_zone_low),
            self._round_price(entry_zone_high),
            self._round_price(entry_limit)
        )

    def _calculate_stop_loss(
        self,
        direction: SignalDirection,
        entry_price: Decimal,
        volatility_pct: float,
        spot_state: Optional["SymbolState"],
        futures_state: Optional["FuturesState"] = None,
        valid_hours: int = 4,  # FIX-J-3: время жизни сигнала
    ) -> tuple[Decimal, float]:
        """
        Рассчитать Stop Loss с учётом orderbook.

        SL = entry ± (volatility * factor)

        ORDERBOOK LOGIC:
        - Для LONG: SL ставится ЗА bid wall (чтобы wall защищал)
        - Для SHORT: SL ставится ЗА ask wall
        - Если сильный imbalance в нашу сторону - можно SL ближе (wall защищает)
        """
        # SL = 1.8x ATR (было 1.5x, увеличено после бэктеста)
        # SL rate 61% был слишком высоким при 1.5x
        atr_sl = volatility_pct * 1.8

        # Минимум 3% (было 2%) — 2% это шум рынка для крипты
        # Максимум 6% (было 8%) — ограничиваем риск
        sl_pct = min(6.0, max(3.0, atr_sl))

        sl_decimal = Decimal(str(sl_pct / 100))

        if direction == SignalDirection.LONG:
            stop_loss = entry_price * (1 - sl_decimal)
        else:
            stop_loss = entry_price * (1 + sl_decimal)

        return self._round_price(stop_loss), round(sl_pct, 1)

    def _calculate_take_profits(
        self,
        direction: SignalDirection,
        entry_price: Decimal,
        stop_loss: Decimal,
        accumulation_score: int = 70,  # FIX-M-1: сила сигнала влияет на TP
    ) -> List[TakeProfit]:
        """
        Рассчитать уровни Take Profit.

        FIX-J-4: TP привязан к SL distance (риску) а не к ATR напрямую.
        Это сохраняет R:R логику но делает TP реалистичными через адаптивный SL.

        Текущие пропорции (после оптимизации по бэктесту):
        - TP1: 1.0x риска — быстрая фиксация
        - TP2: 1.75x риска — основная цель
        - TP3: 2.5x риска — максимальная цель (только 7.3% достигают)
        """
        risk = abs(entry_price - stop_loss)
        # FIX-AUDIT-6: минимальный multiplier 1.0 гарантирует R:R >= 2.0
        # При floor=0.8 и min SL=2%: weighted R:R = 1.94 < 2.0 (dead zone)
        # score 65 (мин) → multiplier 1.0  → R:R ~2.4
        # score 80 (средн) → multiplier 1.2 → R:R ~2.9
        # score 100 (макс) → multiplier 1.5 → R:R ~3.6
        score_clamped = max(65, min(100, accumulation_score))
        tp_multiplier = 1.0 + (score_clamped - 65) / 35 * 0.5
        tp_multiplier = round(tp_multiplier, 2)
        if risk == 0:
            risk = entry_price * Decimal("0.05")  # защита от нуля

        take_profits = []

        # TP1: 1.0x риска (было 1.2x, снижено для быстрой фиксации) → portion 35%
        tp1_distance = risk * Decimal(str(round(1.0 * tp_multiplier, 2)))  # FIX-M-1
        if direction == SignalDirection.LONG:
            tp1_price = entry_price + tp1_distance
        else:
            tp1_price = entry_price - tp1_distance
        tp1_pct = float((tp1_price - entry_price) / entry_price * 100)
        take_profits.append(TakeProfit(
            price=self._round_price(tp1_price),
            percent=round(abs(tp1_pct), 1),
            portion=35,  # FIX-J-4: было 30
            label="TP1"
        ))

        # TP2: 1.75x риска (было 2.5x, снижено) → portion 40%
        tp2_distance = risk * Decimal(str(round(1.75 * tp_multiplier, 2)))  # FIX-M-1
        if direction == SignalDirection.LONG:
            tp2_price = entry_price + tp2_distance
        else:
            tp2_price = entry_price - tp2_distance
        tp2_pct = float((tp2_price - entry_price) / entry_price * 100)
        take_profits.append(TakeProfit(
            price=self._round_price(tp2_price),
            percent=round(abs(tp2_pct), 1),
            portion=40,
            label="TP2"
        ))

        # TP3: 2.5x риска (было 4.0x, снижено - только 7.3% достигают TP3) → portion 25%
        tp3_distance = risk * Decimal(str(round(2.5 * tp_multiplier, 2)))  # FIX-M-1
        if direction == SignalDirection.LONG:
            tp3_price = entry_price + tp3_distance
        else:
            tp3_price = entry_price - tp3_distance
        tp3_pct = float((tp3_price - entry_price) / entry_price * 100)
        take_profits.append(TakeProfit(
            price=self._round_price(tp3_price),
            percent=round(abs(tp3_pct), 1),
            portion=25,  # FIX-J-4: было 30
            label="TP3"
        ))

        return take_profits

    def _calculate_risk_reward(
        self,
        direction: SignalDirection,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profits: List[TakeProfit]
    ) -> float:
        """Рассчитать Risk:Reward ratio."""
        if not take_profits:
            return 0.0

        risk = abs(float(entry_price - stop_loss))
        if risk == 0:
            return 0.0

        # Используем средневзвешенный TP
        weighted_reward = sum(
            float(abs(tp.price - entry_price)) * tp.portion / 100
            for tp in take_profits
        )

        return round(weighted_reward / risk, 2)

    def _round_price(self, price: Decimal) -> Decimal:
        """
        Округлить цену до разумной точности.
        """
        if price <= 0:  # FIX-B-3: защита от нулевой или отрицательной цены
            return Decimal("0")
        if price >= 10000:
            return price.quantize(Decimal("1"), rounding=ROUND_DOWN)       # FIX-15: BTC >10k → до $1
        elif price >= 1000:
            return price.quantize(Decimal("0.1"), rounding=ROUND_DOWN)     # FIX-15: 1k-10k → до $0.1
        elif price >= 100:
            return price.quantize(Decimal("0.01"), rounding=ROUND_DOWN)    # FIX-15: 100-1k → до $0.01
        elif price >= 1:
            return price.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)  # FIX-15: 1-100 → до $0.0001
        else:
            return price.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)  # FIX-15: <$1
