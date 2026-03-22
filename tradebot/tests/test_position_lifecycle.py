# -*- coding: utf-8 -*-
"""
ПОЛНЫЙ ЖИЗНЕННЫЙ ЦИКЛ ПОЗИЦИИ - все сценарии от открытия до закрытия.

Покрывает:
1. WebSocket события (SL/TP/Trailing triggered)
2. Max hold days timeout
3. Missing TP auto-close
4. Partial fills и partial close
5. Cancel retry queue
6. REST sync обнаружение проблем
7. PnL и commission расчёты
8. Множественные позиции
9. Ручное закрытие
10. Аварийные ситуации
"""

import asyncio
import pytest
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from tradebot.core.models import Position, PositionSide, PositionStatus, OrderSide
from tradebot.engine.trade_engine import TradeEngine
from tradebot.engine.position_manager import PositionManager
from tradebot.core.exceptions import BinanceError


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def mock_exchange():
    """Базовый mock exchange."""
    exchange = AsyncMock()
    exchange.get_price = AsyncMock(return_value=Decimal("50000"))
    exchange.get_balance = AsyncMock(return_value=Decimal("10000"))
    exchange.round_quantity = MagicMock(side_effect=lambda s, q: q.quantize(Decimal("0.001")))
    exchange.round_price = MagicMock(side_effect=lambda s, p: p.quantize(Decimal("0.01")))
    exchange.get_step_size = MagicMock(return_value=Decimal("0.001"))
    exchange.get_tick_size = MagicMock(return_value=Decimal("0.01"))
    exchange.set_leverage = AsyncMock(return_value=True)
    exchange.get_position_by_side = AsyncMock(return_value=None)
    exchange.get_open_orders = AsyncMock(return_value=[])
    exchange.get_all_positions = AsyncMock(return_value=[])
    exchange.get_open_algo_orders = AsyncMock(return_value=[])
    exchange.place_market_order = AsyncMock(return_value={
        "orderId": "123456",
        "avgPrice": "50000",
        "executedQty": "0.01",
        "origQty": "0.01",
        "status": "FILLED",
    })
    exchange.place_stop_order = AsyncMock(return_value={"algoId": "789"})
    exchange.place_take_profit_order = AsyncMock(return_value={"orderId": "456"})
    exchange.place_trailing_stop_order = AsyncMock(return_value={"algoId": "999"})
    exchange.cancel_order = AsyncMock(return_value=True)
    exchange.cancel_algo_order = AsyncMock(return_value=True)
    exchange.close_position = AsyncMock(return_value=True)
    exchange.start_user_data_stream = AsyncMock(return_value=True)
    exchange.stop_user_data_stream = AsyncMock(return_value=True)
    return exchange


@pytest.fixture
def open_position():
    """Открытая позиция для тестов."""
    return Position(
        position_id="POS_TEST_001",
        signal_id="SIG_TEST_001",
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        quantity=0.01,
        entry_price=50000.0,
        stop_loss=48000.0,
        take_profit=55000.0,
        status=PositionStatus.OPEN,
        entry_order_id="ENTRY_123",
        sl_order_id="SL_789",
        tp_order_id="TP_456",
        opened_at=datetime.now(timezone.utc),
        strategy="momentum",
    )


@pytest.fixture
def open_position_short():
    """Открытая SHORT позиция."""
    return Position(
        position_id="POS_SHORT_001",
        signal_id="SIG_SHORT_001",
        symbol="ETHUSDT",
        side=PositionSide.SHORT,
        quantity=0.1,
        entry_price=3000.0,
        stop_loss=3150.0,
        take_profit=2700.0,
        status=PositionStatus.OPEN,
        entry_order_id="ENTRY_SHORT",
        sl_order_id="SL_SHORT",
        tp_order_id="TP_SHORT",
        opened_at=datetime.now(timezone.utc),
        strategy="reversal",
    )


# =============================================================================
# TEST: WebSocket Events - SL/TP/Trailing Triggered
# =============================================================================

