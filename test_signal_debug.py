# -*- coding: utf-8 -*-
"""
Debug script to test signal generation.
"""
import sys
sys.path.insert(0, '.')

from config.settings import settings
from src.signals.models import SignalConfig

print("=" * 60)
print("SIGNAL CONFIG DEBUG")
print("=" * 60)

# Test 1: Check settings.signals values
print("\n1. Settings.signals values from config.yaml:")
s = settings.signals
print(f"   min_accumulation_score: {s.min_accumulation_score}")
print(f"   min_probability: {s.min_probability}")
print(f"   min_risk_reward: {s.min_risk_reward}")
print(f"   enabled: {s.enabled}")

# Test 2: Check SignalConfig.from_settings()
print("\n2. SignalConfig.from_settings():")
try:
    config = SignalConfig.from_settings()
    print(f"   SUCCESS!")
    print(f"   min_accumulation_score: {config.min_accumulation_score}")
    print(f"   min_probability: {config.min_probability}")
    print(f"   min_risk_reward: {config.min_risk_reward}")
except Exception as e:
    print(f"   FAILED: {e}")

# Test 3: Compare values
print("\n3. Are values correct?")
expected_accum = 5
expected_prob = 20
expected_rr = 0.5

if config.min_accumulation_score == expected_accum:
    print(f"   ✓ min_accumulation_score = {expected_accum}")
else:
    print(f"   ✗ min_accumulation_score = {config.min_accumulation_score} (expected {expected_accum})")

if config.min_probability == expected_prob:
    print(f"   ✓ min_probability = {expected_prob}")
else:
    print(f"   ✗ min_probability = {config.min_probability} (expected {expected_prob})")

if config.min_risk_reward == expected_rr:
    print(f"   ✓ min_risk_reward = {expected_rr}")
else:
    print(f"   ✗ min_risk_reward = {config.min_risk_reward} (expected {expected_rr})")

print("\n" + "=" * 60)
