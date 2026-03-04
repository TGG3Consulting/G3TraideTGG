# -*- coding: utf-8 -*-
"""
Модели для торговых сигналов.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Dict, Any


class SignalDirection(Enum):
    """Направление сигнала."""
    LONG = "LONG"
    SHORT = "SHORT"


class SignalConfidence(Enum):
    """Уровень уверенности в сигнале."""
    LOW = "НИЗКАЯ"
    MEDIUM = "СРЕДНЯЯ"
    HIGH = "ВЫСОКАЯ"
    VERY_HIGH = "ОЧЕНЬ ВЫСОКАЯ"


class SignalType(Enum):
    """Тип торгового сигнала."""
    ACCUMULATION = "НАКОПЛЕНИЕ"           # Кит набирает позицию
    SQUEEZE_SETUP = "СКВИЗ"               # Short/Long squeeze setup
    BREAKOUT = "ПРОБОЙ"                   # Breakout incoming
    DIVERGENCE = "ДИВЕРГЕНЦИЯ"            # OI/Price divergence
    CROSS_EXCHANGE = "КРОСС-БИРЖЕВОЙ"     # Cross-exchange signal


@dataclass
class TakeProfit:
    """Уровень тейк-профита."""
    price: Decimal
    percent: float           # % от входа
    portion: int             # % позиции для закрытия (30%, 40%, 30%)
    label: str               # "TP1", "TP2", "TP3"


@dataclass
class AccumulationScore:
    """
    Оценка фазы накопления.

    Каждый фактор добавляет баллы к общему скору.
    Максимум ~115 баллов, нормализуется до 100.
    """
    # OI факторы (макс 35)
    oi_growth: int = 0           # OI растёт: 0-20
    oi_stability: int = 0        # OI стабильно растёт (не скачки): 0-15

    # Funding факторы (макс 25)
    funding_cheap: int = 0       # Funding negative = лонги дешёвые: 0-15
    funding_gradient: int = 0    # Funding падает = накопление: 0-10

    # Sentiment факторы (макс 20)
    crowd_bearish: int = 0       # Толпа в шортах: 0-20
    crowd_bullish: int = 0       # FIX-N-3: Толпа в лонгах (contrarian SHORT): 0-20

    # Volume/Trade факторы (макс 20)
    coordinated_buying: int = 0  # Координированные покупки: 0-10
    volume_accumulation: int = 0 # Объём без движения цены: 0-10
    oi_spike_bonus: int = 0      # OI Spike bonus: 0-10

    # Cross-exchange факторы (макс 15)
    cross_oi_migration: int = 0  # OI мигрирует на биржу: 0-10
    cross_price_lead: int = 0    # Биржа ведёт цену: 0-5

    # ========== ORDERBOOK ФАКТОРЫ (макс 25) ==========
    # SPOT orderbook
    spot_bid_pressure: int = 0      # Сильный bid wall (покупатели): 0-10
    spot_ask_weakness: int = 0      # Слабые asks (мало продавцов): 0-5
    spot_imbalance_score: int = 0   # Общий дисбаланс SPOT: 0-5

    # FUTURES orderbook
    futures_bid_pressure: int = 0   # Сильный bid wall на futures: 0-10
    futures_ask_weakness: int = 0   # Слабые asks на futures: 0-5
    futures_imbalance_score: int = 0  # Общий дисбаланс FUTURES: 0-5

    # SPOT-FUTURES divergence
    orderbook_divergence: int = 0   # Разница между SPOT и FUTURES стаканами: 0-5

    # Негативные факторы (вычитаются)
    wash_trading_penalty: int = 0    # Wash trading detected: -10 to 0
    extreme_funding_penalty: int = 0 # Funding уже extreme: -15 to 0
    orderbook_against_penalty: int = 0  # Orderbook против направления: -10 to 0

    @property
    def total(self) -> int:
        """Общий скор накопления (0-100)."""
        positive = (
            self.oi_growth +
            self.oi_stability +
            self.funding_cheap +
            self.funding_gradient +
            self.crowd_bearish +
            self.crowd_bullish +   # FIX-R-1: SHORT сигналы тоже должны проходить порог
            self.coordinated_buying +
            self.volume_accumulation +
            self.oi_spike_bonus +
            self.cross_oi_migration +
            self.cross_price_lead +
            self.spot_bid_pressure +
            self.spot_ask_weakness +
            self.spot_imbalance_score +
            self.futures_bid_pressure +
            self.futures_ask_weakness +
            self.futures_imbalance_score +
            self.orderbook_divergence
        )
        negative = (
            self.wash_trading_penalty +
            self.extreme_funding_penalty +
            self.orderbook_against_penalty
        )
        return max(0, min(100, positive + negative))

    @property
    def orderbook_total(self) -> int:
        """Сумма orderbook факторов (для диагностики)."""
        return max(0, (  # FIX-16: отрицательный orderbook_total ломает отображение в UI
            self.spot_bid_pressure +
            self.spot_ask_weakness +
            self.spot_imbalance_score +
            self.futures_bid_pressure +
            self.futures_ask_weakness +
            self.futures_imbalance_score +
            self.orderbook_divergence +
            self.orderbook_against_penalty
        ))

    def to_dict(self) -> Dict[str, int]:
        """Конвертировать в словарь для логирования."""
        return {
            "oi_growth": self.oi_growth,
            "oi_stability": self.oi_stability,
            "funding_cheap": self.funding_cheap,
            "funding_gradient": self.funding_gradient,
            "crowd_bearish": self.crowd_bearish,
            "crowd_bullish": self.crowd_bullish,
            "coordinated_buying": self.coordinated_buying,
            "volume_accumulation": self.volume_accumulation,
            "oi_spike_bonus": self.oi_spike_bonus,
            "cross_oi_migration": self.cross_oi_migration,
            "cross_price_lead": self.cross_price_lead,
            # Orderbook
            "spot_bid_pressure": self.spot_bid_pressure,
            "spot_ask_weakness": self.spot_ask_weakness,
            "spot_imbalance_score": self.spot_imbalance_score,
            "futures_bid_pressure": self.futures_bid_pressure,
            "futures_ask_weakness": self.futures_ask_weakness,
            "futures_imbalance_score": self.futures_imbalance_score,
            "orderbook_divergence": self.orderbook_divergence,
            "orderbook_total": self.orderbook_total,
            # Penalties
            "wash_trading_penalty": self.wash_trading_penalty,
            "extreme_funding_penalty": self.extreme_funding_penalty,
            "orderbook_against_penalty": self.orderbook_against_penalty,
            "total": self.total,
        }


@dataclass
class TradeSignal:
    """
    Торговый сигнал.

    Содержит всю информацию для открытия позиции.
    """
    # Идентификация
    signal_id: str
    symbol: str
    timestamp: datetime

    # Направление и тип
    direction: SignalDirection
    signal_type: SignalType
    confidence: SignalConfidence
    probability: int              # 0-100%

    # Уровни входа
    entry_zone_low: Decimal       # Нижняя граница зоны входа
    entry_zone_high: Decimal      # Верхняя граница
    entry_limit: Decimal          # Рекомендуемый лимитный ордер
    current_price: Decimal        # Текущая цена

    # Риск-менеджмент
    stop_loss: Decimal
    stop_loss_pct: float          # % от входа
    take_profits: List[TakeProfit] = field(default_factory=list)

    # Risk/Reward
    risk_reward_ratio: float = 0.0

    # Время действия
    valid_hours: int = 24         # Сколько часов сигнал валиден

    # Доказательства (почему сигнал)
    evidence: List[str] = field(default_factory=list)

    # Детали из BinanceFriend
    details: Dict[str, Any] = field(default_factory=dict)

    # Сценарии
    scenarios: Dict[str, str] = field(default_factory=dict)

    # Триггеры (какие детекции вызвали сигнал)
    trigger_detections: List[str] = field(default_factory=list)

    # Ссылки
    links: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Конвертировать в словарь."""
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "direction": self.direction.value,
            "signal_type": self.signal_type.value,
            "confidence": self.confidence.value,
            "probability": self.probability,
            "entry_zone": {
                "low": str(self.entry_zone_low),
                "high": str(self.entry_zone_high),
                "limit": str(self.entry_limit),
            },
            "current_price": str(self.current_price),
            "stop_loss": str(self.stop_loss),
            "stop_loss_pct": self.stop_loss_pct,
            "take_profits": [
                {"label": tp.label, "price": str(tp.price), "percent": tp.percent, "portion": tp.portion}
                for tp in self.take_profits
            ],
            "risk_reward": self.risk_reward_ratio,
            "valid_hours": self.valid_hours,
            "evidence": self.evidence,
            "details": self.details,
            "scenarios": self.scenarios,
            "trigger_detections": self.trigger_detections,
        }


