# -*- coding: utf-8 -*-
"""
Trade Bot API Server - HTTP сервер для приёма сигналов.

Endpoints:
- POST /signal - Принять новый сигнал
- GET /status - Статус бота
- GET /positions - Список позиций
- GET /balance - Баланс
- DELETE /signal/{signal_id} - Отменить сигнал
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Callable, Awaitable

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware

from .schemas import (
    SignalRequest,
    SignalResponse,
    PositionResponse,
    BalanceResponse,
    StatusResponse,
    ErrorResponse,
)
from ..core.models import TradeSignal, SignalAction

logger = logging.getLogger(__name__)


class SignalAPI:
    """
    HTTP API сервер для приёма сигналов.

    Принимает сигналы от telegram_runner.py и вызывает callback для обработки.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        on_signal: Optional[Callable[[TradeSignal], Awaitable[str]]] = None,
    ):
        """
        Инициализация API сервера.

        Args:
            host: Хост для прослушивания
            port: Порт
            on_signal: Callback для обработки сигналов (async)
        """
        self.host = host
        self.port = port
        self.on_signal = on_signal

        # FastAPI app
        self.app = FastAPI(
            title="Trade Bot API",
            description="API для приёма торговых сигналов",
            version="0.1.0",
        )

        # CORS
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Статистика
        self.start_time = datetime.utcnow()
        self.signals_received = 0
        self.signals_processed = 0
        self.signals_failed = 0

        # Регистрация endpoints
        self._register_routes()

    def _register_routes(self):
        """Регистрация маршрутов."""

        @self.app.get("/", response_model=StatusResponse)
        async def root():
            """Корневой endpoint - статус."""
            return await self._get_status()

        @self.app.get("/health")
        async def health():
            """Health check."""
            return {"status": "ok"}

        @self.app.get("/status", response_model=StatusResponse)
        async def status():
            """Статус бота."""
            return await self._get_status()

        @self.app.post("/signal", response_model=SignalResponse)
        async def receive_signal(request: SignalRequest, background_tasks: BackgroundTasks):
            """
            Принять новый торговый сигнал.

            Сигнал валидируется и передаётся на обработку.
            """
            self.signals_received += 1

            try:
                # Конвертируем в TradeSignal
                signal = self._request_to_signal(request)

                # Логируем
                logger.info(
                    f"Signal received: {signal.signal_id} "
                    f"{signal.direction} {signal.symbol} @ {signal.entry_price}"
                )

                # Обрабатываем
                if self.on_signal:
                    position_id = await self.on_signal(signal)
                    self.signals_processed += 1

                    return SignalResponse(
                        success=True,
                        signal_id=signal.signal_id,
                        message="Signal accepted and processed",
                        position_id=position_id,
                    )
                else:
                    # Нет обработчика - просто принимаем
                    return SignalResponse(
                        success=True,
                        signal_id=signal.signal_id,
                        message="Signal received (no handler configured)",
                    )

            except ValueError as e:
                self.signals_failed += 1
                logger.error(f"Signal validation error: {e}")
                raise HTTPException(status_code=400, detail=str(e))

            except Exception as e:
                self.signals_failed += 1
                logger.exception(f"Signal processing error: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/signal/{signal_id}", response_model=SignalResponse)
        async def cancel_signal(signal_id: str):
            """Отменить сигнал (закрыть позицию если открыта)."""
            # TODO: Реализовать отмену
            return SignalResponse(
                success=False,
                signal_id=signal_id,
                message="Not implemented yet",
            )

        @self.app.get("/positions", response_model=List[PositionResponse])
        async def get_positions():
            """Получить список открытых позиций."""
            # TODO: Реализовать
            return []

        @self.app.get("/balance", response_model=BalanceResponse)
        async def get_balance():
            """Получить баланс."""
            # TODO: Реализовать
            return BalanceResponse(available=0.0, total=0.0, asset="USDT")

    def _request_to_signal(self, request: SignalRequest) -> TradeSignal:
        """Конвертировать API запрос в TradeSignal."""
        # Парсим signal_date если есть
        signal_date = None
        if request.signal_date:
            try:
                signal_date = datetime.fromisoformat(request.signal_date)
            except ValueError:
                pass

        # Парсим action
        action_map = {
            "FULL": SignalAction.FULL,
            "DYN": SignalAction.DYN,
            "OFF": SignalAction.OFF,
        }
        action = action_map.get(request.action.upper(), SignalAction.FULL)

        return TradeSignal(
            signal_id=request.signal_id,
            symbol=request.symbol,
            direction=request.direction,
            entry_price=request.entry_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            strategy=request.strategy,
            signal_date=signal_date,
            reason=request.reason,
            action=action,
            sl_pct=request.sl_pct,
            tp_pct=request.tp_pct,
            coin_regime=request.coin_regime,
            coin_volatility=request.coin_volatility,
            metadata=request.metadata,
        )

    async def _get_status(self) -> StatusResponse:
        """Получить статус бота."""
        uptime = (datetime.utcnow() - self.start_time).total_seconds()

        return StatusResponse(
            status="running",
            exchange="not_connected",  # TODO: Из engine
            is_testnet=True,
            is_connected=False,
            open_positions=0,
            pending_signals=0,
            balance_usdt=0.0,
            uptime_seconds=uptime,
        )

    async def start(self):
        """Запустить сервер."""
        import uvicorn

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        await server.serve()

    def run(self):
        """Запустить сервер (синхронно)."""
        import uvicorn

        uvicorn.run(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
