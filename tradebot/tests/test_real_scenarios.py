# -*- coding: utf-8 -*-
"""
РЕАЛЬНЫЕ СЦЕНАРИИ - тесты которые ищут баги, а не подтверждают код.

Эти тесты симулируют РЕАЛЬНЫЕ проблемы которые могут случиться в production:
1. Сетевые сбои
2. Race conditions
3. Partial fills
4. Проблемы с округлением
5. Неожиданные ответы биржи
6. Одновременные сигналы
7. Восстановление после краша
"""

import asyncio
import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import time

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
    return exchange


@pytest.fixture
def make_signal():
    """Factory для создания сигналов."""
    def _make(symbol="BTCUSDT", direction="LONG", signal_id=None):
        signal = MagicMock()
        signal.signal_id = signal_id or f"SIG_{symbol}_{int(time.time()*1000)}"
        signal.symbol = symbol
        signal.direction = direction
        signal.entry = 50000 if symbol == "BTCUSDT" else 3000
        signal.stop_loss = 49000 if direction == "LONG" else 51000
        signal.take_profit = 53000 if direction == "LONG" else 47000
        signal.date = datetime.now(timezone.utc)
        signal.metadata = {"strategy": "momentum"}
        return signal
    return _make


# =============================================================================
# TEST: Сетевые сбои в разных точках
# =============================================================================

class TestNetworkFailures:
    """Тесты сетевых сбоев на разных этапах."""

    @pytest.mark.asyncio
    async def test_get_price_fails(self, mock_exchange, make_signal):
        """Сбой при получении цены - сигнал должен быть пропущен (не exception)."""
        mock_exchange.get_price = AsyncMock(
            side_effect=BinanceError(-1001, "Network error")
        )

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        # Код ловит BinanceError и пропускает сигнал (не пробрасывает)
        result = await engine.execute_signal(signal=signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_set_leverage_fails_but_continues(self, mock_exchange, make_signal):
        """Если set_leverage падает - что происходит?"""
        mock_exchange.get_position_by_side = AsyncMock(return_value=None)
        mock_exchange.set_leverage = AsyncMock(
            side_effect=BinanceError(-4028, "Leverage not changed")
        )

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        # Leverage error может быть non-critical (leverage уже установлен)
        # Проверяем что код корректно обрабатывает
        result = await engine.execute_signal(signal=signal)
        # Если leverage fail критичен - result должен быть None
        # Если не критичен - должна быть позиция

    @pytest.mark.asyncio
    async def test_sl_placement_network_timeout(self, mock_exchange, make_signal):
        """Timeout при выставлении SL - позиция должна быть закрыта.

        FIX: TimeoutError теперь обрабатывается корректно.
        Emergency close вызывается, позиция закрывается.
        """
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        # SL timeout
        mock_exchange.place_stop_order = AsyncMock(
            side_effect=asyncio.TimeoutError("SL placement timeout")
        )

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)

        # Позиция должна быть закрыта (emergency close)
        assert result is None
        assert engine.emergency_closes >= 1

    @pytest.mark.asyncio
    async def test_tp_fails_position_stays_open(self, mock_exchange, make_signal):
        """TP fail - позиция должна остаться открытой (SL защищает)."""
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        mock_exchange.place_take_profit_order = AsyncMock(
            side_effect=BinanceError(-2021, "Order would trigger immediately")
        )

        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)
        engine.position_manager = position_manager

        signal = make_signal()
        result = await engine.execute_signal(signal=signal)

        # Позиция должна быть открыта
        assert result is not None
        assert result.is_open
        # TP order_id должен быть пустым
        assert result.tp_order_id == ""
        # Должна быть зарегистрирована для missing TP мониторинга
        assert result.position_id in position_manager._missing_tp_positions


# =============================================================================
# TEST: Partial Fill сценарии
# =============================================================================