@dataclass
class SignalConfig:
    """Конфигурация генератора сигналов."""
    # Минимальные пороги
    min_accumulation_score: int = 50       # Было 65, снижено (Score 50+ прибылен в 83% файлов)
    min_probability: int = 55              # Было 60→50, теперь 55 (Prob 55 > Prob 50 по паттерну)

    # Confidence thresholds
    confidence_low: int = 50
    confidence_medium: int = 65
    confidence_high: int = 80
    confidence_very_high: int = 90

    # Risk management defaults
    default_sl_pct: float = 7.0            # Default SL %
    min_risk_reward: float = 2.0           # Мин R:R

    # TP levels (% от входа до SL)
    tp1_ratio: float = 1.5                 # TP1 = 1.5x риска
    tp2_ratio: float = 3.0                 # TP2 = 3x риска
    tp3_ratio: float = 5.0                 # TP3 = 5x риска

    # TP portions (% позиции)
    tp1_portion: int = 30
    tp2_portion: int = 40
    tp3_portion: int = 30

    # Validity
    default_valid_hours: int = 24

    # OI thresholds for accumulation
    oi_growth_min: float = 5.0             # Мин рост OI для сигнала
    oi_growth_strong: float = 15.0         # Сильный рост OI

    # Funding thresholds
    funding_cheap_threshold: float = -0.01  # Funding < -0.01% = лонги дешёвые
    funding_extreme_threshold: float = 0.05 # Funding > 0.05% = экстремально

    # Crowd sentiment
    crowd_short_threshold: float = 55.0    # >55% шортов = bearish crowd
    crowd_extreme_short: float = 60.0      # >60% = очень bearish

    # OI Spike bonus
    oi_spike_bonus_points: int = 10        # Баллы за OI_SPIKE детекцию

    # Blacklist токсичных монет (1 монета = 80%+ убытков в 83% файлов бэктеста)
    symbol_blacklist: List[str] = field(default_factory=lambda: ["COMPUSDT", "YFIUSDT", "KSMUSDT"])
    # Blocked hours UTC (часы 10-12 UTC убыточны в 67% файлов бэктеста)
    blocked_hours_utc: List[int] = field(default_factory=lambda: [10, 11, 12])
    # Blocked weekdays (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun)
    blocked_weekdays: List[int] = field(default_factory=lambda: [0])  # Понедельник
    # Max volume spike (spike > 2.0 = FOMO, убыточен в 67% файлов)
    max_volume_spike: float = 2.0

    @classmethod
    def from_settings(cls) -> "SignalConfig":
        """Create SignalConfig from global settings."""
        try:
            from config.settings import settings
            s = settings.signals
            return cls(
                min_accumulation_score=s.min_accumulation_score,
                min_probability=s.min_probability,
                confidence_low=s.confidence_low,
                confidence_medium=s.confidence_medium,
                confidence_high=s.confidence_high,
                confidence_very_high=s.confidence_very_high,
                default_sl_pct=s.default_sl_pct,
                min_risk_reward=s.min_risk_reward,
                tp1_ratio=s.tp1_ratio,
                tp2_ratio=s.tp2_ratio,
                tp3_ratio=s.tp3_ratio,
                tp1_portion=s.tp1_portion,
                tp2_portion=s.tp2_portion,
                tp3_portion=s.tp3_portion,
                default_valid_hours=s.default_valid_hours,
                oi_growth_min=s.oi_growth_min,
                oi_growth_strong=s.oi_growth_strong,
                funding_cheap_threshold=s.funding_cheap_threshold,
                funding_extreme_threshold=s.funding_extreme_threshold,
                crowd_short_threshold=s.crowd_short_threshold,
                crowd_extreme_short=s.crowd_extreme_short,
            )
        except Exception:
            return cls()  # Return defaults if settings not available
