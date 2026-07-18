"""
IST is a fixed UTC+5:30 offset — India doesn't observe daylight saving, so
this fixed offset is always correct and needs no external timezone database.
Exchange candle timestamps are all UTC internally; this is only used to
format times for display in alerts/logs.
"""
from datetime import timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def to_ist_str(dt_utc, fmt="%Y-%m-%d %H:%M IST"):
    return dt_utc.astimezone(IST).strftime(fmt)
