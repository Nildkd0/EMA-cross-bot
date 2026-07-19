"""
Opening Range Breakout (ORB) strategy for the Asia, London, and New York
sessions.

Session start times are defined in IST (Asia/Kolkata, a fixed UTC+5:30 all
year — India doesn't observe daylight saving) and converted to UTC
internally for the actual candle-timestamp math, since Delta's API always
returns UTC epoch timestamps regardless of which zone you think in.

For each session, the "opening range" is the high/low of the first
ORB_RANGE_MINUTES (default 30) after that session's start. Once a session's
range has fully formed for the day, this checks the most recently *closed*
candle (on ORB_TIMEFRAME, default 5m — a closed candle, not just a wick, to
avoid alerting on a single-tick spike) against that range. A close above
the range high or below the range low fires a one-time breakout alert;
each direction only alerts once per session per day.

This is a completely separate strategy from range.py's general
consolidation-range detector — ORB only concerns itself with the fixed
30-minute window right after each session opens, regardless of whether
price is "ranging" by range.py's definition at any other time of day.

State (which ranges have formed, which breakouts already fired) persists
in state.json across runs, same mechanism as the EMA cross state.
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import chart as chart_mod
import delta_client
import indicators
import notifier
import state_file as st
from config import config

log = logging.getLogger("orb")

IST = ZoneInfo("Asia/Kolkata")

SESSIONS = {
    "asia": config.ORB_ASIA_START_IST,
    "london": config.ORB_LONDON_START_IST,
    "newyork": config.ORB_NEWYORK_START_IST,
}


def _parse_hhmm(hhmm: str):
    h, m = hhmm.split(":")
    return int(h), int(m)


def _session_window(now_dt: datetime, start_ist_hhmm: str):
    """
    Today's session start/end, both returned as UTC-aware datetimes (so
    they compare directly against candle timestamps, which are UTC).
    Returns (None, None) if that session hasn't started yet today in IST.
    """
    now_ist = now_dt.astimezone(IST)
    hour, minute = _parse_hhmm(start_ist_hhmm)
    session_start_ist = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_ist < session_start_ist:
        return None, None
    session_start_utc = session_start_ist.astimezone(timezone.utc)
    range_end_utc = session_start_utc + timedelta(minutes=config.ORB_RANGE_MINUTES)
    return session_start_utc, range_end_utc


def in_opening_range_window(now_dt: datetime = None) -> bool:
    """
    True if `now` falls within ANY session's opening-range-forming window
    (session start through session start + ORB_RANGE_MINUTES) today, IST-based.
    NOTE: this is informational only — EMA suppression is driven by
    range.py's per-symbol ranging state, not by this ORB window.
    """
    now_dt = now_dt or datetime.now(timezone.utc)
    for start_hhmm in SESSIONS.values():
        session_start, range_end = _session_window(now_dt, start_hhmm)
        if session_start is not None and session_start <= now_dt < range_end:
            return True
    return False


def _send_with_chart(symbol: str, session: str, range_high: float, range_low: float,
                       caption: str, chart_end_ts: int, direction: str = None):
    resolution_seconds = config.RESOLUTION_SECONDS[config.ORB_TIMEFRAME]
    try:
        chart_start_ts = chart_end_ts - resolution_seconds * 50
        chart_candles = delta_client.get_candles(symbol, config.ORB_TIMEFRAME, chart_start_ts, chart_end_ts)
        if len(chart_candles) < 5:
            raise RuntimeError("not enough candles returned for chart")
        df = indicators.candles_to_df(chart_candles)
        chart_path = chart_mod.render_range_chart(
            df, symbol, config.ORB_TIMEFRAME, range_high, range_low,
            f"{session.title()} ORB", direction=direction,
        )
        notifier.send_photo(chart_path, caption=caption)
    except Exception as e:  # noqa: BLE001
        log.error("ORB chart failed for %s (%s), falling back to text: %s", symbol, session, e)
        notifier.send_message(caption)


def _check_symbol(symbol: str, session: str, start_ist_hhmm: str, state: dict, now_dt: datetime):
    session_start, range_end = _session_window(now_dt, start_ist_hhmm)
    if session_start is None:
        return  # this session hasn't started yet today for this run

    # Group by the IST calendar date the session belongs to (clearer for an
    # IST-based user than grouping by UTC date, which can differ near midnight IST).
    date_str = session_start.astimezone(IST).strftime("%Y-%m-%d")
    entry = st.get_orb_entry(state, symbol, session, date_str)
    if entry is None:
        entry = {"formed": False, "high": None, "low": None, "up_alerted": False, "down_alerted": False}

    if not entry["formed"]:
        if now_dt < range_end:
            return  # still forming, nothing to do yet
        start_ts = int(session_start.timestamp())
        end_ts = int(range_end.timestamp())
        candles = delta_client.get_candles(symbol, config.ORB_TIMEFRAME, start_ts, end_ts)
        expected_candles = max(1, config.ORB_RANGE_MINUTES // (config.RESOLUTION_SECONDS[config.ORB_TIMEFRAME] // 60))
        if len(candles) < max(2, expected_candles - 1):
            return  # not enough data yet (exchange lag) — try again next run
        range_high = max(c["high"] for c in candles)
        range_low = min(c["low"] for c in candles)
        entry.update({"formed": True, "high": range_high, "low": range_low})
        st.set_orb_entry(state, symbol, session, date_str, entry)
        range_end_ist = range_end.astimezone(IST)
        caption = (
            f"📐 *{symbol}* — {session.title()} session ORB range formed\n"
            f"High: `{range_high:.4f}`  Low: `{range_low:.4f}`\n"
            f"_{range_end_ist.strftime('%Y-%m-%d %H:%M IST')}_"
        )
        _send_with_chart(symbol, session, range_high, range_low, caption, end_ts)
        return

    # Range already formed for today — check the latest closed candle for a breakout,
    # unless both directions have already fired (nothing left to check today).
    if entry["up_alerted"] and entry["down_alerted"]:
        return

    now_ts = int(now_dt.timestamp())
    resolution_seconds = config.RESOLUTION_SECONDS[config.ORB_TIMEFRAME]
    lookback_start = now_ts - resolution_seconds * 5
    candles = delta_client.get_candles(symbol, config.ORB_TIMEFRAME, lookback_start, now_ts)
    if len(candles) < 2:
        return
    last_closed = sorted(candles, key=lambda c: c["time"])[-2]
    close = last_closed["close"]

    if not entry["up_alerted"] and close > entry["high"]:
        entry["up_alerted"] = True
        st.set_orb_entry(state, symbol, session, date_str, entry)
        pct = (close - entry["high"]) / entry["high"] * 100
        caption = (
            f"🟢⬆️ *{symbol}* — {session.title()} ORB breakout UP\n"
            f"Close `{close:.4f}` broke above range high `{entry['high']:.4f}` ({pct:+.2f}%)"
        )
        _send_with_chart(symbol, session, entry["high"], entry["low"], caption, now_ts, direction="up")
    elif not entry["down_alerted"] and close < entry["low"]:
        entry["down_alerted"] = True
        st.set_orb_entry(state, symbol, session, date_str, entry)
        pct = (close - entry["low"]) / entry["low"] * 100
        caption = (
            f"🔴⬇️ *{symbol}* — {session.title()} ORB breakout DOWN\n"
            f"Close `{close:.4f}` broke below range low `{entry['low']:.4f}` ({pct:+.2f}%)"
        )
        _send_with_chart(symbol, session, entry["high"], entry["low"], caption, now_ts, direction="down")
    else:
        st.set_orb_entry(state, symbol, session, date_str, entry)


def run_orb_scan(symbols: list, state: dict):
    now_dt = datetime.now(timezone.utc)
    st.prune_old_orb_ranges(state)
    for symbol in symbols:
        for session, start_hhmm in SESSIONS.items():
            try:
                _check_symbol(symbol, session, start_hhmm, state, now_dt)
            except Exception as e:  # noqa: BLE001
                log.error("ORB error for %s (%s): %s", symbol, session, e)
        time.sleep(0.15)
