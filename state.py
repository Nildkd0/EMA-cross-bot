"""
In-memory anti-spam state: blocks a repeat of the *same* signal
(symbol + timeframe + direction) until N candles have elapsed since it
last fired.
"""
import threading


class SignalState:
    def __init__(self, block_candles: int):
        self.block_candles = block_candles
        self._last_fired_candle_idx = {}  # key -> candle index (monotonic counter)
        self._lock = threading.Lock()

    @staticmethod
    def _key(symbol: str, timeframe: str, direction: str) -> str:
        return f"{symbol}|{timeframe}|{direction}"

    def should_fire(self, symbol: str, timeframe: str, direction: str, candle_idx: int) -> bool:
        key = self._key(symbol, timeframe, direction)
        with self._lock:
            last_idx = self._last_fired_candle_idx.get(key)
            if last_idx is None or (candle_idx - last_idx) >= self.block_candles:
                self._last_fired_candle_idx[key] = candle_idx
                return True
            return False
