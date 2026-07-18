"""
Delta Exchange India - EMA Cross Alert Bot
============================================
Runs continuously. For each configured timeframe (15m/1h by default) it:
  1. Waits until the candle for that timeframe has just closed.
  2. Refreshes the list of live perpetual/dated futures with 24h volume
     >= MIN_24H_VOLUME (excluding meme coins).
  3. Fetches recent candles for each symbol, computes a single EMA
     (EMA_PERIOD, default 20), and checks whether price crossed it on the
     candle that just closed.
  4. Sends a Telegram alert (chart + caption) for any new signal, respecting
     the anti-spam window (same signal blocked for N candles) — suppressed
     during any session's opening-range-forming window (see orb.py).

A separate thread runs the Opening Range Breakout (ORB) strategy for the
Asia/London/New York sessions, and another sends a market summary (top
gainers / losers / volume) every SUMMARY_INTERVAL_MINUTES.

Design note: this uses REST polling aligned to candle-close boundaries rather
than a raw WebSocket tick-by-tick candle builder. It's less "real-time exotic"
but far more robust for unattended 24/7 operation — a dropped connection just
means the next poll retries, no reconnect/resync logic to get subtly wrong.
"""
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone

import pandas as pd

from config import config
import delta_client
import indicators
import chart as chart_mod
import notifier
import orb
import range as range_strategy
from state import SignalState
from tzhelper import to_ist_str

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "logs", "bot.log")),
    ],
)
log = logging.getLogger("main")

signal_state = SignalState(block_candles=config.ANTI_SPAM_CANDLES)
# ORB and range-strategy state, in-memory only (this process never restarts
# the way GitHub Actions runs do, so there's no need to persist this to
# disk — it just needs to survive for as long as the process is alive).
orb_state = {"orb_ranges": {}}
range_state = {"range_state": {}}

REQUEST_SPACING_SECONDS = 0.25  # small pause between per-symbol API calls


