"""
Central configuration, loaded from environment variables / .env file.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_list(name: str, default: str) -> list:
    raw = os.getenv(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def _get_float(name: str, default: str) -> float:
    # Treat a missing OR empty-string env var (e.g. a secret that exists but
    # was never given a value) the same way — both fall back to default.
    val = os.getenv(name)
    if val is None or val.strip() == "":
        val = default
    return float(val)


def _get_int(name: str, default: str) -> int:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        val = default
    return int(val)


class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # Delta Exchange
    DELTA_BASE_URL = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")
    CONTRACT_TYPES = _get_list("CONTRACT_TYPES", "perpetual_futures,futures")

    # Scanning
    MIN_24H_VOLUME = _get_float("MIN_24H_VOLUME", "1000000")
    TIMEFRAMES = _get_list("TIMEFRAMES", "15m,1h")
    EMA_PERIOD = _get_int("EMA_PERIOD", "20")
    ANTI_SPAM_CANDLES = _get_int("ANTI_SPAM_CANDLES", "3")
    CANDLE_LOOKBACK = _get_int("CANDLE_LOOKBACK", "50")
    SUMMARY_INTERVAL_MINUTES = _get_int("SUMMARY_INTERVAL_MINUTES", "60")

    # Opening Range Breakout (ORB) strategy — session start times in IST
    # (Asia/Kolkata, fixed UTC+5:30 year-round, India has no daylight saving),
    # given as "HH:MM". Defaults are the IST equivalents of each session's
    # standard open: Asia/Tokyo 00:00 UTC, London 08:00 UTC, New York 13:00 UTC.
    ORB_ASIA_START_IST = os.getenv("ORB_ASIA_START_IST", "05:30")
    ORB_LONDON_START_IST = os.getenv("ORB_LONDON_START_IST", "13:30")
    ORB_NEWYORK_START_IST = os.getenv("ORB_NEWYORK_START_IST", "18:30")
    ORB_RANGE_MINUTES = _get_int("ORB_RANGE_MINUTES", "30")
    ORB_TIMEFRAME = os.getenv("ORB_TIMEFRAME", "15m")

    # Range strategy — separate from ORB, not tied to any session. Flags a
    # symbol+timeframe as "ranging" whenever its last RANGE_LOOKBACK_CANDLES
    # closed candles all fit within a band RANGE_WIDTH_PCT wide (measured as
    # (high-low)/low). EMA cross alerts go quiet for that symbol+timeframe
    # while it's ranging; a close outside the established band fires a
    # breakout alert and EMA alerts resume as normal afterward.
    RANGE_LOOKBACK_CANDLES = _get_int("RANGE_LOOKBACK_CANDLES", "12")
    RANGE_WIDTH_PCT = _get_float("RANGE_WIDTH_PCT", "1.5")

    # Symbols containing any of these keywords (case-insensitive substring match,
    # e.g. "PEPE" matches "1000PEPEUSD") are skipped entirely during scanning.
    # This is a curated, editable list — not a definitive/exhaustive registry of
    # every meme coin on the exchange. Add/remove via the EXCLUDE_KEYWORDS env var.
    EXCLUDE_KEYWORDS = _get_list(
        "EXCLUDE_KEYWORDS",
        "DOGE,SHIB,PEPE,BONK,WIF,FLOKI,BABYDOGE,PENGU,FARTCOIN,"
        "TURBO,WOJAK,MOG,BRETT,POPCAT,NEIRO,CHEEMS,BOME,MYRO,SLERF,MEME,SPX",
    )

    # Resolution string -> seconds, used for scheduling + candle math.
    RESOLUTION_SECONDS = {
        "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
        "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
        "1d": 86400, "1w": 604800,
    }

    def validate(self):
        missing = []
        if not self.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.TELEGRAM_CHAT_ID:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}. "
                f"Copy .env.example to .env and fill them in."
            )
        for tf in self.TIMEFRAMES:
            if tf not in self.RESOLUTION_SECONDS:
                raise RuntimeError(
                    f"Unsupported timeframe '{tf}'. Supported: {list(self.RESOLUTION_SECONDS)}"
                )
        if self.ORB_TIMEFRAME not in self.RESOLUTION_SECONDS:
            raise RuntimeError(
                f"Unsupported ORB_TIMEFRAME '{self.ORB_TIMEFRAME}'. Supported: {list(self.RESOLUTION_SECONDS)}"
            )


config = Config()
