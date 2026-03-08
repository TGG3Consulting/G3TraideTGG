# -*- coding: utf-8 -*-
"""
Tests for core/exceptions.py

Testing:
- ExchangeError and BinanceError properties
- All specialized exception types
- parse_binance_error() function with all error codes
- Edge cases for error parsing
"""

import pytest
import json
from tradebot.core.exceptions import (
    ErrorCategory,
    ExchangeError,
    BinanceError,
    NetworkError,
    RateLimitError,
    IPBanError,
    AuthError,
    LiquidationError,
    InsufficientBalanceError,
    OrderRejectedError,
    CancelFailedError,
    ValidationError,
    WebSocketError,
    parse_binance_error,
)


class TestExchangeError:
    """Test base ExchangeError class."""

    def test_exchange_error_attributes(self):
        """ExchangeError should have code, message, category."""
        error = ExchangeError(
            code=-1000,
            message="Test error",
            category=ErrorCategory.NETWORK,
        )
        assert error.code == -1000
        assert error.message == "Test error"
        assert error.category == ErrorCategory.NETWORK

    def test_is_critical_for_ip_ban(self):
        """is_critical should return True for IP_BAN category."""
        error = ExchangeError(
            code=418,
            message="IP banned",
            category=ErrorCategory.IP_BAN,
        )
        assert error.is_critical is True

    def test_is_critical_for_auth(self):
        """is_critical should return True for AUTH category."""
        error = ExchangeError(
            code=-1002,
            message="Auth failed",
            category=ErrorCategory.AUTH,
        )
        assert error.is_critical is True

    def test_is_critical_for_liquidation(self):
        """is_critical should return True for LIQUIDATION category."""
        error = ExchangeError(
            code=-2023,
            message="Liquidation",
            category=ErrorCategory.LIQUIDATION,
        )
        assert error.is_critical is True

    def test_is_critical_false_for_other_categories(self):
        """is_critical should return False for non-critical categories."""
        for category in [
            ErrorCategory.NETWORK,
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.INSUFFICIENT_BALANCE,
            ErrorCategory.ORDER_REJECTED,
            ErrorCategory.UNKNOWN,
        ]:
            error = ExchangeError(code=0, message="", category=category)
            assert error.is_critical is False, f"Failed for {category}"

    def test_string_representation(self):
        """ExchangeError should have proper string representation."""
        error = ExchangeError(code=-1000, message="Test error")
        assert str(error) == "[-1000] Test error"


class TestBinanceError:
    """Test BinanceError class."""

    def test_inherits_from_exchange_error(self):
        """BinanceError should inherit from ExchangeError."""
        error = BinanceError(code=-1000, message="Test")
        assert isinstance(error, ExchangeError)

    def test_binance_specific_attributes(self):
        """BinanceError should have Binance-specific attributes."""
        error = BinanceError(
            code=-1000,
            message="Test error",
            http_status=400,
            raw_response={"code": -1000, "msg": "Test"},
            retry_after=5,
        )
        assert error.http_status == 400
        assert error.raw_response == {"code": -1000, "msg": "Test"}
        assert error.retry_after == 5

    def test_retryable_for_network_errors(self):
        """retryable should return True for NETWORK category."""
        error = BinanceError(
            code=-1000,
            message="Network error",
            category=ErrorCategory.NETWORK,
        )
        assert error.retryable is True

    def test_retryable_for_rate_limit(self):
        """retryable should return True for RATE_LIMIT category."""
        error = BinanceError(
            code=-1003,
            message="Rate limit",
            category=ErrorCategory.RATE_LIMIT,
        )
        assert error.retryable is True

    def test_retryable_false_for_non_retryable(self):
        """retryable should return False for non-retryable categories."""
        for category in [
            ErrorCategory.AUTH,
            ErrorCategory.LIQUIDATION,
            ErrorCategory.INSUFFICIENT_BALANCE,
            ErrorCategory.ORDER_REJECTED,
        ]:
            error = BinanceError(code=0, message="", category=category)
            assert error.retryable is False, f"Failed for {category}"

    def test_should_skip_signal_for_balance(self):
        """should_skip_signal should return True for INSUFFICIENT_BALANCE."""
        error = BinanceError(
            code=-2019,
            message="Insufficient balance",
            category=ErrorCategory.INSUFFICIENT_BALANCE,
        )
        assert error.should_skip_signal is True

    def test_should_skip_signal_for_order_rejected(self):
        """should_skip_signal should return True for ORDER_REJECTED."""
        error = BinanceError(
            code=-2010,
            message="Order rejected",
            category=ErrorCategory.ORDER_REJECTED,
        )
        assert error.should_skip_signal is True

    def test_should_skip_signal_for_validation(self):
        """should_skip_signal should return True for VALIDATION."""
        error = BinanceError(
            code=-4000,
            message="Validation error",
            category=ErrorCategory.VALIDATION,
        )
        assert error.should_skip_signal is True


