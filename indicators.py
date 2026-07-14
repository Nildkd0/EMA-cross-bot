"""
Indicator math: EMA, volume stats, and the EMA-cross signal detector.
"""
import pandas as pd


def candles_to_df(candles: list) -> pd.DataFrame:
    df = pd.DataFrame(candles, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("time").astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float}
    )
    return df


def add_ema(df: pd.DataFrame, period: int, col_name: str = None) -> pd.DataFrame:
    col_name = col_name or f"ema_{period}"
    df[col_name] = df["close"].ewm(span=period, adjust=False).mean()
    return df


def add_volume_stats(df: pd.DataFrame, avg_period: int = 20) -> pd.DataFrame:
    """
    Adds:
      - vol_avg: rolling average volume over `avg_period` candles
      - vol_rel: this candle's volume as a multiple of that average
        (e.g. 2.0 = twice the recent average volume, 0.5 = half)
    A high vol_rel on the signal candle means the cross happened on
    unusually strong participation, not a quiet, low-conviction move.
    """
    df["vol_avg"] = df["volume"].rolling(window=avg_period, min_periods=1).mean()
    df["vol_rel"] = df["volume"] / df["vol_avg"].replace(0, float("nan"))
    return df


def add_daily_vwap_series(df: pd.DataFrame, session_start, col_name: str = "vwap") -> pd.DataFrame:
    """
    Adds a cumulative volume-weighted average price column, resetting at
    `session_start` (e.g. the most recent UTC midnight). Rows before
    session_start get NaN — they're the previous session, not "today".
    Uses typical price (H+L+C)/3. The same column powers both the caption
    text (its last value) and the chart overlay (the whole evolving line).
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3
    in_session = df.index >= session_start
    pv = (typical * df["volume"]).where(in_session, 0.0)
    vol = df["volume"].where(in_session, 0.0)
    cum_pv = pv.cumsum()
    cum_vol = vol.cumsum()
    vwap = cum_pv / cum_vol.replace(0, float("nan"))
    df[col_name] = vwap.where(in_session, float("nan"))
    return df


def detect_ema_cross(df: pd.DataFrame, fast_col: str, slow_col: str):
    """
    Look at the last *closed* candle vs the one before it and report whether
    price crossed the fast or slow EMA.

    Returns a list of dicts, each: {"ema": "fast"|"slow", "direction": "up"|"down"}
    (there can be 0, 1, or 2 simultaneous crosses on the last closed candle).
    """
    if len(df) < 3:
        return []

    prev = df.iloc[-3]
    last = df.iloc[-2]  # last fully closed candle (the most recent row may be an in-progress candle)

    signals = []
    for label, col in (("fast", fast_col), ("slow", slow_col)):
        prev_above = prev["close"] > prev[col]
        last_above = last["close"] > last[col]
        if prev_above != last_above:
            signals.append({"ema": label, "direction": "up" if last_above else "down"})
    return signals