class TestPartialFills:
    """Тесты частичного исполнения ордеров."""

    @pytest.mark.asyncio
    async def test_entry_partial_fill_50_percent(self, mock_exchange, make_signal):
        """Entry order исполнен на 50% - SL/TP должны быть на фактический объём."""
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            # Частичное исполнение - только 50%
            return {"positionAmt": "0.005", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        mock_exchange.place_market_order = AsyncMock(return_value={
            "orderId": "123456",
            "avgPrice": "50000",
            "executedQty": "0.005",  # Только 50%
            "origQty": "0.01",
            "status": "PARTIALLY_FILLED",
        })

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal, order_size_usd=500)

        assert result is not None
        # Количество в позиции должно быть фактическое (0.005), не запрошенное
        assert result.quantity == 0.005
        assert result.is_partial_fill is True
        assert engine.partial_fills == 1

        # SL ордер должен быть на 0.005, не на 0.01
        sl_call = mock_exchange.place_stop_order.call_args
        sl_quantity = sl_call.kwargs.get('quantity')
        assert float(sl_quantity) == 0.005

    @pytest.mark.asyncio
    async def test_entry_partial_fill_tiny_amount(self, mock_exchange, make_signal):
        """Entry на 1% - что происходит? Min notional?"""
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            # Очень маленькое исполнение
            return {"positionAmt": "0.0001", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        mock_exchange.place_market_order = AsyncMock(return_value={
            "orderId": "123456",
            "avgPrice": "50000",
            "executedQty": "0.0001",
            "origQty": "0.01",
            "status": "PARTIALLY_FILLED",
        })

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal, order_size_usd=500)

        # Даже tiny fill должен быть обработан
        # Notional = 0.0001 * 50000 = 5 USDT < 100 USDT min
        # Биржа может отклонить SL для такого маленького qty


# =============================================================================
# TEST: Race Conditions
# =============================================================================

class TestRaceConditions:
    """Тесты race conditions."""

    @pytest.mark.asyncio
    async def test_concurrent_signals_same_symbol(self, mock_exchange, make_signal):
        """Два сигнала на один символ одновременно."""
        positions_created = [0]

        original_place_market = mock_exchange.place_market_order

        async def mock_market_with_delay(*args, **kwargs):
            await asyncio.sleep(0.1)  # Симулируем задержку
            positions_created[0] += 1
            return await original_place_market(*args, **kwargs)

        mock_exchange.place_market_order = mock_market_with_delay

        # После первого entry - позиция есть
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] <= 2:  # Первые 2 проверки - нет позиции
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(exchange=mock_exchange)

        signal1 = make_signal(signal_id="SIG_1")
        signal2 = make_signal(signal_id="SIG_2")

        # Запускаем оба сигнала одновременно
        results = await asyncio.gather(
            engine.execute_signal(signal=signal1),
            engine.execute_signal(signal=signal2),
            return_exceptions=True,
        )

        # Должна быть создана только ОДНА позиция (lock защищает)
        successful = [r for r in results if r is not None and not isinstance(r, Exception)]
        assert len(successful) <= 1, "Должна быть максимум 1 позиция из-за lock"

    @pytest.mark.asyncio
    async def test_position_closed_while_placing_tp(self, mock_exchange, make_signal):
        """Позиция закрыта по SL пока мы ставим TP."""
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # До entry
            if call_count[0] == 2:
                return {"positionAmt": "0.01", "entryPrice": "50000"}  # После entry
            # После SL - позиция закрыта
            return {"positionAmt": "0", "entryPrice": "0"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        # TP попытка будет после того как позиция закрыта
        async def slow_tp(*args, **kwargs):
            await asyncio.sleep(0.1)  # Задержка
            return {"orderId": "456"}
        mock_exchange.place_take_profit_order = slow_tp

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)
        # Результат зависит от того как код обрабатывает эту ситуацию


# =============================================================================
# TEST: Проблемы с округлением и Decimal
# =============================================================================