class TestSpecializedExceptions:
    """Test specialized exception classes."""

    def test_network_error_category(self):
        """NetworkError should have NETWORK category."""
        error = NetworkError(code=-1000, message="Network issue")
        assert error.category == ErrorCategory.NETWORK
        assert error.retryable is True

    def test_rate_limit_error_default_retry_after(self):
        """RateLimitError should have default retry_after=1."""
        error = RateLimitError(code=-1003, message="Rate limit")
        assert error.retry_after == 1

    def test_rate_limit_error_custom_retry_after(self):
        """RateLimitError should accept custom retry_after."""
        error = RateLimitError(code=-1003, message="Rate limit", retry_after=60)
        assert error.retry_after == 60

    def test_ip_ban_error_defaults(self):
        """IPBanError should have correct defaults."""
        error = IPBanError()
        assert error.code == 418
        assert error.http_status == 418
        assert error.retry_after == 120
        assert error.category == ErrorCategory.IP_BAN
        assert error.is_critical is True

    def test_auth_error_is_critical(self):
        """AuthError should be critical."""
        error = AuthError(code=-1002, message="Auth failed")
        assert error.category == ErrorCategory.AUTH
        assert error.is_critical is True

    def test_liquidation_error_is_critical(self):
        """LiquidationError should be critical."""
        error = LiquidationError()
        assert error.code == -2023
        assert error.category == ErrorCategory.LIQUIDATION
        assert error.is_critical is True

    def test_insufficient_balance_should_skip(self):
        """InsufficientBalanceError should skip signal."""
        error = InsufficientBalanceError(code=-2019, message="No balance")
        assert error.category == ErrorCategory.INSUFFICIENT_BALANCE
        assert error.should_skip_signal is True

    def test_order_rejected_should_skip(self):
        """OrderRejectedError should skip signal."""
        error = OrderRejectedError(code=-2010, message="Order rejected")
        assert error.category == ErrorCategory.ORDER_REJECTED
        assert error.should_skip_signal is True

    def test_cancel_failed_category(self):
        """CancelFailedError should have CANCEL_FAILED category."""
        error = CancelFailedError(code=-2011, message="Cancel failed")
        assert error.category == ErrorCategory.CANCEL_FAILED

    def test_validation_error_should_skip(self):
        """ValidationError should skip signal."""
        error = ValidationError(code=-4000, message="Validation")
        assert error.category == ErrorCategory.VALIDATION
        assert error.should_skip_signal is True

    def test_websocket_error_category(self):
        """WebSocketError should have WEBSOCKET category."""
        error = WebSocketError()
        assert error.category == ErrorCategory.WEBSOCKET


