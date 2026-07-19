"""
Range strategy — completely independent of ORB and sessions.

For every symbol+timeframe, on each new closed candle: look at the last
RANGE_LOOKBACK_CANDLES closed candles. If they all fit within a band
RANGE_WIDTH_PCT wide (measured as (high-low)/low over that window), the
symbol is considered to be "ranging" on that timeframe.

  - The moment ranging is first detected: send a one-time "range formed"
    update with the band's high/low, and start suppressing EMA cross
    alerts for that symbol+timeframe.
  - While still ranging: stay quiet, keep suppressing EMA alerts. No
    repeated "still ranging" spam.
  - When the latest closed candle's close moves outside the established
    band: send a one-time breakout alert (up or down) and stop suppressing
    EMA — alerts resume normally from the next candle.

This uses the same df already fetched/computed for EMA scanning (no extra
API calls) — just called once per symbol+timeframe alongside the EMA check.

Returns True from check_range() when EMA alerts should be suppressed this
candle (currently ranging, or a breakout just happened on this very candle
— suppressed once more so the breakout message doesn't compete with an EMA
alert on the same candle; EMA resumes from the next candle onward).
"""
import logging

import chart as chart_mod
import notifier
import state_file as st
from config import config
from tzhelper import to_ist_str

log = logging.getLogger("range")


def _send_with_chart(df, symbol: str, timeframe: str, range_high: float, range_low: float,
                       caption: str, thread_id: int, direction: str = None):
    try:
        chart_path = chart_mod.render_range_chart(
            df, symbol, timeframe, range_high, range_low, "Range", direction=direction
        )
        notifier.send_photo(chart_path, caption=caption, thread_id=thread_id)
    except Exception as e:  # noqa: BLE001
        log.error("Range chart failed for %s %s, falling back to text: %s", symbol, timeframe, e)
        notifier.send_message(caption, thread_id=thread_id)


def check_range(symbol: str, timeframe: str, df, state: dict) -> bool:
    lookback = config.RANGE_LOOKBACK_CANDLES
    # Last `lookback` CLOSED candles, excluding the current in-progress one.
    window = df.iloc[-(lookback + 1):-1]
    if len(window) < lookback:
        return False  # not enough history yet — don't suppress, don't flag

    entry = st.get_range_entry(state, symbol, timeframe)
    if entry is None:
        entry = {"in_range": False, "high": None, "low": None}

    last_closed = df.iloc[-2]
    close = last_closed["close"]
    candle_time_str = to_ist_str(last_closed.name.to_pydatetime())

    if not entry["in_range"]:
        candidate_high = float(window["high"].max())
        candidate_low = float(window["low"].min())
        if candidate_low <= 0:
            return False
        width_pct = (candidate_high - candidate_low) / candidate_low * 100
        if width_pct <= config.RANGE_WIDTH_PCT:
            entry = {"in_range": True, "high": candidate_high, "low": candidate_low}
            st.set_range_entry(state, symbol, timeframe, entry)
            caption = (
                f"↔️ *{symbol}* — {timeframe} range formed\n"
                f"High: `{candidate_high:.4f}`  Low: `{candidate_low:.4f}` "
                f"({width_pct:.2f}% wide)\n"
                f"_EMA alerts paused for this pair until it breaks out._\n"
                f"_{candle_time_str}_"
            )
            _send_with_chart(
                df, symbol, timeframe, candidate_high, candidate_low, caption,
                thread_id=config.TELEGRAM_THREAD_RANGE_FORMING,
            )
            return True
        return False

    # Already ranging — check for a breakout against the established band
    if close > entry["high"]:
        pct = (close - entry["high"]) / entry["high"] * 100
        caption = (
            f"🟢⬆️ *{symbol}* — {timeframe} range breakout UP\n"
            f"Close `{close:.4f}` broke above range high `{entry['high']:.4f}` ({pct:+.2f}%)\n"
            f"_EMA alerts resume from the next candle._\n"
            f"_{candle_time_str}_"
        )
        _send_with_chart(
            df, symbol, timeframe, entry["high"], entry["low"], caption,
            thread_id=config.TELEGRAM_THREAD_RANGE_BREAKOUT, direction="up",
        )
        st.set_range_entry(state, symbol, timeframe, {"in_range": False, "high": None, "low": None})
        return True  # still suppress on this same breakout candle
    elif close < entry["low"]:
        pct = (close - entry["low"]) / entry["low"] * 100
        caption = (
            f"🔴⬇️ *{symbol}* — {timeframe} range breakout DOWN\n"
            f"Close `{close:.4f}` broke below range low `{entry['low']:.4f}` ({pct:+.2f}%)\n"
            f"_EMA alerts resume from the next candle._\n"
            f"_{candle_time_str}_"
        )
        _send_with_chart(
            df, symbol, timeframe, entry["high"], entry["low"], caption,
            thread_id=config.TELEGRAM_THREAD_RANGE_BREAKOUT, direction="down",
        )
        st.set_range_entry(state, symbol, timeframe, {"in_range": False, "high": None, "low": None})
        return True

    # Still inside the band — no new message, keep suppressing
    return True