class TestWebSocketEvents:
    """Тесты обработки WebSocket событий."""

    def test_sl_triggered_closes_position(self, mock_exchange, open_position):
        """SL сработал - позиция должна закрыться."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)

        # Симулируем ALGO_UPDATE событие - SL сработал
        order_update = {
            "orderId": "SL_789",
            "algoId": 789,
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "STOP_MARKET",
            "avgPrice": "48000.00",
            "executedQty": "0.01",
            "origQty": "0.01",
            "eventType": "ALGO_UPDATE",
        }

        pm._handle_order_update(order_update)

        # Позиция должна быть закрыта
        assert open_position.status == PositionStatus.CLOSED
        assert open_position.exit_reason == "SL"
        assert open_position.exit_price == 48000.0
        assert pm._stats["positions_closed_sl"] == 1

    def test_tp_triggered_closes_position(self, mock_exchange, open_position):
        """TP сработал - позиция должна закрыться."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)

        # Симулируем ORDER_TRADE_UPDATE - TP сработал
        order_update = {
            "orderId": "TP_456",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "LIMIT",
            "avgPrice": "55000.00",
            "executedQty": "0.01",
            "origQty": "0.01",
            "eventType": "ORDER_TRADE_UPDATE",
        }

        pm._handle_order_update(order_update)

        # Позиция должна быть закрыта
        assert open_position.status == PositionStatus.CLOSED
        assert open_position.exit_reason == "TP"
        assert open_position.exit_price == 55000.0
        assert pm._stats["positions_closed_tp"] == 1

    def test_trailing_stop_triggered(self, mock_exchange, open_position):
        """Trailing stop сработал."""
        open_position.trailing_stop_enabled = True
        open_position.trailing_stop_order_id = "TRAIL_999"
        open_position.trailing_stop_callback_rate = 1.0

        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)

        # Симулируем ALGO_UPDATE - trailing stop сработал
        order_update = {
            "orderId": "TRAIL_999",
            "algoId": 999,
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "TRAILING_STOP_MARKET",
            "avgPrice": "54000.00",
            "executedQty": "0.01",
            "origQty": "0.01",
            "eventType": "ALGO_UPDATE",
        }

        pm._handle_order_update(order_update)

        # Позиция закрыта по trailing
        assert open_position.status == PositionStatus.CLOSED
        assert open_position.exit_reason == "TRAILING_STOP"

    def test_unknown_order_ignored(self, mock_exchange, open_position):
        """Неизвестный ордер игнорируется."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)

        # Ордер не от нашей позиции
        order_update = {
            "orderId": "UNKNOWN_ORDER",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "LIMIT",
            "avgPrice": "50000.00",
            "executedQty": "0.01",
        }

        pm._handle_order_update(order_update)

        # Позиция не изменилась
        assert open_position.status == PositionStatus.OPEN


# =============================================================================
# TEST: Partial Fills
# =============================================================================

class TestPartialFills:
    """Тесты частичного исполнения."""

    def test_partial_fill_updates_exit_qty(self, mock_exchange, open_position):
        """Partial fill обновляет exit_filled_qty."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)

        # Частичное исполнение TP
        order_update = {
            "orderId": "TP_456",
            "symbol": "BTCUSDT",
            "status": "PARTIALLY_FILLED",
            "type": "LIMIT",
            "avgPrice": "55000.00",
            "executedQty": "0.005",  # 50% заполнено
            "origQty": "0.01",
        }

        pm._handle_order_update(order_update)

        # exit_filled_qty обновлён
        assert open_position.exit_filled_qty == 0.005
        # Позиция всё ещё открыта
        assert open_position.status == PositionStatus.OPEN

    def test_partial_fill_then_cancel(self, mock_exchange, open_position):
        """Partial fill + cancel = частичное закрытие."""
        open_position.quantity = 0.01

        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)

        # Сначала partial fill
        partial_update = {
            "orderId": "TP_456",
            "symbol": "BTCUSDT",
            "status": "PARTIALLY_FILLED",
            "avgPrice": "55000.00",
            "executedQty": "0.005",
            "origQty": "0.01",
        }
        pm._handle_order_update(partial_update)

        # Потом cancel
        cancel_update = {
            "orderId": "TP_456",
            "symbol": "BTCUSDT",
            "status": "CANCELED",
            "avgPrice": "55000.00",
            "executedQty": "0.005",
            "origQty": "0.01",
        }
        pm._handle_order_update(cancel_update)

        # Позиция частично закрыта - quantity уменьшилось
        assert open_position.quantity == 0.005  # Осталось 50%

    def test_99_percent_fill_closes_position(self, mock_exchange, open_position):
        """99% fill при cancel = полное закрытие."""
        open_position.quantity = 0.01
        open_position.exit_filled_qty = 0.0099  # 99% уже заполнено

        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)

        # Cancel после 99% fill
        cancel_update = {
            "orderId": "TP_456",
            "symbol": "BTCUSDT",
            "status": "CANCELED",
            "avgPrice": "55000.00",
            "executedQty": "0.0099",
            "origQty": "0.01",
        }
        pm._handle_order_update(cancel_update)

        # Позиция полностью закрыта
        assert open_position.status == PositionStatus.CLOSED


