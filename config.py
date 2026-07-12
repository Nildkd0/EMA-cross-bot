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


class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # Delta Exchange
    DELTA_BASE_URL = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")
    CONTRACT_TYPES = _get_list("CONTRACT_TYPES", "perpetual_futures,futures")

    # Scanning
    MIN_24H_VOLUME = float(os.getenv("MIN_24H_VOLUME", "1000000"))
    TIMEFRAMES = _get_list("TIMEFRAMES", "5m,15m,1h")
    EMA_FAST = int(os.getenv("EMA_FAST", "8"))
    EMA_SLOW = int(os.getenv("EMA_SLOW", "20"))
    ANTI_SPAM_CANDLES = int(os.getenv("ANTI_SPAM_CANDLES", "3"))
    CANDLE_LOOKBACK = int(os.getenv("CANDLE_LOOKBACK", "50"))
    SUMMARY_INTERVAL_MINUTES = int(os.getenv("SUMMARY_INTERVAL_MINUTES", "60"))

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


config = Config()