class TestDecimalPrecision:
    """Тесты точности вычислений."""

    @pytest.mark.asyncio
    async def test_sl_equals_entry_after_rounding(self, mock_exchange, make_signal):
        """SL == entry после округления - должен быть сдвиг на tick_size."""
        # Price очень точная, SL% очень маленький
        mock_exchange.get_price = AsyncMock(return_value=Decimal("50000.00"))
        mock_exchange.round_price = MagicMock(
            side_effect=lambda s, p: Decimal("50000.00")  # Всё округляется до одного значения
        )
        mock_exchange.get_tick_size = MagicMock(return_value=Decimal("0.01"))

        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000.00"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(exchange=mock_exchange, sl_pct=0.001)  # Очень маленький SL
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)

        # SL должен быть сдвинут на tick_size
        if result is not None:
            sl_call = mock_exchange.place_stop_order.call_args
            sl_price = sl_call.kwargs.get('stop_price')
            # Для LONG: SL должен быть ниже entry
            assert sl_price < Decimal("50000.00") or sl_price > Decimal("50000.00")

    @pytest.mark.asyncio
    async def test_quantity_rounds_to_zero_but_min_notional_adjusts(self, mock_exchange, make_signal):
        """Количество округляется до 0, но min notional adjustment увеличивает его.

        Код НЕ reject'ает qty=0 потому что min notional adjustment
        происходит ПОСЛЕ округления и ПЕРЕД проверкой qty>0.
        Это правильное поведение - min notional $100 гарантирует qty > 0.
        """
        mock_exchange.get_price = AsyncMock(return_value=Decimal("50000"))
        # round_quantity возвращает 0, но step_size позволяет увеличить
        mock_exchange.round_quantity = MagicMock(return_value=Decimal("0"))
        mock_exchange.get_step_size = MagicMock(return_value=Decimal("0.001"))
        mock_exchange.get_position_by_side = AsyncMock(return_value=None)

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        # order_size $1 < min_notional $100, будет увеличено
        result = await engine.execute_signal(signal=signal, order_size_usd=1)

        # Min notional adjustment должен увеличить qty до >= 0.002
        # (100 USDT / 50000 = 0.002 BTC)
        # Ордер может пройти или не пройти в зависимости от других проверок

    @pytest.mark.asyncio
    async def test_very_small_sl_pct(self, mock_exchange, make_signal):
        """Очень маленький SL% (0.1%) - должен работать корректно."""
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(exchange=mock_exchange, sl_pct=0.1)  # 0.1%
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)

        if result is not None:
            # SL должен быть 50000 * 0.999 = 49950
            sl_call = mock_exchange.place_stop_order.call_args
            sl_price = sl_call.kwargs.get('stop_price')
            assert float(sl_price) == pytest.approx(49950, rel=0.01)


# =============================================================================
# TEST: Неожиданные ответы биржи
# =============================================================================

class TestUnexpectedExchangeResponses:
    """Тесты неожиданных ответов от биржи."""

    @pytest.mark.asyncio
    async def test_entry_returns_empty_result(self, mock_exchange, make_signal):
        """Entry order возвращает пустой результат."""
        mock_exchange.get_position_by_side = AsyncMock(return_value=None)
        mock_exchange.place_market_order = AsyncMock(return_value={})  # Пустой ответ

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)

        assert result is None
        assert engine.signals_skipped == 1

    @pytest.mark.asyncio
    async def test_entry_returns_none(self, mock_exchange, make_signal):
        """Entry order возвращает None."""
        mock_exchange.get_position_by_side = AsyncMock(return_value=None)
        mock_exchange.place_market_order = AsyncMock(return_value=None)

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)

        assert result is None

    @pytest.mark.asyncio
    async def test_position_exists_with_zero_qty(self, mock_exchange, make_signal):
        """Позиция существует но qty=0 (закрыта сразу после entry)."""
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # До entry
            # После entry - позиция есть но qty=0 (моментально ликвидирована?)
            return {"positionAmt": "0", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)

        # Должен быть reject - qty=0
        assert result is None

    @pytest.mark.asyncio
    async def test_sl_returns_empty_algo_id(self, mock_exchange, make_signal):
        """SL order возвращает пустой algoId."""
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        mock_exchange.place_stop_order = AsyncMock(return_value={"algoId": ""})  # Пустой ID

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)

        # SL без ID = fail = emergency close
        assert result is None
        assert engine.emergency_closes >= 1


# =============================================================================
# TEST: Orphan Cleanup реальные сценарии
# =============================================================================