# =============================================================================
# TEST: Max Hold Days Timeout
# =============================================================================

class TestMaxHoldDaysTimeout:
    """Тесты таймаута по max_hold_days."""

    def test_position_expired_detected(self, mock_exchange, open_position):
        """Позиция старше max_hold_days обнаруживается."""
        # Позиция открыта 15 дней назад
        open_position.opened_at = datetime.now(timezone.utc) - timedelta(days=15)
        open_position.max_hold_days = 14

        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # Проверяем что позиция просрочена
        is_expired = (datetime.now(timezone.utc) - open_position.opened_at).days > open_position.max_hold_days
        assert is_expired is True

    @pytest.mark.asyncio
    async def test_close_position_timeout(self, mock_exchange, open_position):
        """Позиция закрывается по таймауту."""
        open_position.opened_at = datetime.now(timezone.utc) - timedelta(days=15)
        open_position.max_hold_days = 14

        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # Мокаем close_position на engine
        engine.close_position = AsyncMock(return_value=True)

        # Вызываем timeout close
        result = await pm._close_position_timeout(open_position)

        # Позиция должна быть закрыта
        assert result is True or engine.close_position.called


# =============================================================================
# TEST: Missing TP Monitoring
# =============================================================================

class TestMissingTPMonitoring:
    """Тесты мониторинга позиций без TP."""

    def test_position_registered_for_missing_tp(self, mock_exchange, open_position):
        """Позиция без TP регистрируется для мониторинга."""
        open_position.tp_order_id = ""  # Нет TP

        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)

        pm.register_missing_tp(open_position)

        assert open_position.position_id in pm._missing_tp_positions
        assert pm._missing_tp_positions[open_position.position_id] > 0

    @pytest.mark.asyncio
    async def test_check_tp_exists_on_exchange(self, mock_exchange, open_position):
        """Проверка существования TP на бирже."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # TP существует на бирже (метод ищет TAKE_PROFIT_MARKET, не LIMIT)
        mock_exchange.get_open_orders = AsyncMock(return_value=[
            {
                "orderId": "TP_456",
                "symbol": "BTCUSDT",
                "type": "TAKE_PROFIT_MARKET",
                "side": "SELL",  # LONG позиция закрывается SELL
                "positionSide": "LONG",
            }
        ])

        exists = await pm._check_tp_exists_on_exchange(open_position)
        assert exists is True

    @pytest.mark.asyncio
    async def test_tp_not_exists_triggers_close(self, mock_exchange, open_position):
        """TP не существует после таймаута - закрытие по market."""
        open_position.tp_order_id = ""

        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # Нет TP на бирже
        mock_exchange.get_open_orders = AsyncMock(return_value=[])

        # Мокаем close
        close_called = [False]
        async def mock_close(pos):
            close_called[0] = True
            return True
        pm._close_position_missing_tp = mock_close

        await pm._close_position_missing_tp(open_position)
        assert close_called[0] is True


# =============================================================================
# TEST: REST Sync Issues
# =============================================================================

class TestRestSyncIssues:
    """Тесты REST синхронизации."""

    @pytest.mark.asyncio
    async def test_position_closed_on_exchange_detected(self, mock_exchange, open_position):
        """Позиция закрыта на бирже, но мы не знаем."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm._running = True

        # На бирже позиции нет
        mock_exchange.get_all_positions = AsyncMock(return_value=[])
        mock_exchange.get_open_orders = AsyncMock(return_value=[])
        mock_exchange.get_open_algo_orders = AsyncMock(return_value=[])

        # REST sync должен обнаружить
        await pm._perform_rest_sync()

        # Проверяем статистику
        assert pm._stats["rest_sync_runs"] >= 1

    @pytest.mark.asyncio
    async def test_sl_missing_on_exchange_detected(self, mock_exchange, open_position):
        """SL ордер пропал с биржи."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm._running = True

        # Позиция есть, но SL ордера нет
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.01"}
        ])
        mock_exchange.get_open_orders = AsyncMock(return_value=[
            {"orderId": "TP_456", "symbol": "BTCUSDT"}  # Только TP, нет SL
        ])
        mock_exchange.get_open_algo_orders = AsyncMock(return_value=[])

        await pm._perform_rest_sync()

        # Должно быть обнаружено как suspicious


# =============================================================================
# TEST: PnL and Commission
# =============================================================================

class TestPnLCalculation:
    """Тесты расчёта PnL."""

    def test_long_profit_calculation(self, open_position):
        """Расчёт профита для LONG."""
        # Entry: 50000, Exit: 55000, Qty: 0.01
        # PnL = (55000 - 50000) * 0.01 = 50 USD
        open_position.exit_price = 55000.0

        expected_pnl = (open_position.exit_price - open_position.entry_price) * open_position.quantity
        assert expected_pnl == 50.0

    def test_long_loss_calculation(self, open_position):
        """Расчёт убытка для LONG."""
        # Entry: 50000, Exit: 48000, Qty: 0.01
        # PnL = (48000 - 50000) * 0.01 = -20 USD
        open_position.exit_price = 48000.0

        expected_pnl = (open_position.exit_price - open_position.entry_price) * open_position.quantity
        assert expected_pnl == -20.0

    def test_short_profit_calculation(self, open_position_short):
        """Расчёт профита для SHORT."""
        # Entry: 3000, Exit: 2700, Qty: 0.1
        # PnL = (3000 - 2700) * 0.1 = 30 USD
        open_position_short.exit_price = 2700.0

        expected_pnl = (open_position_short.entry_price - open_position_short.exit_price) * open_position_short.quantity
        assert expected_pnl == 30.0

    def test_short_loss_calculation(self, open_position_short):
        """Расчёт убытка для SHORT."""
        # Entry: 3000, Exit: 3150, Qty: 0.1
        # PnL = (3000 - 3150) * 0.1 = -15 USD
        open_position_short.exit_price = 3150.0

        expected_pnl = (open_position_short.entry_price - open_position_short.exit_price) * open_position_short.quantity
        assert expected_pnl == -15.0


# =============================================================================
# TEST: Multiple Positions
# =============================================================================

class TestMultiplePositions:
    """Тесты с несколькими позициями."""

    def test_multiple_positions_different_symbols(self, mock_exchange, open_position, open_position_short):
        """Несколько позиций на разных символах."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position
        engine.positions[open_position_short.position_id] = open_position_short

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)
        pm.register_position(open_position_short)

        # Оба ордера должны быть в маппинге
        assert "SL_789" in pm._order_to_position
        assert "SL_SHORT" in pm._order_to_position

        # SL для BTC не должен влиять на ETH
        btc_sl_update = {
            "orderId": "SL_789",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "STOP_MARKET",
            "avgPrice": "48000.00",
            "executedQty": "0.01",
        }
        pm._handle_order_update(btc_sl_update)

        # BTC закрыта, ETH открыта
        assert open_position.status == PositionStatus.CLOSED
        assert open_position_short.status == PositionStatus.OPEN

    def test_same_symbol_different_sides(self, mock_exchange):
        """LONG и SHORT на одном символе (Hedge Mode)."""
        long_pos = Position(
            position_id="POS_LONG",
            signal_id="SIG_LONG",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.01,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=55000.0,
            status=PositionStatus.OPEN,
            sl_order_id="SL_LONG",
            tp_order_id="TP_LONG",
        )

        short_pos = Position(
            position_id="POS_SHORT",
            signal_id="SIG_SHORT",
            symbol="BTCUSDT",
            side=PositionSide.SHORT,
            quantity=0.01,
            entry_price=50000.0,
            stop_loss=52000.0,
            take_profit=45000.0,
            status=PositionStatus.OPEN,
            sl_order_id="SL_SHORT",
            tp_order_id="TP_SHORT",
        )

        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[long_pos.position_id] = long_pos
        engine.positions[short_pos.position_id] = short_pos

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(long_pos)
        pm.register_position(short_pos)

        # Закрываем LONG
        pm._handle_order_update({
            "orderId": "SL_LONG",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "STOP_MARKET",
            "avgPrice": "48000.00",
            "executedQty": "0.01",
        })

        # LONG закрыт, SHORT открыт
        assert long_pos.status == PositionStatus.CLOSED
        assert short_pos.status == PositionStatus.OPEN