class TestParseBinanceError:
    """Test parse_binance_error() function."""

    # === HTTP STATUS CODES ===

    def test_parse_http_418_ip_ban(self):
        """HTTP 418 should return IPBanError."""
        error = parse_binance_error(418, "IP banned")
        assert isinstance(error, IPBanError)
        assert error.is_critical is True

    def test_parse_http_429_rate_limit(self):
        """HTTP 429 should return RateLimitError."""
        error = parse_binance_error(429, "Rate limit exceeded")
        assert isinstance(error, RateLimitError)
        assert error.retryable is True

    def test_parse_http_403_auth(self):
        """HTTP 403 should return AuthError."""
        error = parse_binance_error(403, "Forbidden")
        assert isinstance(error, AuthError)
        assert error.is_critical is True

    def test_parse_http_500_network(self):
        """HTTP 500+ should return NetworkError."""
        error = parse_binance_error(500, "Server error")
        assert isinstance(error, NetworkError)
        assert error.retryable is True

    def test_parse_http_502_network(self):
        """HTTP 502 should return NetworkError."""
        error = parse_binance_error(502, "Bad gateway")
        assert isinstance(error, NetworkError)

    # === NETWORK ERROR CODES ===

    def test_parse_code_minus_1000(self):
        """Code -1000 should return NetworkError."""
        response = json.dumps({"code": -1000, "msg": "An unknown error occurred."})
        error = parse_binance_error(400, response)
        assert isinstance(error, NetworkError)

    def test_parse_code_minus_1001(self):
        """Code -1001 should return NetworkError."""
        response = json.dumps({"code": -1001, "msg": "Disconnected."})
        error = parse_binance_error(400, response)
        assert isinstance(error, NetworkError)

    def test_parse_code_minus_1006(self):
        """Code -1006 should return NetworkError."""
        response = json.dumps({"code": -1006, "msg": "Unexpected response."})
        error = parse_binance_error(400, response)
        assert isinstance(error, NetworkError)

    def test_parse_code_minus_1007(self):
        """Code -1007 should return NetworkError."""
        response = json.dumps({"code": -1007, "msg": "Timeout."})
        error = parse_binance_error(400, response)
        assert isinstance(error, NetworkError)

    # === RATE LIMIT ERROR CODES ===

    def test_parse_code_minus_1003(self):
        """Code -1003 should return RateLimitError."""
        response = json.dumps({"code": -1003, "msg": "Rate limit."})
        error = parse_binance_error(400, response)
        assert isinstance(error, RateLimitError)
        assert error.retry_after == 5  # Default for -1003

    def test_parse_code_minus_1008(self):
        """Code -1008 should return RateLimitError with retry_after=1."""
        response = json.dumps({"code": -1008, "msg": "Server busy."})
        error = parse_binance_error(400, response)
        assert isinstance(error, RateLimitError)
        assert error.retry_after == 1  # Special case for -1008

    def test_parse_code_minus_1015(self):
        """Code -1015 should return RateLimitError."""
        response = json.dumps({"code": -1015, "msg": "Too many requests."})
        error = parse_binance_error(400, response)
        assert isinstance(error, RateLimitError)

    # === AUTH ERROR CODES ===

    def test_parse_code_minus_1002(self):
        """Code -1002 should return AuthError."""
        response = json.dumps({"code": -1002, "msg": "Invalid API key."})
        error = parse_binance_error(400, response)
        assert isinstance(error, AuthError)
        assert error.is_critical is True

    def test_parse_code_minus_1021(self):
        """Code -1021 should return AuthError (timestamp)."""
        response = json.dumps({"code": -1021, "msg": "Timestamp outside recv window."})
        error = parse_binance_error(400, response)
        assert isinstance(error, AuthError)

    def test_parse_code_minus_1022(self):
        """Code -1022 should return AuthError (signature)."""
        response = json.dumps({"code": -1022, "msg": "Invalid signature."})
        error = parse_binance_error(400, response)
        assert isinstance(error, AuthError)

    def test_parse_code_minus_2014(self):
        """Code -2014 should return AuthError (API key format)."""
        response = json.dumps({"code": -2014, "msg": "API-key format invalid."})
        error = parse_binance_error(400, response)
        assert isinstance(error, AuthError)

    def test_parse_code_minus_2015(self):
        """Code -2015 should return AuthError (invalid API key)."""
        response = json.dumps({"code": -2015, "msg": "Invalid API-key."})
        error = parse_binance_error(400, response)
        assert isinstance(error, AuthError)

    # === LIQUIDATION ===

    def test_parse_code_minus_2023_liquidation(self):
        """Code -2023 should return LiquidationError."""
        response = json.dumps({"code": -2023, "msg": "User in liquidation mode."})
        error = parse_binance_error(400, response)
        assert isinstance(error, LiquidationError)
        assert error.is_critical is True

    # === INSUFFICIENT BALANCE CODES ===

    def test_parse_code_minus_2018(self):
        """Code -2018 should return InsufficientBalanceError."""
        response = json.dumps({"code": -2018, "msg": "Balance insufficient."})
        error = parse_binance_error(400, response)
        assert isinstance(error, InsufficientBalanceError)
        assert error.should_skip_signal is True

    def test_parse_code_minus_2019(self):
        """Code -2019 should return InsufficientBalanceError."""
        response = json.dumps({"code": -2019, "msg": "Margin insufficient."})
        error = parse_binance_error(400, response)
        assert isinstance(error, InsufficientBalanceError)

    def test_parse_code_minus_2024(self):
        """Code -2024 should return InsufficientBalanceError."""
        response = json.dumps({"code": -2024, "msg": "Position side mismatch."})
        error = parse_binance_error(400, response)
        assert isinstance(error, InsufficientBalanceError)

    # === ORDER REJECTED CODES ===

    def test_parse_code_minus_2010(self):
        """Code -2010 should return OrderRejectedError."""
        response = json.dumps({"code": -2010, "msg": "Order would immediately trigger."})
        error = parse_binance_error(400, response)
        assert isinstance(error, OrderRejectedError)
        assert error.should_skip_signal is True

    def test_parse_code_minus_2020(self):
        """Code -2020 should return OrderRejectedError."""
        response = json.dumps({"code": -2020, "msg": "Order would immediately close."})
        error = parse_binance_error(400, response)
        assert isinstance(error, OrderRejectedError)

    def test_parse_code_minus_2021(self):
        """Code -2021 should return OrderRejectedError."""
        response = json.dumps({"code": -2021, "msg": "Order would immediately close."})
        error = parse_binance_error(400, response)
        assert isinstance(error, OrderRejectedError)

    def test_parse_code_minus_2025(self):
        """Code -2025 should return OrderRejectedError."""
        response = json.dumps({"code": -2025, "msg": "Reduce only."})
        error = parse_binance_error(400, response)
        assert isinstance(error, OrderRejectedError)

    # === CANCEL FAILED CODES ===

    def test_parse_code_minus_2011(self):
        """Code -2011 should return CancelFailedError."""
        response = json.dumps({"code": -2011, "msg": "Unknown order."})
        error = parse_binance_error(400, response)
        assert isinstance(error, CancelFailedError)

    def test_parse_code_minus_2013(self):
        """Code -2013 should return CancelFailedError."""
        response = json.dumps({"code": -2013, "msg": "Order does not exist."})
        error = parse_binance_error(400, response)
        assert isinstance(error, CancelFailedError)

    # === VALIDATION ERROR CODES ===

    def test_parse_code_minus_4000_series(self):
        """Codes <= -4000 should return ValidationError."""
        for code in [-4000, -4001, -4010, -4050, -4100]:
            response = json.dumps({"code": code, "msg": "Validation error."})
            error = parse_binance_error(400, response)
            # -4050 and -4051 are InsufficientBalanceError
            if code in (-4050, -4051):
                assert isinstance(error, InsufficientBalanceError)
            else:
                assert isinstance(error, ValidationError), f"Failed for code {code}"

    def test_parse_code_minus_1121_symbol_error(self):
        """Code -1121 should return ValidationError (invalid symbol)."""
        response = json.dumps({"code": -1121, "msg": "Invalid symbol."})
        error = parse_binance_error(400, response)
        assert isinstance(error, ValidationError)

    # === WEBSOCKET ===

    def test_parse_code_minus_1125_websocket(self):
        """Code -1125 should return WebSocketError."""
        response = json.dumps({"code": -1125, "msg": "WebSocket error."})
        error = parse_binance_error(400, response)
        assert isinstance(error, WebSocketError)

    # === UNKNOWN ===

    def test_parse_unknown_code(self):
        """Unknown codes should return generic BinanceError."""
        response = json.dumps({"code": -9999, "msg": "Unknown error."})
        error = parse_binance_error(400, response)
        assert isinstance(error, BinanceError)
        assert error.category == ErrorCategory.UNKNOWN

    # === EDGE CASES ===

    def test_parse_invalid_json(self):
        """Invalid JSON should be handled gracefully."""
        error = parse_binance_error(400, "not valid json")
        assert isinstance(error, BinanceError)
        assert error.message == "not valid json"

    def test_parse_empty_response(self):
        """Empty response should be handled gracefully."""
        error = parse_binance_error(400, "")
        assert isinstance(error, BinanceError)

    def test_parse_json_without_code(self):
        """JSON without 'code' should use default code 0."""
        response = json.dumps({"msg": "Some message"})
        error = parse_binance_error(400, response)
        assert error.code == 0

    def test_raw_response_preserved(self):
        """Raw response should be preserved in error."""
        response = json.dumps({"code": -1000, "msg": "Error", "extra": "data"})
        error = parse_binance_error(400, response)
        assert error.raw_response["extra"] == "data"
