"""
Delta Exchange India - EMA Cross Alert Bot (GitHub Actions edition)
=====================================================================
Unlike main.py (which runs forever as one long process — meant for a VPS),
this script runs ONE pass and exits. GitHub Actions calls it every 5 minutes
via a scheduled workflow. State (which candles/signals were already
processed) is persisted to state.json, which the workflow commits back to
the repo after every run.

Because runs are only every 5 minutes but timeframes include 15m/1h, this
script checks EVERY configured timeframe on every run and simply skips a
symbol+timeframe if its last closed candle hasn't changed since the last
run — so a 1h timeframe naturally only produces a fresh check once an hour,
whenever the run happens to land after that candle closes.
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

    # Skip if we've already processed this exact closed candle in a previous run
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
        lines.append(f"{i:>2}. `{r['symbol']:<14}` {val_str}")
    return "\n".join(lines) if lines else "_no data_"


def maybe_send_summary(state: dict):
    now = int(time.time())
    interval = config.SUMMARY_INTERVAL_MINUTES * 60
    if now - state.get("last_summary_sent", 0) < interval:
        return

    ct_param = ",".join(config.CONTRACT_TYPES)
    tickers = delta_client.get_tickers(contract_types=ct_param)

    rows = []
    for t in tickers:
        try:
            rows.append({
                "symbol": t["symbol"],
                "change_24h": float(t.get("ltp_change_24h") or 0),
                "volume": float(t.get("volume") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue

    rows = [r for r in rows if r["volume"] >= config.MIN_24H_VOLUME]
    gainers = sorted(rows, key=lambda r: r["change_24h"], reverse=True)[:15]
    losers = sorted(rows, key=lambda r: r["change_24h"])[:15]
    by_volume = sorted(rows, key=lambda r: r["volume"], reverse=True)[:15]

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    message = (
        f"📊 *Market Summary* — {ts}\n\n"
        f"*Top Gainers (24h %)*\n{format_summary_table(gainers, 'change_24h', is_percent=True)}\n\n"
        f"*Top Losers (24h %)*\n{format_summary_table(losers, 'change_24h', is_percent=True)}\n\n"
        f"*Top Volume*\n{format_summary_table(by_volume, 'volume')}"
    )
    notifier.send_message(message)
    state["last_summary_sent"] = now
    log.info("Market summary sent (%d instruments considered)", len(rows))


def main():
    config.validate()
    log.info(
        "Active config: MIN_24H_VOLUME=%s EMA_FAST=%s EMA_SLOW=%s ANTI_SPAM_CANDLES=%s "
        "TIMEFRAMES=%s SUMMARY_INTERVAL_MINUTES=%s CANDLE_LOOKBACK=%s CONTRACT_TYPES=%s "
        "EXCLUDE_KEYWORDS=%s",
        config.MIN_24H_VOLUME, config.EMA_FAST, config.EMA_SLOW, config.ANTI_SPAM_CANDLES,
        config.TIMEFRAMES, config.SUMMARY_INTERVAL_MINUTES, config.CANDLE_LOOKBACK, config.CONTRACT_TYPES,
        config.EXCLUDE_KEYWORDS,
    )
    state = st.load_state()

    for timeframe in config.TIMEFRAMES:
        resolution_seconds = config.RESOLUTION_SECONDS[timeframe]
        try:
            symbols = delta_client.get_scannable_symbols(
                min_volume=config.MIN_24H_VOLUME, contract_types=config.CONTRACT_TYPES,
                exclude_keywords=config.EXCLUDE_KEYWORDS,
            )
        except Exception as e:  # noqa: BLE001
            log.error("Failed to fetch scannable symbols: %s", e)
            continue

        log.info("[%s] Scanning %d symbols", timeframe, len(symbols))
        for symbol in symbols:
            try:
                scan_symbol(symbol, timeframe, resolution_seconds, state)
            except Exception as e:  # noqa: BLE001
                log.error("Error scanning %s on %s: %s", symbol, timeframe, e)
            time.sleep(REQUEST_SPACING_SECONDS)

    try:
        maybe_send_summary(state)
    except Exception as e:  # noqa: BLE001
        log.error("Summary error: %s", e)

    st.save_state(state)
    log.info("Run complete.")


if __name__ == "__main__":
    main()
