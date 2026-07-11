import pandas as pd
import numpy as np
import pytest
from datetime import datetime, timedelta

# Import the engine and constants we want to test
from data_engine import blended_return, scan_volume_dry_up, add_indicators
from constants import VDU_VOL_LOW, VDU_VOL_HIGH, VOL_SMA

# A helper to create mock price data easily
def create_mock_series(days: int, start_price: float, daily_change_pct: float) -> pd.Series:
    """Creates a mock pandas Series of close prices."""
    prices = [start_price]
    for _ in range(1, days):
        prices.append(prices[-1] * (1 + daily_change_pct / 100))

    dates = [datetime.now() - timedelta(days=i) for i in range(days)]
    dates.reverse() # Make dates chronological
    return pd.Series(prices, index=pd.to_datetime(dates))

# === Test Case 1: blended_return ===
# This is the core of RS Rating. We must get this right.

def test_blended_return_positive_trend():
    """Test with a simple, consistent uptrend over a year."""
    # Create data for 300 trading days, growing 0.1% daily
    close_prices = create_mock_series(days=300, start_price=100, daily_change_pct=0.1)

    # Expected returns (approximate)
    # 3M (63 days): (100 * 1.001^63) / 100 - 1 = ~6.5%
    # 6M (126 days): (100 * 1.001^126) / 100 - 1 = ~13.4%
    # 9M (189 days): (100 * 1.001^189) / 100 - 1 = ~20.8%
    # 12M (252 days): (100 * 1.001^252) / 100 - 1 = ~28.6%
    expected_3m = (1.001**63) - 1
    expected_6m = (1.001**126) - 1
    expected_9m = (1.001**189) - 1
    expected_12m = (1.001**252) - 1

    expected_blend = (0.4 * expected_3m) + (0.2 * expected_6m) + (0.2 * expected_9m) + (0.2 * expected_12m)

    actual_blend = blended_return(close_prices)

    # Use pytest.approx for floating point comparison
    assert actual_blend == pytest.approx(expected_blend)

def test_blended_return_not_enough_data():
    """Test when data is less than 3 months old. It should not crash."""
    close_prices = create_mock_series(days=50, start_price=100, daily_change_pct=0.1)
    actual_blend = blended_return(close_prices)

    # Should calculate based on available data, not error out.
    expected_3m = (1.001**50) - 1 # It will use 50 days as max lookback
    expected_blend = (0.4 * expected_3m) + (0.2 * 0) + (0.2 * 0) + (0.2 * 0)

    assert actual_blend == pytest.approx(expected_blend)


# === Test Case 2: scan_volume_dry_up ===
# This tests one of our key scanners.

def test_scan_volume_dry_up_signal_triggers():
    """Test the VDU scanner to ensure it fires when conditions are met."""
    # Create a DataFrame with indicator columns
    data = {
        'Open': [100]*51, 'High': [100]*51, 'Low': [100]*51, 'Close': [100]*51,
        'Volume': [1_000_000] * 50 + [500_000] # 50 days of high vol, last day is low
    }
    df = pd.DataFrame(data)
    df_with_indicators = add_indicators(df) # This will calculate VOL_SMA50

    # Manually verify the condition for the last day
    # VOL_SMA50 will be the average of the first 50 days = 1,000,000
    # Last day volume = 500,000
    # Ratio = 500,000 / 1,000,000 = 0.5
    # VDU_VOL_LOW (0.40) <= 0.5 <= VDU_VOL_HIGH (0.60) -> TRUE

    result_series = scan_volume_dry_up(df_with_indicators)

    # The last value in the series should be True
    assert result_series.iloc[-1] == True

def test_scan_volume_dry_up_no_signal():
    """Test that VDU does NOT fire when volume is too high."""
    data = {
        'Open': [100]*51, 'High': [100]*51, 'Low': [100]*51, 'Close': [100]*51,
        'Volume': [1_000_000] * 51 # All days have high volume
    }
    df = pd.DataFrame(data)
    df_with_indicators = add_indicators(df)

    result_series = scan_volume_dry_up(df_with_indicators)

    # The last value in the series should be False
    assert result_series.iloc[-1] == False
