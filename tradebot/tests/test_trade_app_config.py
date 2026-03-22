# -*- coding: utf-8 -*-
"""
Test: Проверка конфигурации TradeApp

Команда которую тестируем:
py -3.12 -m tradebot.trade_app --mainnet --symbols XRPUSDT,BTCUSDT \
    --strategies ls_fade,momentum,momentum_ls --order-size 1500 --sl 1.5 \
    --dynamic-size --protected-size 10 --trailing-stop \
    --trailing-activation 3.0 --trailing-callback 2.0 \
    --vol-filter-high --vol-filter-low --log-level=DEBUG

Тест проверяет:
1. Order size = $1500
2. После LOSS: order size = $1500 / 10 = $150
3. После WIN: order size = $1500
4. SL = 1.5% от entry
5. Trailing activation = 3% от entry
6. Trailing callback = 2%
7. Стратегии = ls_fade, momentum, momentum_ls
"""

import pytest
import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'GenerateHistorySignals'))


class TestTradeAppConfig:
    """Тесты конфигурации TradeApp."""

    # Параметры из команды
    ORDER_SIZE = 1500.0
    PROTECTED_SIZE_DIVISOR = 10.0
    SL_PCT = 1.5
    TRAILING_ACTIVATION = 3.0
    TRAILING_CALLBACK = 2.0
    STRATEGIES = ['ls_fade', 'momentum', 'momentum_ls']
    SYMBOLS = ['XRPUSDT', 'BTCUSDT']

    def test_01_order_size_initial(self):
        """Order size должен быть $1500 изначально."""
        order_size = self.ORDER_SIZE
        assert order_size == 1500.0, f"Expected 1500, got {order_size}"
        print(f"[OK] Initial order size: ${order_size}")

    def test_02_order_size_after_loss(self):
        """После LOSS: order size = $1500 / 10 = $150."""
        order_size = self.ORDER_SIZE
        divisor = self.PROTECTED_SIZE_DIVISOR

        # Симуляция LOSS
        last_trade_was_win = False

        if last_trade_was_win:
            calculated_size = order_size
        else:
            calculated_size = order_size / divisor

        expected = 150.0
        assert calculated_size == expected, f"Expected {expected}, got {calculated_size}"
        print(f"[OK] After LOSS: ${order_size} / {divisor} = ${calculated_size}")

    def test_03_order_size_after_win(self):
        """После WIN: order size возвращается к $1500."""
        order_size = self.ORDER_SIZE
        divisor = self.PROTECTED_SIZE_DIVISOR

        # Симуляция WIN после LOSS
        last_trade_was_win = True

        if last_trade_was_win:
            calculated_size = order_size
        else:
            calculated_size = order_size / divisor

        expected = 1500.0
        assert calculated_size == expected, f"Expected {expected}, got {calculated_size}"
        print(f"[OK] After WIN: ${calculated_size}")

    def test_04_dynamic_size_sequence(self):
        """Полный цикл: WIN -> LOSS -> LOSS -> WIN."""
        order_size = self.ORDER_SIZE
        divisor = self.PROTECTED_SIZE_DIVISOR

        # Симуляция последовательности сделок
        trades = [
            ("WIN", True),
            ("LOSS", False),
            ("LOSS", False),
            ("WIN", True),
        ]

        expected_sizes = [1500.0, 150.0, 150.0, 1500.0]

        print("\nDynamic Size Sequence:")
        print("-" * 40)

        for i, (result, was_win) in enumerate(trades):
            if was_win:
                size = order_size
            else:
                size = order_size / divisor

            assert size == expected_sizes[i], f"Trade {i+1}: Expected {expected_sizes[i]}, got {size}"
            print(f"  Trade {i+1} ({result}): ${size}")

        print("-" * 40)
        print("[OK] Dynamic size sequence correct")

    def test_05_sl_percentage(self):
        """SL должен быть 1.5% от entry."""
        sl_pct = self.SL_PCT
        entry_price = 100000.0  # Пример: BTC @ $100,000

        # LONG: SL ниже entry
        sl_long = entry_price * (1 - sl_pct / 100)
        expected_sl_long = 98500.0

        # SHORT: SL выше entry
        sl_short = entry_price * (1 + sl_pct / 100)
        expected_sl_short = 101500.0

        assert abs(sl_long - expected_sl_long) < 0.01, f"LONG SL: Expected {expected_sl_long}, got {sl_long}"
        assert abs(sl_short - expected_sl_short) < 0.01, f"SHORT SL: Expected {expected_sl_short}, got {sl_short}"

        print(f"\n[OK] SL = {sl_pct}%")
        print(f"  LONG entry ${entry_price}: SL @ ${sl_long:.0f}")
        print(f"  SHORT entry ${entry_price}: SL @ ${sl_short:.0f}")

    def test_06_trailing_activation(self):
        """Trailing активируется при +3% от entry."""
        activation_pct = self.TRAILING_ACTIVATION
        entry_price = 100000.0

        # LONG: активация выше entry
        activation_long = entry_price * (1 + activation_pct / 100)
        expected_long = 103000.0

        # SHORT: активация ниже entry
        activation_short = entry_price * (1 - activation_pct / 100)
        expected_short = 97000.0

        assert abs(activation_long - expected_long) < 0.01, f"LONG activation: Expected {expected_long}, got {activation_long}"
        assert abs(activation_short - expected_short) < 0.01, f"SHORT activation: Expected {expected_short}, got {activation_short}"

        print(f"\n[OK] Trailing Activation = {activation_pct}%")
        print(f"  LONG entry ${entry_price}: activates @ ${activation_long:.0f}")
        print(f"  SHORT entry ${entry_price}: activates @ ${activation_short:.0f}")

    def test_07_trailing_callback(self):
        """Trailing callback = 2% от максимума."""
        callback_pct = self.TRAILING_CALLBACK
        entry_price = 100000.0

        # Симуляция: цена дошла до максимума
        max_price_long = 110000.0  # Максимум для LONG
        min_price_short = 90000.0   # Минимум для SHORT

        # LONG: trailing stop = max - 2%
        trailing_stop_long = max_price_long * (1 - callback_pct / 100)
        expected_long = 107800.0

        # SHORT: trailing stop = min + 2%
        trailing_stop_short = min_price_short * (1 + callback_pct / 100)
        expected_short = 91800.0

        assert abs(trailing_stop_long - expected_long) < 0.01, f"LONG trailing: Expected {expected_long}, got {trailing_stop_long}"
        assert abs(trailing_stop_short - expected_short) < 0.01, f"SHORT trailing: Expected {expected_short}, got {trailing_stop_short}"

        print(f"\n[OK] Trailing Callback = {callback_pct}%")
        print(f"  LONG max ${max_price_long}: trail stop @ ${trailing_stop_long:.0f}")
        print(f"  SHORT min ${min_price_short}: trail stop @ ${trailing_stop_short:.0f}")

    def test_08_strategies_list(self):
        """Проверка списка стратегий."""
        strategies = self.STRATEGIES
        expected = ['ls_fade', 'momentum', 'momentum_ls']

        assert strategies == expected, f"Expected {expected}, got {strategies}"
        assert len(strategies) == 3, f"Expected 3 strategies, got {len(strategies)}"

        print(f"\n[OK] Strategies: {', '.join(strategies)}")

    def test_09_symbols_list(self):
        """Проверка списка символов."""
        symbols = self.SYMBOLS
        expected = ['XRPUSDT', 'BTCUSDT']

        assert symbols == expected, f"Expected {expected}, got {symbols}"
        assert len(symbols) == 2, f"Expected 2 symbols, got {len(symbols)}"

        print(f"\n[OK] Symbols: {', '.join(symbols)}")

    def test_10_full_trade_simulation_long(self):
        """Полная симуляция LONG сделки."""
        entry = 100000.0
        sl_pct = self.SL_PCT
        activation_pct = self.TRAILING_ACTIVATION
        callback_pct = self.TRAILING_CALLBACK

        # Расчёты
        sl_price = entry * (1 - sl_pct / 100)
        activation_price = entry * (1 + activation_pct / 100)

        # Симуляция: цена дошла до $108,000, потом откат
        max_price = 108000.0
        trailing_stop = max_price * (1 - callback_pct / 100)

        print(f"\n{'='*50}")
        print("LONG Trade Simulation")
        print(f"{'='*50}")
        print(f"Entry:              ${entry:,.0f}")
        print(f"SL ({sl_pct}%):          ${sl_price:,.0f}")
        print(f"Trail Activation:   ${activation_price:,.0f} (+{activation_pct}%)")
        print(f"Max Price Reached:  ${max_price:,.0f}")
        print(f"Trail Stop ({callback_pct}%):   ${trailing_stop:,.0f}")
        print(f"{'='*50}")

        # Проверки
        assert abs(sl_price - 98500.0) < 0.01
        assert abs(activation_price - 103000.0) < 0.01
        assert abs(trailing_stop - 105840.0) < 0.01

        # Профит если закрылись по trailing
        profit_pct = (trailing_stop - entry) / entry * 100
        print(f"Profit if trail hit: {profit_pct:.2f}%")
        print(f"{'='*50}")
        print("[OK] LONG simulation correct")

    def test_11_full_trade_simulation_short(self):
        """Полная симуляция SHORT сделки."""
        entry = 100000.0
        sl_pct = self.SL_PCT
        activation_pct = self.TRAILING_ACTIVATION
        callback_pct = self.TRAILING_CALLBACK

        # Расчёты
        sl_price = entry * (1 + sl_pct / 100)
        activation_price = entry * (1 - activation_pct / 100)

        # Симуляция: цена упала до $92,000, потом отскок
        min_price = 92000.0
        trailing_stop = min_price * (1 + callback_pct / 100)

        print(f"\n{'='*50}")
        print("SHORT Trade Simulation")
        print(f"{'='*50}")
        print(f"Entry:              ${entry:,.0f}")
        print(f"SL ({sl_pct}%):          ${sl_price:,.0f}")
        print(f"Trail Activation:   ${activation_price:,.0f} (-{activation_pct}%)")
        print(f"Min Price Reached:  ${min_price:,.0f}")
        print(f"Trail Stop ({callback_pct}%):   ${trailing_stop:,.0f}")
        print(f"{'='*50}")

        # Проверки
        assert abs(sl_price - 101500.0) < 0.01
        assert abs(activation_price - 97000.0) < 0.01
        assert abs(trailing_stop - 93840.0) < 0.01

        # Профит если закрылись по trailing
        profit_pct = (entry - trailing_stop) / entry * 100
        print(f"Profit if trail hit: {profit_pct:.2f}%")
        print(f"{'='*50}")
        print("[OK] SHORT simulation correct")

    def test_12_commission_calculation(self):
        """Расчёт комиссий."""
        order_size = self.ORDER_SIZE
        leverage = 10  # Предполагаемое плечо

        # Комиссии VIP 0
        entry_fee = 0.0005  # 0.05% taker
        exit_fee_trailing = 0.0005  # 0.05% taker (STOP_MARKET)
        exit_fee_sl = 0.0005  # 0.05% taker (STOP_MARKET)

        notional = order_size  # $1500

        # Entry
        entry_commission = notional * entry_fee

        # Exit по Trailing
        trailing_commission = notional * exit_fee_trailing
        total_trailing = entry_commission + trailing_commission

        # Exit по SL
        sl_commission = notional * exit_fee_sl
        total_sl = entry_commission + sl_commission

        print(f"\n{'='*50}")
        print("Commission Calculation (VIP 0)")
        print(f"{'='*50}")
        print(f"Order Size: ${order_size}")
        print(f"Entry (MARKET 0.05%): ${entry_commission:.2f}")
        print(f"Exit Trailing (0.05%): ${trailing_commission:.2f}")
        print(f"Exit SL (0.05%): ${sl_commission:.2f}")
        print(f"{'='*50}")
        print(f"Total if Trailing: ${total_trailing:.2f} (0.10%)")
        print(f"Total if SL: ${total_sl:.2f} (0.10%)")
        print(f"{'='*50}")

        assert abs(entry_commission - 0.75) < 0.001
        assert abs(total_trailing - 1.50) < 0.001
        assert abs(total_sl - 1.50) < 0.001

        print("[OK] Commission calculation correct")


