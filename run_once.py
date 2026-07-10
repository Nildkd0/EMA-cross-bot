"""
import logging
import sys
import time
from datetime import datetime, timezone

from config import config
import delta_client
import indicators
import chart as chart_mod
import notifier
import state_file as st

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("run_once")

REQUEST_SPACING_SECONDS = 0.2


def scan_symbol(symbol: str, timeframe: str, resolution_seconds: int, state: dict):
    now = int(time.time())
    lookback_seconds = resolution_seconds * (config.CANDLE_LOOKBACK + 5)
    start = now - lookback_seconds

    candles = delta_client.get_candles(symbol, timeframe, start, now)
    min_rows = max(config.EMA_SLOW, 30) + 3
    if len(candles) < min_rows:
        return

    df = indicators.candles_to_df(candles)
    fast_col = f"ema_{config.EMA_FAST}"
    slow_col = f"ema_{config.EMA_SLOW}"
    indicators.add_ema(df, config.EMA_FAST, fast_col)
    indicators.add_ema(df, config.EMA_SLOW, slow_col)
    indicators.add_volume_stats(df)

    last_closed = df.iloc[-2]
    candle_epoch = int(last_closed.name.timestamp())

    if not st.is_new_candle(state, symbol, timeframe, candle_epoch):
        return
    st.mark_candle_seen(state, symbol, timeframe, candle_epoch)

    crosses = indicators.detect_ema_cross(df, fast_col, slow_col)
    if not crosses:
        return

    candle_idx = candle_epoch // resolution_seconds

    for cross in crosses:
        ema_label = cross["ema"]
        direction = cross["direction"]
        if not st.should_fire(state, symbol, timeframe, ema_label, direction, candle_idx, config.ANTI_SPAM_CANDLES):
            continue

        ema_period = config.EMA_FAST if ema_label == "fast" else config.EMA_SLOW
        arrow = "🟢⬆️" if direction == "up" else "🔴⬇️"
        vol_rel = last_closed.get("vol_rel", float("nan"))
        vol_tag = ""
        if vol_rel == vol_rel:  # not NaN
            if vol_rel >= 1.5:
                vol_tag = f" 🔥 {vol_rel:.1f}x avg"
            elif vol_rel <= 0.5:
                vol_tag = f" (quiet, {vol_rel:.1f}x avg)"
            else:
                vol_tag = f" ({vol_rel:.1f}x avg)"

        caption = (
            f"{arrow} *{symbol}* — {timeframe}\n"
            f"Price crossed *EMA{ema_period}* {direction.upper()}\n"
            f"Close: `{last_closed['close']:.4f}`\n"
            f"Volume: `{last_closed['volume']:,.0f}`{vol_tag}\n"
            f"_{last_closed.name.strftime('%Y-%m-%d %H:%M UTC')}_"
        )

        try:
            chart_path = chart_mod.render_signal_chart(
                df, symbol, timeframe, fast_col, slow_col,
                config.EMA_FAST, config.EMA_SLOW, direction, ema_label,
            )
            notifier.send_photo(chart_path, caption=caption)
        except Exception as e:  # noqa: BLE001
            log.error("Chart/send failed for %s %s, falling back to text: %s", symbol, timeframe, e)
            notifier.send_message(caption)

        log.info("Signal fired: %s %s EMA%s %s", symbol, timeframe, ema_period, direction)


def format_summary_table(rows, value_key, is_percent=False):
    lines = []
    for i, r in enumerate(rows, 1):
        val = r[value_key]
        val_str = f"{val:+.2f}%" if is_percent else f"{val:,.0f}"
