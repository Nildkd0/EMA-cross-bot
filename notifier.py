"""
Minimal Telegram Bot API client — sends text messages and photos.
No external telegram library needed, just plain requests calls.
"""
import logging

import requests

from config import config

log = logging.getLogger("notifier")

_API_BASE = "https://api.telegram.org"


def _url(method: str) -> str:
    return f"{_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


def send_message(text: str, parse_mode: str = "Markdown"):
    try:
        resp = requests.post(
            _url("sendMessage"),
            data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=15,
        )
        if not resp.ok:
            log.error("Telegram sendMessage failed: %s", resp.text)
    except Exception as e:  # noqa: BLE001
        log.error("Telegram sendMessage exception: %s", e)


def send_photo(photo_path: str, caption: str = "", parse_mode: str = "Markdown"):
    try:
        with open(photo_path, "rb") as f:
            resp = requests.post(
                _url("sendPhoto"),
                data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": parse_mode},
                files={"photo": f},
                timeout=30,
            )
        if not resp.ok:
            log.error("Telegram sendPhoto failed: %s", resp.text)
    except Exception as e:  # noqa: BLE001
        log.error("Telegram sendPhoto exception: %s", e)
