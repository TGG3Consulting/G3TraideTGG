# -*- coding: utf-8 -*-
"""
Download L/S ratio data from Coinalyze and add to Excel
"""
import pandas as pd
import os
import sys
from datetime import datetime, timezone, timedelta

# Add path for imports
sys.path.insert(0, r'G:\BinanceFriend\GenerateHistorySignals')

from coinalyze_client import CoinalyzeClient

# API key from config
API_KEY = "adb282f9-7e9e-4b6c-a669-b01c0304d506"

# Load existing Excel
excel_path = r'G:\TradeAI1\TimeLagTester\BTCUSDT_volatility_2y.xlsx'
df = pd.read_excel(excel_path)

print(f"Loaded Excel: {len(df)} rows")
print(f"Period: {df['Date'].iloc[0]} to {df['Date'].iloc[-1]}")
print(f"Columns: {list(df.columns)}")
print()

# Initialize Coinalyze client
client = CoinalyzeClient(api_key=API_KEY)

# Download L/S data for 2 years
start_date = datetime(2024, 3, 22, tzinfo=timezone.utc)
end_date = datetime(2026, 3, 21, tzinfo=timezone.utc)

print(f"Downloading L/S data from Coinalyze...")
print(f"Period: {start_date.date()} to {end_date.date()}")
print()

ls_data = client.download_ls_ratio_history(
    symbol="BTCUSDT",
    start_time=start_date,
    end_time=end_date,
    interval="daily"
)

print(f"Downloaded {len(ls_data)} L/S records")
print()

if len(ls_data) == 0:
    print("ERROR: No L/S data downloaded!")
    exit(1)

# Convert to DataFrame
ls_df = pd.DataFrame(ls_data)
ls_df['date'] = pd.to_datetime(ls_df['timestamp'], unit='ms').dt.date
ls_df['longAccount'] = ls_df['longAccount'].astype(float)
ls_df['shortAccount'] = ls_df['shortAccount'].astype(float)

print("L/S data sample:")
print(ls_df[['date', 'longAccount', 'shortAccount']].head())
print()

# Convert Excel Date to date for matching
df['date_match'] = pd.to_datetime(df['Date']).dt.date

# Merge
df = df.merge(
    ls_df[['date', 'longAccount', 'shortAccount']],
    left_on='date_match',
    right_on='date',
    how='left'
)

# Clean up
df.drop(columns=['date_match', 'date'], inplace=True)

# Check result
print(f"After merge: {len(df)} rows")
print(f"L/S data filled: {df['longAccount'].notna().sum()} / {len(df)}")
print()

# Show sample
print("Sample with L/S data:")
print(df[['Date', 'Close', 'longAccount', 'shortAccount']].tail(10).to_string(index=False))
print()

# Save to new file
output_path = r'G:\BinanceFriend\BTCUSDT_volatility_2y_with_LS.xlsx'
df.to_excel(output_path, index=False)
print(f"Saved: {output_path}")