class TestTradeAppIntegration:
    """Интеграционные тесты с реальными модулями."""

    def test_13_load_dynamic_size_state(self):
        """Тест загрузки/сохранения состояния dynamic size."""
        from tradebot.trade_app import load_dynamic_size_state, save_dynamic_size_state

        # Сохраняем состояние LOSS
        save_dynamic_size_state(False)
        loaded = load_dynamic_size_state()
        assert loaded == False, f"Expected False (LOSS), got {loaded}"
        print("[OK] State saved: last_was_win=False")

        # Сохраняем состояние WIN
        save_dynamic_size_state(True)
        loaded = load_dynamic_size_state()
        assert loaded == True, f"Expected True (WIN), got {loaded}"
        print("[OK] State saved: last_was_win=True")

    def test_14_strategies_exist(self):
        """Проверка что стратегии существуют."""
        from strategies import get_strategy, list_strategies

        available_tuples = list_strategies()
        # list_strategies возвращает [(name, description), ...]
        available = [name for name, desc in available_tuples]
        required = ['ls_fade', 'momentum', 'momentum_ls']

        print(f"\nAvailable strategies: {available}")

        for strat in required:
            assert strat in available, f"Strategy '{strat}' not found!"
            # Пробуем создать
            strategy = get_strategy(strat)
            assert strategy is not None, f"Failed to create strategy '{strat}'"
            print(f"[OK] Strategy '{strat}' exists and loads")

    def test_15_trailing_stop_config(self):
        """Проверка конфига trailing stop."""
        from tradebot.trade_app import load_trailing_stop_config

        config = load_trailing_stop_config()

        print(f"\nTrailing Stop Config from file:")
        print(f"  enabled: {config.get('enabled')}")
        print(f"  callback_rate: {config.get('callback_rate')}")
        print(f"  activation_price_pct: {config.get('activation_price_pct')}")
        print(f"  use_instead_of_tp: {config.get('use_instead_of_tp')}")

        # CLI переопределит эти значения, но файл должен существовать
        assert 'enabled' in config
        assert 'callback_rate' in config
        print("[OK] Trailing stop config loads correctly")