def sleep_until_next_boundary(resolution_seconds: int, buffer_seconds: int = 5):
    now = time.time()
    next_boundary = (int(now // resolution_seconds) + 1) * resolution_seconds
    wait = (next_boundary - now) + buffer_seconds
    if wait > 0:
        time.sleep(wait)


def scan_symbol(symbol: str, timeframe: str, resolution_seconds: int):
    now = int(time.time())
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    midnight_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    ema_lookback_start = now - resolution_seconds * (config.CANDLE_LOOKBACK + 5)
    start = min(ema_lookback_start, int(midnight_dt.timestamp()))

    candles = delta_client.get_candles(symbol, timeframe, start, now)
    min_rows = max(config.EMA_PERIOD, 30) + 3
    if len(candles) < min_rows:
        return  # not enough history yet for a reliable EMA

    df = indicators.candles_to_df(candles)
    ema_col = f"ema_{config.EMA_PERIOD}"
    indicators.add_ema(df, config.EMA_PERIOD, ema_col)
    indicators.add_volume_stats(df)
    indicators.add_daily_vwap_series(df, midnight_dt)

    # Range strategy runs every candle regardless of EMA — independent
    # scenario from ORB (see range.py) — and tells us whether to suppress
    # the EMA alert this candle.
    suppress_ema = range_strategy.check_range(symbol, timeframe, df, range_state)

    direction = indicators.detect_ema_cross(df, ema_col)
    if direction is None or suppress_ema:
        return

    last_closed = df.iloc[-2]
    candle_idx = int(last_closed.name.timestamp()) // resolution_seconds

    if not signal_state.should_fire(symbol, timeframe, direction, candle_idx):
        return

    arrow = "🟢⬆️" if direction == "up" else "🔴⬇️"
    vol_rel = last_closed.get("vol_rel", float("nan"))
    vol_tag = ""
    if pd.notna(vol_rel):
        if vol_rel >= 1.5:
            vol_tag = f" 🔥 {vol_rel:.1f}x avg"
        elif vol_rel <= 0.5:
            vol_tag = f" (quiet, {vol_rel:.1f}x avg)"
        else:
            vol_tag = f" ({vol_rel:.1f}x avg)"

    caption = (
        f"{arrow} *{symbol}* — {timeframe}\n"
        f"Price crossed *EMA{config.EMA_PERIOD}* {direction.upper()}\n"
        f"Close: `{last_closed['close']:.4f}`\n"
        f"Volume: `{last_closed['volume']:,.0f}`{vol_tag}\n"
        f"_{to_ist_str(last_closed.name.to_pydatetime())}_"
    )

    try:
        chart_path = chart_mod.render_signal_chart(
            df, symbol, timeframe, ema_col, config.EMA_PERIOD, direction,
            vwap_col="vwap",
        )
        notifier.send_photo(chart_path, caption=caption)
        os.remove(chart_path)
    except Exception as e:  # noqa: BLE001
        log.error("Chart/send failed for %s %s, falling back to text: %s", symbol, timeframe, e)
        notifier.send_message(caption)

    log.info("Signal fired: %s %s EMA%s %s", symbol, timeframe, config.EMA_PERIOD, direction)


def orb_worker():
    """Runs the ORB (Opening Range Breakout) scan on a fixed interval,
    independent of the EMA timeframe workers."""
    interval = config.RESOLUTION_SECONDS.get(config.ORB_TIMEFRAME, 300)
    log.info("Starting ORB worker (checking every %ss)", interval)
    while True:
        try:
            symbols = delta_client.get_scannable_symbols(
                min_volume=config.MIN_24H_VOLUME, contract_types=config.CONTRACT_TYPES,
                exclude_keywords=config.EXCLUDE_KEYWORDS,
            )
            orb.run_orb_scan(symbols, orb_state)
        except Exception as e:  # noqa: BLE001
            log.error("ORB worker error: %s — retrying next cycle", e)
        time.sleep(interval)


def timeframe_worker(timeframe: str):
    resolution_seconds = config.RESOLUTION_SECONDS[timeframe]
    log.info("Starting worker for timeframe %s (%ss)", timeframe, resolution_seconds)

    while True:
        try:
            sleep_until_next_boundary(resolution_seconds)

            symbols = delta_client.get_scannable_symbols(
                min_volume=config.MIN_24H_VOLUME, contract_types=config.CONTRACT_TYPES,
                exclude_keywords=config.EXCLUDE_KEYWORDS,
            )
            log.info("[%s] Scanning %d symbols", timeframe, len(symbols))

            for symbol in symbols:
                try:
                    scan_symbol(symbol, timeframe, resolution_seconds)
                except Exception as e:  # noqa: BLE001
                    log.error("Error scanning %s on %s: %s", symbol, timeframe, e)
                time.sleep(REQUEST_SPACING_SECONDS)

        except Exception as e:  # noqa: BLE001
            log.error("Worker error on %s: %s — retrying in 10s", timeframe, e)
            time.sleep(10)


def format_summary_table(rows, value_key, value_label, is_percent=False):
    lines = []
    for i, r in enumerate(rows, 1):
        val = r[value_key]
        val_str = f"{val:+.2f}%" if is_percent else f"{val:,.0f}"
        lines.append(f"{i:>2}. `{r['symbol']:<14}` {val_str}")
    return "\n".join(lines) if lines else "_no data_"


def summary_worker():
    interval = config.SUMMARY_INTERVAL_MINUTES * 60
    log.info("Starting market summary worker (every %s min)", config.SUMMARY_INTERVAL_MINUTES)

    while True:
        try:
            ct_param = ",".join(config.CONTRACT_TYPES)
            tickers = delta_client.get_tickers(contract_types=ct_param)

            rows = []
            for t in tickers:
                try:
                    turnover = t.get("turnover_usd")
                    if turnover is None:
                        turnover = t.get("volume")
                    rows.append({
                        "symbol": t["symbol"],
                        "change_24h": float(t.get("ltp_change_24h") or 0),
                        "volume": float(turnover or 0),
                    })
                except (KeyError, TypeError, ValueError):
                    continue

            rows = [r for r in rows if r["volume"] >= config.MIN_24H_VOLUME]
            gainers = sorted(rows, key=lambda r: r["change_24h"], reverse=True)[:15]
            losers = sorted(rows, key=lambda r: r["change_24h"])[:15]
            by_volume = sorted(rows, key=lambda r: r["volume"], reverse=True)[:15]

            ts = to_ist_str(datetime.now(timezone.utc))
            message = (
                f"📊 *Market Summary* — {ts}\n\n"
                f"*Top Gainers (24h %)*\n{format_summary_table(gainers, 'change_24h', 'change', is_percent=True)}\n\n"
                f"*Top Losers (24h %)*\n{format_summary_table(losers, 'change_24h', 'change', is_percent=True)}\n\n"
                f"*Top Volume*\n{format_summary_table(by_volume, 'volume', 'volume')}"
            )
            notifier.send_message(message)
            log.info("Market summary sent (%d instruments considered)", len(rows))

        except Exception as e:  # noqa: BLE001
            log.error("Summary worker error: %s", e)

        time.sleep(interval)


def main():
    config.validate()
    log.info(
        "Delta EMA Cross Bot starting. Timeframes=%s EMA=%s MinVol=%s "
        "ORB_SESSIONS(IST)=%s RANGE_LOOKBACK_CANDLES=%s RANGE_WIDTH_PCT=%s",
        config.TIMEFRAMES, config.EMA_PERIOD, config.MIN_24H_VOLUME, orb.SESSIONS,
        config.RANGE_LOOKBACK_CANDLES, config.RANGE_WIDTH_PCT,
    )

    notifier.send_message(
        f"🤖 EMA Cross Bot online.\nTimeframes: {', '.join(config.TIMEFRAMES)}\n"
        f"EMA{config.EMA_PERIOD} cross alerts, min 24h volume {config.MIN_24H_VOLUME:,.0f}.\n"
        f"ORB strategy active for Asia/London/New York sessions (IST).\n"
        f"Range strategy active — EMA alerts pause per-symbol while ranging."
    )

    threads = []
    for tf in config.TIMEFRAMES:
        t = threading.Thread(target=timeframe_worker, args=(tf,), daemon=True, name=f"tf-{tf}")
        t.start()
        threads.append(t)

    orb_thread = threading.Thread(target=orb_worker, daemon=True, name="orb")
    orb_thread.start()
    threads.append(orb_thread)

    summary_thread = threading.Thread(target=summary_worker, daemon=True, name="summary")
    summary_thread.start()
    threads.append(summary_thread)

    try:
        while True:
            time.sleep(60)
            for t in threads:
                if not t.is_alive():
                    log.error("Thread %s died unexpectedly", t.name)
    except KeyboardInterrupt:
        log.info("Shutting down (KeyboardInterrupt).")


if __name__ == "__main__":
    main()
