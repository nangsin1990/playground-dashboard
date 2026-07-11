# FILE: leadership.py

from __future__ import annotations
import pandas as pd
import data_engine as eng
from cache_utils import ttl_cache  # ✨ FIX 1: Import ttl_cache
from constants import CACHE_TTL_DATA # ✨ FIX 1: Import constant for cache

# ✨ FIX 2: Add the @ttl_cache decorator to enable caching and .cache_clear()
@ttl_cache(CACHE_TTL_DATA)
def build_leadership_board(mode: str = "core") -> dict:
    """
    Analyzes the main pipeline data to find market leaders based on strength.

    Args:
        mode (str): The pipeline mode to use (e.g., 'core').

    Returns:
        dict: A dictionary containing leadership data or an error message.
    """
    try:
        # Fetch the processed data from the main pipeline
        pipeline_data = eng.pipeline(mode=mode)

        if not pipeline_data["ok"]:
            return {"ok": False, "error": "Pipeline data is not available."}

        df = pipeline_data["data"]

        # Ensure required columns are present
        required_cols = ['close', 'sma50', 'sma200', 'high252d', 'low252d']
        if not all(col in df.columns for col in required_cols):
            return {"ok": False, "error": "Pipeline data is missing required columns."}

        # --- Calculations ---
        # 1. Trend (using SMAs)
        df['trend_status'] = df.apply(
            lambda row: 2 if row['close'] > row['sma50'] > row['sma200'] else  # Strong Uptrend
                      1 if row['close'] > row['sma200'] else                    # Uptrend
                      -2 if row['close'] < row['sma50'] < row['sma200'] else # Strong Downtrend
                      -1 if row['close'] < row['sma200'] else                   # Downtrend
                      0,                                                      # Sideways
            axis=1
        )

        # 2. Pullback from 52-week high
        df['pullback_pct'] = ((df['close'] - df['high252d']) / df['high252d']) * 100

        # 3. ✨ FIX (from previous conversation): Calculate Drawdown from 52-week high
        # This calculates how far the current price is from the 52-week high, as a positive percentage.
        df['drawdown_pct'] = ((df['high252d'] - df['close']) / df['high252d']) * 100

        # --- Filtering and Ranking ---
        # Filter for stocks in an uptrend and near their 52-week highs (e.g., within a 25% pullback)
        leaders = df[
            (df['trend_status'] > 0) & 
            (df['pullback_pct'] > -25)
        ].copy()

        # Simple Strength Score
        leaders['strength_score'] = (
            (1 - abs(leaders['pullback_pct'] / 25)) * 0.7 + 
            (leaders['trend_status'] / 2) * 0.3
        )

        # Sort by the score
        leaders = leaders.sort_values(by='strength_score', ascending=False)

        # Format for API output
        output_data = []
        for ticker, row in leaders.head(50).iterrows(): # Limit to top 50
            output_data.append({
                "ticker": ticker,
                "name": pipeline_data.get("meta", {}).get(ticker, {}).get("shortName", ticker),
                "close": row["close"],
                "strength_score": row["strength_score"],
                "pullback_pct": row["pullback_pct"],
                "drawdown_pct": row["drawdown_pct"], # Include the corrected drawdown
                "trend": row["trend_status"],
            })

        return {
            "ok": True,
            "data": output_data,
        }

    except Exception as e:
        # Log the exception here in a real application
        return {"ok": False, "error": f"An unexpected error occurred in build_leadership_board: {str(e)}"}