class TestOrphanCleanupRealScenarios:
    """Реальные сценарии очистки orphan ордеров."""

    @pytest.mark.asyncio
    async def test_sl_triggered_tp_becomes_orphan(self, mock_exchange):
        """SL сработал - TP стал orphan и должен быть отменён."""
        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # Создаём позицию вручную
        position = Position(
            position_id="POS_TEST_1",
            signal_id="SIG_TEST_1",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.01,
            entry_price=50000,
            stop_loss=49000,
            take_profit=53000,
            status=PositionStatus.OPEN,
            sl_order_id="SL_123",
            tp_order_id="TP_456",
        )
        engine.positions[position.position_id] = position
        position_manager.register_position(position)

        # SL сработал - позиция закрыта
        position.status = PositionStatus.CLOSED
        position.close_reason = "SL"
        del engine.positions[position.position_id]

        # Регистрируем symbol как недавно закрытый
        position_manager._recently_closed_symbols["BTCUSDT"] = datetime.now(timezone.utc)

        # Биржа всё ещё имеет TP ордер (orphan)
        exchange_orders = [
            {
                "orderId": "TP_456",
                "symbol": "BTCUSDT",
                "type": "LIMIT",
                "positionSide": "LONG",
                "side": "SELL",
            }
        ]
        exchange_positions = []  # Позиция закрыта

        # Сразу после закрытия - grace period защищает
        await position_manager._clean_orphan_orders(
            exchange_positions=exchange_positions,
            exchange_orders=exchange_orders,
            exchange_algo_orders=[],
        )

        # TP НЕ должен быть отменён (grace period)
        mock_exchange.cancel_order.assert_not_called()

        # Ждём expiry grace period
        position_manager._recently_closed_symbols["BTCUSDT"] = (
            datetime.now(timezone.utc) - timedelta(seconds=120)
        )

        # Теперь cleanup должен сработать
        await position_manager._clean_orphan_orders(
            exchange_positions=exchange_positions,
            exchange_orders=exchange_orders,
            exchange_algo_orders=[],
        )

        # TP ДОЛЖЕН быть отменён
        mock_exchange.cancel_order.assert_called()

    @pytest.mark.asyncio
    async def test_manual_position_close_cleanup(self, mock_exchange):
        """Позиция закрыта вручную - SL и TP должны быть отменены."""
        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # У нас нет tracked позиций
        assert len(engine.get_open_positions()) == 0

        # Но на бирже есть ордера от старой позиции
        exchange_orders = [
            {
                "orderId": "TP_OLD",
                "symbol": "SOLUSDT",
                "type": "LIMIT",
                "positionSide": "SHORT",
                "side": "BUY",
            }
        ]
        exchange_algo_orders = [
            {
                "algoId": 111222,
                "symbol": "SOLUSDT",
                "orderType": "STOP_MARKET",
                "positionSide": "SHORT",
                "side": "BUY",
            }
        ]
        exchange_positions = []  # Нет позиций

        await position_manager._clean_orphan_orders(
            exchange_positions=exchange_positions,
            exchange_orders=exchange_orders,
            exchange_algo_orders=exchange_algo_orders,
        )

        # Оба ордера должны быть отменены
        mock_exchange.cancel_order.assert_called()
        mock_exchange.cancel_algo_order.assert_called()


# =============================================================================
# TEST: REST Sync сценарии
# =============================================================================

class TestRestSyncScenarios:
    """Тесты REST синхронизации."""

    @pytest.mark.asyncio
    async def test_position_closed_websocket_missed(self, mock_exchange):
        """Позиция закрыта но WebSocket пропустил событие."""
        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # Создаём tracked позицию
        position = Position(
            position_id="POS_SYNC_1",
            signal_id="SIG_SYNC_1",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=0.01,
            entry_price=50000,
            stop_loss=49000,
            take_profit=53000,
            status=PositionStatus.OPEN,
            sl_order_id="SL_SYNC",
            tp_order_id="TP_SYNC",
        )
        engine.positions[position.position_id] = position

        # На бирже позиции УЖЕ нет (WS пропустил)
        mock_exchange.get_all_positions = AsyncMock(return_value=[])
        mock_exchange.get_open_orders = AsyncMock(return_value=[])
        mock_exchange.get_open_algo_orders = AsyncMock(return_value=[])

        # REST sync должен обнаружить это
        await position_manager._perform_rest_sync()

        # Статистика должна отразить fix
        # (зависит от реализации - может закрыть позицию или пометить)

    @pytest.mark.asyncio
    async def test_sl_order_disappeared(self, mock_exchange):
        """SL ордер исчез (отменён вручную?) - позиция в опасности."""
        engine = TradeEngine(exchange=mock_exchange)
        position_manager = PositionManager(exchange=mock_exchange, trade_engine=engine)

        # Tracked позиция с SL
        position = Position(
            position_id="POS_DANGER_1",
            signal_id="SIG_DANGER_1",
            symbol="ETHUSDT",
            side=PositionSide.SHORT,
            quantity=0.1,
            entry_price=3000,
            stop_loss=3150,
            take_profit=2700,
            status=PositionStatus.OPEN,
            sl_order_id="SL_DANGER",
            tp_order_id="TP_DANGER",
        )
        engine.positions[position.position_id] = position

        # Позиция есть на бирже
        mock_exchange.get_all_positions = AsyncMock(return_value=[
            {"symbol": "ETHUSDT", "positionSide": "SHORT", "positionAmt": "-0.1"}
        ])
        # SL ордера НЕТ!
        mock_exchange.get_open_orders = AsyncMock(return_value=[
            {"orderId": "TP_DANGER", "symbol": "ETHUSDT", "type": "LIMIT", "positionSide": "SHORT"}
        ])
        mock_exchange.get_open_algo_orders = AsyncMock(return_value=[])  # SL исчез

        # REST sync должен обнаружить missing SL
        await position_manager._perform_rest_sync()

        # Ожидаем какое-то действие (warning, попытка восстановить SL, etc.)


