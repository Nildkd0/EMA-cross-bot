"""
import json
import os

STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")

_DEFAULT_STATE = {
    "last_seen_candle": {},       # "symbol|timeframe" -> epoch seconds of last processed closed candle
    "last_alert_candle_idx": {},  # "symbol|timeframe|ema|direction" -> candle index (epoch // resolution)
    "last_summary_sent": 0,       # epoch seconds
}


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return json.loads(json.dumps(_DEFAULT_STATE))  # deep copy
    try:
        with open(STATE_PATH, "r") as f:
            data = json.load(f)
        for key, default in _DEFAULT_STATE.items():
            data.setdefault(key, default)
        return data
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(_DEFAULT_STATE))


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def is_new_candle(state: dict, symbol: str, timeframe: str, candle_epoch: int) -> bool:
    key = f"{symbol}|{timeframe}"
    return state["last_seen_candle"].get(key) != candle_epoch


def mark_candle_seen(state: dict, symbol: str, timeframe: str, candle_epoch: int):
    key = f"{symbol}|{timeframe}"
    state["last_seen_candle"][key] = candle_epoch


def should_fire(state: dict, symbol: str, timeframe: str, ema_label: str, direction: str,
                 candle_idx: int, block_candles: int) -> bool:
    key = f"{symbol}|{timeframe}|{ema_label}|{direction}"
    last_idx = state["last_alert_candle_idx"].get(key)
    if last_idx is None or (candle_idx - last_idx) >= block_candles:
        state["last_alert_candle_idx"][key] = candle_idx
        return True
    return False