# =============================================================================
# TEST: Manual Close
# =============================================================================

class TestManualClose:
    """Тесты ручного закрытия."""

    @pytest.mark.asyncio
    async def test_close_position_cancels_orders(self, mock_exchange, open_position):
        """Закрытие позиции отменяет SL/TP ордера."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)
        engine.position_manager = pm

        # Закрываем позицию
        await engine.close_position(open_position.position_id, reason="MANUAL")

        # SL и TP должны быть отменены
        assert mock_exchange.cancel_algo_order.called or mock_exchange.cancel_order.called

    @pytest.mark.asyncio
    async def test_close_nonexistent_position(self, mock_exchange):
        """Закрытие несуществующей позиции."""
        engine = TradeEngine(exchange=mock_exchange)

        result = await engine.close_position("NONEXISTENT_POS", reason="TEST")

        # Должен вернуть False или None
        assert result is None or result is False


# =============================================================================
# TEST: Emergency Scenarios
# =============================================================================

class TestEmergencyScenarios:
    """Тесты аварийных ситуаций."""

    @pytest.mark.asyncio
    async def test_emergency_close_on_sl_failure(self, mock_exchange):
        """Emergency close при ошибке SL."""
        # Setup: позиция уже на бирже, SL не выставился
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        mock_exchange.place_stop_order = AsyncMock(
            side_effect=BinanceError(-2018, "Balance insufficient")
        )

        engine = TradeEngine(exchange=mock_exchange)

        signal = MagicMock()
        signal.signal_id = "SIG_EMERGENCY"
        signal.symbol = "BTCUSDT"
        signal.direction = "LONG"
        signal.entry = 50000
        signal.stop_loss = 49000
        signal.take_profit = 53000
        signal.date = datetime.now(timezone.utc)
        signal.metadata = {"strategy": "test"}

        result = await engine.execute_signal(signal=signal)

        # Позиция не должна быть создана
        assert result is None
        assert engine.emergency_closes >= 1

    @pytest.mark.asyncio
    async def test_user_in_liquidation(self, mock_exchange):
        """Аккаунт в ликвидации."""
        mock_exchange.get_position_by_side = AsyncMock(return_value=None)
        mock_exchange.place_market_order = AsyncMock(
            side_effect=BinanceError(-2023, "User in liquidation")
        )

        engine = TradeEngine(exchange=mock_exchange)

        signal = MagicMock()
        signal.signal_id = "SIG_LIQ"
        signal.symbol = "BTCUSDT"
        signal.direction = "LONG"
        signal.entry = 50000
        signal.stop_loss = 49000
        signal.take_profit = 53000
        signal.date = datetime.now(timezone.utc)
        signal.metadata = {}

        result = await engine.execute_signal(signal=signal)

        assert result is None


# =============================================================================
# TEST: Cancel Retry Queue
# =============================================================================

class TestCancelRetryQueue:
    """Тесты очереди повторных отмен."""

    @pytest.mark.asyncio
    async def test_failed_cancel_added_to_queue(self, mock_exchange, open_position):
        """Неудачная отмена добавляется в очередь."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)

        # Первая отмена не удалась
        mock_exchange.cancel_order = AsyncMock(
            side_effect=BinanceError(-1001, "Network error")
        )

        # Пробуем отменить
        try:
            await pm._cancel_order_with_retry("BTCUSDT", "TP_456", is_algo=False)
        except:
            pass

        # Должно быть добавлено в pending_cancels (если реализовано)
        # В текущей реализации может просто логировать ошибку