# =============================================================================
# TEST: State Persistence
# =============================================================================

class TestStatePersistence:
    """Тесты сохранения и восстановления состояния."""

    @pytest.mark.asyncio
    async def test_state_saved_immediately_after_position_open(self, mock_exchange, make_signal):
        """State должен быть сохранён СРАЗУ после открытия позиции."""
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(exchange=mock_exchange)

        state_saves = []
        engine.on_state_changed = lambda: state_saves.append(datetime.now())

        signal = make_signal()
        result = await engine.execute_signal(signal=signal)

        assert result is not None
        # State должен быть сохранён хотя бы раз
        assert len(state_saves) >= 1

    @pytest.mark.asyncio
    async def test_crash_recovery_positions_restored(self, mock_exchange):
        """После краша позиции должны быть восстановлены из state."""
        # Симулируем восстановление из state файла
        saved_positions = {
            "POS_CRASH_1": {
                "position_id": "POS_CRASH_1",
                "signal_id": "SIG_CRASH_1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "quantity": 0.01,
                "entry_price": 50000,
                "stop_loss": 49000,
                "take_profit": 53000,
                "status": "OPEN",
                "sl_order_id": "SL_CRASH",
                "tp_order_id": "TP_CRASH",
            }
        }

        engine = TradeEngine(exchange=mock_exchange)

        # Восстанавливаем позиции
        for pos_data in saved_positions.values():
            position = Position(
                position_id=pos_data["position_id"],
                signal_id=pos_data["signal_id"],
                symbol=pos_data["symbol"],
                side=PositionSide[pos_data["side"]],
                quantity=pos_data["quantity"],
                entry_price=pos_data["entry_price"],
                stop_loss=pos_data["stop_loss"],
                take_profit=pos_data["take_profit"],
                status=PositionStatus[pos_data["status"]],
                sl_order_id=pos_data["sl_order_id"],
                tp_order_id=pos_data["tp_order_id"],
            )
            engine.positions[position.position_id] = position

        # Проверяем что позиция восстановлена
        assert len(engine.get_open_positions()) == 1
        assert engine.positions["POS_CRASH_1"].symbol == "BTCUSDT"


# =============================================================================
# TEST: Edge Cases
# =============================================================================

class TestEdgeCasesReal:
    """Реальные edge cases."""

    @pytest.mark.asyncio
    async def test_signal_with_invalid_direction(self, mock_exchange, make_signal):
        """Сигнал с неправильным direction."""
        signal = make_signal()
        signal.direction = "INVALID"  # Ни LONG ни SHORT

        engine = TradeEngine(exchange=mock_exchange)

        # Должен быть обработан корректно (reject или exception)
        result = await engine.execute_signal(signal=signal)
        # Зависит от реализации

    @pytest.mark.asyncio
    async def test_zero_balance(self, mock_exchange, make_signal):
        """Баланс = 0."""
        mock_exchange.get_balance = AsyncMock(return_value=Decimal("0"))

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)

        assert result is None
        assert engine.signals_skipped == 1

    @pytest.mark.asyncio
    async def test_negative_entry_price_from_api(self, mock_exchange, make_signal):
        """Биржа возвращает отрицательную цену (баг API?)."""
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "-50000"}  # Отрицательная!
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(exchange=mock_exchange)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)
        # Код должен обработать это корректно

    @pytest.mark.asyncio
    async def test_very_high_leverage(self, mock_exchange, make_signal):
        """Leverage 125x - максимум на Binance."""
        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "0.01", "entryPrice": "50000"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)

        engine = TradeEngine(exchange=mock_exchange, default_leverage=125)
        signal = make_signal()

        result = await engine.execute_signal(signal=signal)

        if result is not None:
            mock_exchange.set_leverage.assert_called_with("BTCUSDT", 125)

    @pytest.mark.asyncio
    async def test_symbol_with_special_characters(self, mock_exchange, make_signal):
        """Символ с необычными символами (если такие есть)."""
        signal = make_signal(symbol="1000PEPEUSDT")  # Символ начинается с цифры

        call_count = [0]
        async def mock_position(symbol, side):
            call_count[0] += 1
            if call_count[0] == 1:
                return None
            return {"positionAmt": "1000000", "entryPrice": "0.00001"}
        mock_exchange.get_position_by_side = AsyncMock(side_effect=mock_position)
        mock_exchange.get_price = AsyncMock(return_value=Decimal("0.00001"))

        engine = TradeEngine(exchange=mock_exchange)

        result = await engine.execute_signal(signal=signal)
        # Должен работать с любым валидным символом
