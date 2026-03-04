# -*- coding: utf-8 -*-
"""
Backtest Data Adapter

Converts Excel backtest results into ML training data.
Extracts features and targets from trade history.
"""

import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')


class BacktestDataAdapter:
    """
    Loads Excel backtest files and prepares data for ML training.

    Usage:
        adapter = BacktestDataAdapter('outputNEWARCH')
        X, y = adapter.load_all()

        # Or load specific strategy
        X, y = adapter.load_strategy('ls_fade')
    """

    STRATEGIES = ['ls_fade', 'momentum', 'reversal', 'mean_reversion', 'momentum_ls']

    # HONEST features - only data available at signal time (no look-ahead bias)
    # Uses PREVIOUS DAY's candle data instead of entry day (prev_* columns from Excel)
    # REMOVED: Chain Total, Chain Last (requires future knowledge)
    NUMERIC_FEATURES = [
        # Market Data (L/S) - available from previous day
        'Long %',
        'Short %',

        # Market Data (OI, Funding) - available at signal time
        'Funding Rate',
        'OI USD',
        'OI Contracts',

        # Entry price (only OPEN is known at entry time)
        'Open',

        # Previous Day Candle Data (HONEST - known before signal)
        'Prev High',
        'Prev Low',
        'Prev Close',
        'Prev Volume',
        'Prev Volume USD',
        'Prev Trades Count',
        'Prev Taker Buy Vol',
        'Prev Taker Buy USD',

        # Indicators - calculated from historical data before signal
        'ADX',

        # Trade params - strategy settings, known at signal time
        'SL %',
        'TP %',
        'R:R Ratio',

        # Chain - only past-looking features
        'Chain Seq',    # Position in chain (how many signals so far)
        'Gap Days',     # Days since PREVIOUS signal (known)
    ]

    # Features that need log transform (large values)
    LOG_FEATURES = ['OI USD', 'OI Contracts', 'Prev Volume', 'Prev Volume USD', 'Prev Taker Buy Vol', 'Prev Taker Buy USD']

    # Boolean features - only Chain First (can be determined from Gap Days)
    # REMOVED: Chain Last (requires future knowledge)
    BOOL_FEATURES = ['Chain First']

    def __init__(self, data_dir: str):
        """
        Initialize adapter.

        Args:
            data_dir: Directory containing Excel backtest files
        """
        self.data_dir = Path(data_dir)
        self._symbols: List[str] = []
        self._all_data: Optional[pd.DataFrame] = None

    def load_all(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Load all Excel files and prepare training data.

        Returns:
            Tuple of (X features DataFrame, y targets DataFrame)
        """
        df = self._load_excel_files()
        self._all_data = df

        df_traded = df[df['Result'].isin(['WIN', 'LOSS', 'TIMEOUT'])].copy()
        self._symbols = sorted(df_traded['Symbol'].unique().tolist())

        X = self._extract_features(df_traded, include_strategy=True)
        y = self._extract_targets(df_traded)

        return X, y

    def load_strategy(self, strategy: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Load data for specific strategy only.

        Args:
            strategy: Strategy name (ls_fade, momentum, etc.)

        Returns:
            Tuple of (X features DataFrame, y targets DataFrame)
        """
        if self._all_data is None:
            self._load_excel_files()

        df = self._all_data[self._all_data['Strategy'] == strategy].copy()

        if len(df) == 0:
            raise ValueError(f"No data found for strategy: {strategy}")

        df_traded = df[df['Result'].isin(['WIN', 'LOSS', 'TIMEOUT'])].copy()

        # Update symbols for this strategy
        strategy_symbols = sorted(df_traded['Symbol'].unique().tolist())

        X = self._extract_features(df_traded, include_strategy=False, symbols=strategy_symbols)
        y = self._extract_targets(df_traded)

        return X, y

    def get_strategies(self) -> List[str]:
        """Get list of strategies in data."""
        if self._all_data is None:
            self._load_excel_files()
        return sorted(self._all_data['Strategy'].unique().tolist())

    def get_strategy_stats(self) -> Dict[str, Dict]:
        """Get statistics per strategy."""
        if self._all_data is None:
            self._load_excel_files()

        stats = {}
        for strategy in self.get_strategies():
            df = self._all_data[self._all_data['Strategy'] == strategy]
            traded = df[df['Result'].isin(['WIN', 'LOSS', 'TIMEOUT'])]
            wins = len(traded[traded['Result'] == 'WIN'])

            stats[strategy] = {
                'total': len(traded),
                'wins': wins,
                'losses': len(traded[traded['Result'] == 'LOSS']),
                'win_rate': wins / len(traded) * 100 if len(traded) > 0 else 0,
                'symbols': traded['Symbol'].nunique(),
            }
        return stats

    def _load_excel_files(self) -> pd.DataFrame:
        """Load all Excel files into DataFrame."""
        xlsx_files = list(self.data_dir.glob('backtest_*.xlsx'))

        if not xlsx_files:
            raise FileNotFoundError(f"No backtest Excel files found in {self.data_dir}")

        print(f"Loading {len(xlsx_files)} backtest files...")

        all_trades = []
        for xlsx_path in xlsx_files:
            if xlsx_path.name.startswith('~$'):
                continue

            strategy = xlsx_path.stem.replace('backtest_', '').rsplit('_', 2)[0]

            try:
                trades = pd.read_excel(xlsx_path, sheet_name='Trades')
                trades['Strategy'] = strategy
                trades['Source'] = xlsx_path.name
                all_trades.append(trades)
            except Exception as e:
                print(f"  Error loading {xlsx_path.name}: {e}")
                continue

        if not all_trades:
            raise ValueError("No trades loaded from Excel files")

        df = pd.concat(all_trades, ignore_index=True)
        self._all_data = df

        print(f"Total trades: {len(df)}")
        return df

    def _extract_features(
        self,
        df: pd.DataFrame,
        include_strategy: bool = True,
        symbols: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """Extract ALL features from trades DataFrame."""
        features = pd.DataFrame(index=df.index)

        # Numeric features
        for col in self.NUMERIC_FEATURES:
            if col in df.columns:
                values = pd.to_numeric(df[col], errors='coerce').fillna(0)
                # Log transform for large value features
                if col in self.LOG_FEATURES:
                    values = np.log1p(np.abs(values))
                features[col] = values

        # Boolean features (Chain First, Chain Last)
        for col in self.BOOL_FEATURES:
            if col in df.columns:
                features[col] = df[col].astype(int)

        # Direction
        features['Direction_num'] = df['Direction'].map({'LONG': 1, 'SHORT': -1}).fillna(0)

        # Strategy one-hot (only if training all strategies together)
        if include_strategy:
            for strat in self.STRATEGIES:
                features[f'Strategy_{strat}'] = (df['Strategy'] == strat).astype(int)

        # Symbol one-hot
        symbols_to_use = symbols if symbols else self._symbols
        for symbol in symbols_to_use:
            features[f'Symbol_{symbol}'] = (df['Symbol'] == symbol).astype(int)

        # Date features
        if 'Signal Date' in df.columns:
            dates = pd.to_datetime(df['Signal Date'], errors='coerce')
            features['DayOfWeek'] = dates.dt.dayofweek.fillna(0)
            features['Month'] = dates.dt.month.fillna(1)
            features['Hour'] = dates.dt.hour.fillna(0)

        # Derived features (HONEST - using previous day data, no look-ahead bias)
        if 'Long %' in df.columns:
            # L/S extremeness (how far from 50/50) - available at signal time
            features['LS_Extreme'] = np.abs(df['Long %'].fillna(0.5) - 0.5)

            # L/S Ratio (calculate if not in data)
            if 'L/S Ratio' not in df.columns and 'Short %' in df.columns:
                short_pct = df['Short %'].fillna(0.5)
                short_pct = short_pct.replace(0, 0.001)  # Avoid division by zero
                features['L/S Ratio'] = df['Long %'].fillna(0.5) / short_pct

        # Volatility from PREVIOUS DAY (HONEST - no look-ahead bias)
        if 'Prev High' in df.columns and 'Prev Low' in df.columns and 'Prev Close' in df.columns:
            prev_close = df['Prev Close'].fillna(1)
            features['Prev_Volatility'] = ((df['Prev High'] - df['Prev Low']) / prev_close).fillna(0)

        # Buy pressure from PREVIOUS DAY (HONEST - no look-ahead bias)
        if 'Prev Taker Buy USD' in df.columns and 'Prev Volume USD' in df.columns:
            prev_vol = df['Prev Volume USD'].fillna(1)
            features['Prev_BuyPressure'] = (df['Prev Taker Buy USD'].fillna(0) / prev_vol).fillna(0.5)

        # Candle direction from PREVIOUS DAY (HONEST - no look-ahead bias)
        # Note: We use Open of entry day vs Prev Close to determine momentum into signal
        if 'Open' in df.columns and 'Prev Close' in df.columns:
            features['Prev_CandleDir'] = np.sign(df['Prev Close'].fillna(0) - df['Prev Low'].fillna(0))

        features = features.fillna(0)
        return features

    def _extract_targets(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extract target variables from trades DataFrame."""
        targets = pd.DataFrame(index=df.index)

        # Confidence: WIN = 1, LOSS = 0
        targets['target_win'] = (df['Result'] == 'WIN').astype(int)

        # Filter: Profitable direction = 1, Not profitable = 0
        targets['target_filter'] = ((df['Net PnL %'] > 0) | (df['Result'] == 'WIN')).astype(int)

        # Direction: LONG correct = 1, SHORT correct = -1, Wrong = 0 (should skip)
        is_profitable = (df['Result'] == 'WIN') | (df['Net PnL %'] > 0)
        direction_map = df['Direction'].map({'LONG': 1, 'SHORT': -1}).fillna(0)
        targets['target_direction'] = np.where(is_profitable, direction_map, 0).astype(int)

        # SL/TP Regressors
        targets['target_sl'] = df['SL %'].fillna(4.0)
        targets['target_tp'] = df['TP %'].fillna(10.0)

        # Lifetime: Hold days
        targets['target_lifetime'] = df['Hold Days'].fillna(1)

        # Extra
        targets['actual_pnl'] = df['Net PnL %'].fillna(0)

        return targets

    def get_symbols(self) -> List[str]:
        """Get list of symbols found in data."""
        return self._symbols


if __name__ == '__main__':
    import sys
    data_dir = sys.argv[1] if len(sys.argv) > 1 else 'outputNEWARCH'

    adapter = BacktestDataAdapter(data_dir)

    print("\n=== Strategy Stats ===")
    stats = adapter.get_strategy_stats()
    for strat, s in stats.items():
        print(f"{strat}: {s['total']} trades, {s['win_rate']:.1f}% WR, {s['symbols']} symbols")