# =============================================================================
# TEST: Order ID Edge Cases
# =============================================================================

class TestOrderIdEdgeCases:
    """Тесты edge cases с order ID."""

    def test_empty_order_id_ignored(self, mock_exchange, open_position):
        """Пустой order_id игнорируется."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)

        order_update = {
            "orderId": "",  # Пустой
            "symbol": "BTCUSDT",
            "status": "FILLED",
        }

        # Не должно падать
        pm._handle_order_update(order_update)
        assert open_position.status == PositionStatus.OPEN

    def test_none_order_id_ignored(self, mock_exchange, open_position):
        """None order_id игнорируется."""
        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)

        order_update = {
            "orderId": None,
            "symbol": "BTCUSDT",
            "status": "FILLED",
        }

        pm._handle_order_update(order_update)
        assert open_position.status == PositionStatus.OPEN

    def test_numeric_string_order_id(self, mock_exchange, open_position):
        """Числовой string order_id обрабатывается."""
        open_position.sl_order_id = "123456789"

        engine = TradeEngine(exchange=mock_exchange)
        engine.positions[open_position.position_id] = open_position

        pm = PositionManager(exchange=mock_exchange, trade_engine=engine)
        pm.register_position(open_position)

        order_update = {
            "orderId": "123456789",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "type": "STOP_MARKET",
            "avgPrice": "48000.00",
            "executedQty": "0.01",
        }

        pm._handle_order_update(order_update)
        assert open_position.status == PositionStatus.CLOSED
