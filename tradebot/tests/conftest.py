# -*- coding: utf-8 -*-
"""
Pytest configuration and shared fixtures.

Fixtures are reusable test components that can be injected into test functions.
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from typing import Dict, Any

# Add parent directory to path for imports
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tradebot.core.models import (
    Position,
    PositionSide,
    PositionStatus,
    TradeOrder,
    OrderSide,
    OrderType,
    OrderStatus,
)
from tradebot.core.exceptions import ErrorCategory


# =============================================================================
# POSITION FIXTURES
# =============================================================================

@pytest.fixture
def sample_long_position() -> Position:
    """Create a sample LONG position for testing."""
    return Position(
        position_id="POS_TEST_001",
        signal_id="SIG_TEST_001",
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        quantity=0.001,
        entry_price=50000.0,
        stop_loss=48000.0,
        take_profit=55000.0,
        status=PositionStatus.OPEN,
        entry_order_id="ORD_001",
        sl_order_id="ORD_002",
        tp_order_id="ORD_003",
        opened_at=datetime.utcnow(),
        strategy="momentum",
        max_hold_days=14,
    )


@pytest.fixture
def sample_short_position() -> Position:
    """Create a sample SHORT position for testing."""
    return Position(
        position_id="POS_TEST_002",
        signal_id="SIG_TEST_002",
        symbol="ETHUSDT",
        side=PositionSide.SHORT,
        quantity=0.1,
        entry_price=3000.0,
        stop_loss=3200.0,
        take_profit=2700.0,
        status=PositionStatus.OPEN,
        entry_order_id="ORD_004",
        sl_order_id="ORD_005",
        tp_order_id="ORD_006",
        opened_at=datetime.utcnow(),
        strategy="reversal",
        max_hold_days=14,
    )


@pytest.fixture
def expired_position() -> Position:
    """Create an expired position (max_hold_days exceeded)."""
    return Position(
        position_id="POS_EXPIRED_001",
        signal_id="SIG_EXPIRED_001",
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        quantity=0.001,
        entry_price=50000.0,
        stop_loss=48000.0,
        take_profit=55000.0,
        status=PositionStatus.OPEN,
        opened_at=datetime.utcnow() - timedelta(days=15),  # 15 days ago
        max_hold_days=14,
    )


@pytest.fixture
def closed_position() -> Position:
    """Create a closed position."""
    return Position(
        position_id="POS_CLOSED_001",
        signal_id="SIG_CLOSED_001",
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        quantity=0.001,
        entry_price=50000.0,
        stop_loss=48000.0,
        take_profit=55000.0,
        status=PositionStatus.CLOSED,
        exit_price=55000.0,
        exit_reason="TP",
        realized_pnl=5.0,
        opened_at=datetime.utcnow() - timedelta(days=2),
        closed_at=datetime.utcnow(),
    )


# =============================================================================
# ORDER FIXTURES
# =============================================================================

@pytest.fixture
def sample_market_order() -> TradeOrder:
    """Create a sample MARKET order."""
    return TradeOrder(
        order_id="ORD_TEST_001",
        signal_id="SIG_TEST_001",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.001,
        position_side=PositionSide.LONG,
    )


@pytest.fixture
def sample_stop_order() -> TradeOrder:
    """Create a sample STOP_MARKET order (for SL)."""
    return TradeOrder(
        order_id="ORD_TEST_002",
        signal_id="SIG_TEST_001",
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.STOP_MARKET,
        quantity=0.001,
        stop_price=48000.0,
        position_side=PositionSide.LONG,
        reduce_only=True,
    )


# =============================================================================
# MOCK EXCHANGE FIXTURES
# =============================================================================

@pytest.fixture
def mock_exchange() -> MagicMock:
    """Create a mock exchange adapter."""
    exchange = MagicMock()
    exchange.name = "mock_exchange"
    exchange.is_testnet = True

    # Async methods
    exchange.connect = AsyncMock(return_value=True)
    exchange.disconnect = AsyncMock()
    exchange.get_balance = AsyncMock(return_value=Decimal("1000.0"))
    exchange.get_price = AsyncMock(return_value=Decimal("50000.0"))
    exchange.get_position = AsyncMock(return_value=None)
    exchange.get_all_positions = AsyncMock(return_value=[])
    exchange.get_open_orders = AsyncMock(return_value=[])
    exchange.set_leverage = AsyncMock(return_value=True)
    exchange.cancel_order = AsyncMock(return_value=True)
    exchange.cancel_all_orders = AsyncMock(return_value=0)
    exchange.start_user_data_stream = AsyncMock(return_value=True)
    exchange.stop_user_data_stream = AsyncMock()

    # Sync methods
    exchange.round_quantity = MagicMock(side_effect=lambda s, q: q)
    exchange.round_price = MagicMock(side_effect=lambda s, p: p)

    # Market order returns filled order data
    exchange.place_market_order = AsyncMock(return_value={
        "orderId": "123456789",
        "status": "FILLED",
        "avgPrice": "50000.0",
        "executedQty": "0.001",
        "origQty": "0.001",
    })

    # Stop order returns order data
    exchange.place_stop_order = AsyncMock(return_value={
        "orderId": "987654321",
        "status": "NEW",
    })

    # Take profit order returns order data
    exchange.place_take_profit_order = AsyncMock(return_value={
        "orderId": "456789123",
        "status": "NEW",
    })

    return exchange


# =============================================================================
# BINANCE API RESPONSE FIXTURES
# =============================================================================

@pytest.fixture
def binance_filled_order_response() -> Dict[str, Any]:
    """Sample Binance filled order response."""
    return {
        "orderId": 123456789,
        "symbol": "BTCUSDT",
        "status": "FILLED",
        "clientOrderId": "test_order_001",
        "price": "0",
        "avgPrice": "50000.00",
        "origQty": "0.001",
        "executedQty": "0.001",
        "cumQuote": "50.00",
        "timeInForce": "GTC",
        "type": "MARKET",
        "reduceOnly": False,
        "closePosition": False,
        "side": "BUY",
        "positionSide": "LONG",
        "stopPrice": "0",
        "workingType": "CONTRACT_PRICE",
        "priceProtect": False,
        "origType": "MARKET",
        "updateTime": 1234567890123,
    }


@pytest.fixture
def binance_error_response_auth() -> str:
    """Sample Binance auth error response."""
    return '{"code":-1002,"msg":"Invalid API-key, IP, or permissions for action."}'


@pytest.fixture
def binance_error_response_insufficient_balance() -> str:
    """Sample Binance insufficient balance error response."""
    return '{"code":-2019,"msg":"Margin is insufficient."}'


@pytest.fixture
def binance_error_response_order_rejected() -> str:
    """Sample Binance order rejected error response."""
    return '{"code":-2010,"msg":"Order would immediately trigger."}'


@pytest.fixture
def binance_error_response_liquidation() -> str:
    """Sample Binance liquidation error response."""
    return '{"code":-2023,"msg":"User in liquidation mode."}'


# =============================================================================
# WEBSOCKET EVENT FIXTURES
# =============================================================================

@pytest.fixture
def ws_order_filled_event() -> Dict[str, Any]:
    """Sample WebSocket ORDER_TRADE_UPDATE event (FILLED)."""
    return {
        "e": "ORDER_TRADE_UPDATE",
        "T": 1234567890123,
        "o": {
            "s": "BTCUSDT",
            "i": 123456789,
            "X": "FILLED",
            "o": "STOP_MARKET",
            "ap": "48000.00",
            "rp": "-2.00",
            "z": "0.001",
            "l": "0.001",
            "L": "48000.00",
            "q": "0.001",
        }
    }


@pytest.fixture
def ws_account_update_event() -> Dict[str, Any]:
    """Sample WebSocket ACCOUNT_UPDATE event."""
    return {
        "e": "ACCOUNT_UPDATE",
        "T": 1234567890123,
        "a": {
            "B": [
                {"a": "USDT", "wb": "998.00", "cw": "998.00"}
            ],
            "P": [
                {
                    "s": "BTCUSDT",
                    "pa": "0",
                    "ep": "0.00",
                    "cr": "-2.00",
                    "ps": "LONG"
                }
            ]
        }
    }