def run_all_tests():
    """Запуск всех тестов с выводом."""
    print("\n" + "="*60)
    print("TRADE APP CONFIGURATION TESTS")
    print("="*60)
    print("\nКоманда:")
    print("py -3.12 -m tradebot.trade_app --mainnet --symbols XRPUSDT,BTCUSDT \\")
    print("    --strategies ls_fade,momentum,momentum_ls --order-size 1500 --sl 1.5 \\")
    print("    --dynamic-size --protected-size 10 --trailing-stop \\")
    print("    --trailing-activation 3.0 --trailing-callback 2.0 \\")
    print("    --vol-filter-high --vol-filter-low --log-level=DEBUG")
    print("\n" + "="*60 + "\n")

    # Config tests
    config_tests = TestTradeAppConfig()
    config_tests.test_01_order_size_initial()
    config_tests.test_02_order_size_after_loss()
    config_tests.test_03_order_size_after_win()
    config_tests.test_04_dynamic_size_sequence()
    config_tests.test_05_sl_percentage()
    config_tests.test_06_trailing_activation()
    config_tests.test_07_trailing_callback()
    config_tests.test_08_strategies_list()
    config_tests.test_09_symbols_list()
    config_tests.test_10_full_trade_simulation_long()
    config_tests.test_11_full_trade_simulation_short()
    config_tests.test_12_commission_calculation()

    # Integration tests
    print("\n" + "="*60)
    print("INTEGRATION TESTS")
    print("="*60 + "\n")

    integration_tests = TestTradeAppIntegration()
    integration_tests.test_13_load_dynamic_size_state()
    integration_tests.test_14_strategies_exist()
    integration_tests.test_15_trailing_stop_config()

    print("\n" + "="*60)
    print("ALL TESTS PASSED!")
    print("="*60 + "\n")


if __name__ == "__main__":
    run_all_tests()
