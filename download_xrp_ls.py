# -*- coding: utf-8 -*-
import pandas as pd
import sys
from datetime import datetime, timezone

sys.path.insert(0, r'G:\BinanceFriend\GenerateHistorySignals')
from coinalyze_client import CoinalyzeClient

API_KEY = "adb282f9-7e9e-4b6c-a669-b01c0304d506"

# Load XRP Excel
excel_path = r'G:\TradeAI1\TimeLagTester\XRPUSDT_volatility_2y.xlsx'
df = pd.read_excel(excel_path)

print(f"Loaded XRP: {len(df)} rows")
print(f"Period: {df['Date'].iloc[0]} - {df['Date'].iloc[-1]}")
print()

# Download L/S
client = CoinalyzeClient(api_key=API_KEY)

start_date = datetime(2024, 3, 22, tzinfo=timezone.utc)
end_date = datetime(2026, 3, 21, tzinfo=timezone.utc)

print("Downloading L/S data for XRPUSDT...")
ls_data = client.download_ls_ratio_history(
    symbol="XRPUSDT",
    start_time=start_date,
    end_time=end_date,
    interval="daily"
)

print(f"Downloaded {len(ls_data)} records")
print()

if len(ls_data) == 0:
    print("ERROR: No L/S data!")
    exit(1)

ls_df = pd.DataFrame(ls_data)
ls_df['date'] = pd.to_datetime(ls_df['timestamp'], unit='ms').dt.date
ls_df['longAccount'] = ls_df['longAccount'].astype(float)
ls_df['shortAccount'] = ls_df['shortAccount'].astype(float)

df['date_match'] = pd.to_datetime(df['Date']).dt.date
df = df.merge(ls_df[['date', 'longAccount', 'shortAccount']], left_on='date_match', right_on='date', how='left')
df.drop(columns=['date_match', 'date'], inplace=True)

print(f"L/S filled: {df['longAccount'].notna().sum()} / {len(df)}")

output_path = r'G:\BinanceFriend\XRPUSDT_volatility_2y_with_LS.xlsx'
df.to_excel(output_path, index=False)
print(f"Saved: {output_path}")
