"""
Renders a dark-themed candlestick chart (last N candles) with EMA overlays
and an arrow marking the signal candle. Saves to a PNG and returns the path.
"""
import os
import uuid

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

CHART_DIR = os.path.join(os.path.dirname(__file__), "charts")
os.makedirs(CHART_DIR, exist_ok=True)

_dark_style = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=mpf.make_marketcolors(
        up="#26a69a", down="#ef5350", edge="inherit", wick="inherit", volume="inherit"
    ),
    facecolor="#131722",
    edgecolor="#131722",
    figcolor="#131722",
    gridcolor="#2a2e39",
    gridstyle="--",
    rc={"axes.labelcolor": "#d1d4dc", "xtick.color": "#d1d4dc", "ytick.color": "#d1d4dc"},
)


def render_signal_chart(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    fast_col: str,
    slow_col: str,
    fast_period: int,
    slow_period: int,
    signal_direction: str,
    signal_ema_label: str,
) -> str:
    """
    df must already have fast/slow EMA columns and a datetime index.
    The signal candle is assumed to be the second-to-last row (last closed candle).
    Returns the filepath of the saved PNG.
    """
    plot_df = df.tail(50).copy()

    addplots = [
        mpf.make_addplot(plot_df[fast_col], color="#42a5f5", width=1.2),
        mpf.make_addplot(plot_df[slow_col], color="#ffa726", width=1.2),
    ]

    signal_idx = len(plot_df) - 2  # last closed candle within the plotted window
    signal_idx = max(0, min(signal_idx, len(plot_df) - 1))
    marker_series = pd.Series(index=plot_df.index, dtype=float)
    signal_price = plot_df.iloc[signal_idx]["close"]
    offset = plot_df["high"].max() * 0.01
    marker_series.iloc[signal_idx] = signal_price + (offset if signal_direction == "up" else -offset)
    marker_color = "#26a69a" if signal_direction == "up" else "#ef5350"
    marker_symbol = "^" if signal_direction == "up" else "v"
    addplots.append(
        mpf.make_addplot(
            marker_series, type="scatter", markersize=140, marker=marker_symbol, color=marker_color
        )
    )

    title = (
        f"{symbol}  |  {timeframe}  |  EMA{fast_period}/{slow_period} cross "
        f"{signal_ema_label.upper()} ({'UP' if signal_direction == 'up' else 'DOWN'})"
    )

    filename = f"{symbol}_{timeframe}_{uuid.uuid4().hex[:8]}.png"
    filepath = os.path.join(CHART_DIR, filename)

    fig, axes = mpf.plot(
        plot_df,
        type="candle",
        style=_dark_style,
        addplot=addplots,
        volume=True,
        title=title,
        returnfig=True,
        figsize=(10, 6),
        tight_layout=True,
    )
    fig.savefig(filepath, dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)
    return filepath
