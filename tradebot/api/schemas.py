# -*- coding: utf-8 -*-
"""
Trade Bot API Schemas - Pydantic модели для API.

Определяют структуру запросов и ответов.
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator


class SignalRequest(BaseModel):
    """
    Запрос на создание сигнала.

    Структура соответствует данным из telegram_runner.py.
    """
    # === ОБЯЗАТЕЛЬНЫЕ ===
    signal_id: str = Field(..., description="Уникальный ID сигнала", example="20240115_BTCUSDT_LONG")
    symbol: str = Field(..., description="Торговая пара", example="BTCUSDT")
    direction: str = Field(..., description="LONG или SHORT", example="LONG")
    entry_price: float = Field(..., gt=0, description="Цена входа", example=42500.0)
    stop_loss: float = Field(..., gt=0, description="Цена стоп-лосса", example=40375.0)
    take_profit: float = Field(..., gt=0, description="Цена тейк-профита", example=46750.0)

    # === ОПЦИОНАЛЬНЫЕ ===
    strategy: str = Field("", description="Название стратегии", example="ls_fade")
    signal_date: Optional[str] = Field(None, description="Дата сигнала ISO", example="2024-01-15T00:00:00")
    reason: str = Field("", description="Причина сигнала")

    # === SIZING ===
    action: str = Field("FULL", description="FULL/DYN/OFF", example="FULL")

    # === КОНТЕКСТ ===
    sl_pct: float = Field(0.0, ge=0, description="SL в %", example=5.0)
    tp_pct: float = Field(0.0, ge=0, description="TP в %", example=10.0)
    coin_regime: str = Field("", description="Режим монеты", example="BULL")
    coin_volatility: float = Field(0.0, ge=0, description="Волатильность %", example=3.5)

    # === METADATA ===
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator("direction")
    def validate_direction(cls, v):
        v = v.upper()
        if v not in ("LONG", "SHORT"):
            raise ValueError("direction must be LONG or SHORT")
        return v

    @validator("symbol")
    def validate_symbol(cls, v):
        return v.upper()

    @validator("action")
    def validate_action(cls, v):
        v = v.upper()
        if v not in ("FULL", "DYN", "OFF"):
            raise ValueError("action must be FULL, DYN or OFF")
        return v

    class Config:
        schema_extra = {
            "example": {
                "signal_id": "20240115_BTCUSDT_LONG",
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "entry_price": 42500.0,
                "stop_loss": 40375.0,
                "take_profit": 46750.0,
                "strategy": "ls_fade",
                "action": "FULL",
                "sl_pct": 5.0,
                "tp_pct": 10.0,
                "coin_regime": "BULL",
            }
        }


class SignalResponse(BaseModel):
    """Ответ на запрос сигнала."""
    success: bool
    signal_id: str
    message: str
    position_id: Optional[str] = None
    error: Optional[str] = None


class PositionResponse(BaseModel):
    """Информация о позиции."""
    position_id: str
    signal_id: str
    symbol: str
    side: str
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float
    status: str
    unrealized_pnl: float
    created_at: Optional[str] = None
    opened_at: Optional[str] = None


class BalanceResponse(BaseModel):
    """Информация о балансе."""
    available: float
    total: float
    asset: str = "USDT"


class StatusResponse(BaseModel):
    """Статус бота."""
    status: str  # "running", "stopped", "error"
    exchange: str
    is_testnet: bool
    is_connected: bool
    open_positions: int
    pending_signals: int
    balance_usdt: float
    uptime_seconds: float


class ErrorResponse(BaseModel):
    """Ответ об ошибке."""
    success: bool = False
    error: str
    error_code: Optional[str] = None
