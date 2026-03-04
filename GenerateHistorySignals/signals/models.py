# -*- coding: utf-8 -*-
"""
Модели для торговых сигналов.
Копия из BinanceFriend/src/signals/models.py
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
    ACCUMULATION = "НАКОПЛЕНИЕ"
    SQUEEZE_SETUP = "СКВИЗ"
    BREAKOUT = "ПРОБОЙ"
    DIVERGENCE = "ДИВЕРГЕНЦИЯ"
    CROSS_EXCHANGE = "КРОСС-БИРЖЕВОЙ"


@dataclass
class TakeProfit:
    """Уровень тейк-профита."""
    price: Decimal
    percent: float
    portion: int
    label: str


@dataclass
class AccumulationScore:
    """
    Оценка фазы накопления.
    Каждый фактор добавляет баллы к общему скору.
    """
    # OI факторы (макс 35)
    oi_growth: int = 0
    oi_stability: int = 0

    # Funding факторы (макс 25)
    funding_cheap: int = 0
    funding_gradient: int = 0

    # Sentiment факторы (макс 20)
    crowd_bearish: int = 0
    crowd_bullish: int = 0

    # Volume/Trade факторы (макс 20)
    coordinated_buying: int = 0
    volume_accumulation: int = 0
    oi_spike_bonus: int = 0

    # Cross-exchange факторы (макс 15)
    cross_oi_migration: int = 0
    cross_price_lead: int = 0

    # ORDERBOOK ФАКТОРЫ (макс 25)
    spot_bid_pressure: int = 0
    spot_ask_weakness: int = 0
    spot_imbalance_score: int = 0
    futures_bid_pressure: int = 0
    futures_ask_weakness: int = 0
    futures_imbalance_score: int = 0
    orderbook_divergence: int = 0

    # Негативные факторы
    wash_trading_penalty: int = 0
    extreme_funding_penalty: int = 0
    orderbook_against_penalty: int = 0

    @property
    def total(self) -> int:
        """Общий скор накопления (0-100)."""
        positive = (
            self.oi_growth +
            self.oi_stability +
            self.funding_cheap +
            self.funding_gradient +
            self.crowd_bearish +
            self.crowd_bullish +
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
        """Сумма orderbook факторов."""
        return max(0, (
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
        """Конвертировать в словарь."""
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
            "spot_bid_pressure": self.spot_bid_pressure,
            "spot_ask_weakness": self.spot_ask_weakness,
            "spot_imbalance_score": self.spot_imbalance_score,
            "futures_bid_pressure": self.futures_bid_pressure,
            "futures_ask_weakness": self.futures_ask_weakness,
            "futures_imbalance_score": self.futures_imbalance_score,
            "orderbook_divergence": self.orderbook_divergence,
            "orderbook_total": self.orderbook_total,
            "wash_trading_penalty": self.wash_trading_penalty,
            "extreme_funding_penalty": self.extreme_funding_penalty,
            "orderbook_against_penalty": self.orderbook_against_penalty,
            "total": self.total,
        }


@dataclass
class TradeSignal:
    """Торговый сигнал."""
    signal_id: str
    symbol: str
    timestamp: datetime
    direction: SignalDirection
    signal_type: SignalType
    confidence: SignalConfidence
    probability: int

    entry_zone_low: Decimal
    entry_zone_high: Decimal
    entry_limit: Decimal
    current_price: Decimal

    stop_loss: Decimal
    stop_loss_pct: float
    take_profits: List[TakeProfit] = field(default_factory=list)

    risk_reward_ratio: float = 0.0
    valid_hours: int = 24
    evidence: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    scenarios: Dict[str, str] = field(default_factory=dict)
    trigger_detections: List[str] = field(default_factory=list)
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
    min_accumulation_score: int = 50  # Было 65, снижено на основе бэктеста (Score 50+ прибылен в 83% файлов)
    min_probability: int = 55  # Было 60→50, теперь 55 (Prob 55 > Prob 50 по паттерну)
    confidence_low: int = 50
    confidence_medium: int = 65
    confidence_high: int = 80
    confidence_very_high: int = 90
    default_sl_pct: float = 7.0
    min_risk_reward: float = 1.5  # снижено для честного порога без хака TP
    tp1_ratio: float = 1.5
    tp2_ratio: float = 3.0
    tp3_ratio: float = 5.0
    tp1_portion: int = 30
    tp2_portion: int = 40
    tp3_portion: int = 30
    default_valid_hours: int = 24
    oi_growth_min: float = 5.0
    oi_growth_strong: float = 15.0
    funding_cheap_threshold: float = -0.01
    funding_extreme_threshold: float = 0.05
    crowd_short_threshold: float = 55.0
    crowd_extreme_short: float = 60.0
    oi_spike_bonus_points: int = 10
    # Blacklist токсичных монет (1 монета = 80%+ убытков в 83% файлов бэктеста)
    symbol_blacklist: List[str] = field(default_factory=lambda: ["COMPUSDT", "YFIUSDT", "KSMUSDT"])
    # Blocked hours UTC (часы 10-12 UTC убыточны в 67% файлов бэктеста)
    blocked_hours_utc: List[int] = field(default_factory=lambda: [10, 11, 12])
    # Blocked weekdays (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun)
    blocked_weekdays: List[int] = field(default_factory=lambda: [0])  # Понедельник
    # Max volume spike (spike > 2.0 = FOMO, убыточен в 67% файлов)
    max_volume_spike: float = 2.0
