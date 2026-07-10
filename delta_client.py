"""
Thin wrapper around Delta Exchange India's public (no-auth-needed) REST
market-data endpoints:
  - GET /v2/products
  - GET /v2/tickers
  - GET /v2/history/candles

Docs: https://docs.delta.exchange/
"""
import logging
import time

import requests

from config import config

log = logging.getLogger("delta_client")

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})


def _get(path: str, params: dict = None, retries: int = 3, timeout: int = 10):
    url = f"{config.DELTA_BASE_URL}{path}"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = _session.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                wait = 2 * attempt
                log.warning("Rate limited on %s, backing off %ss", path, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success", False):
                raise RuntimeError(f"API returned success=false: {data}")
            return data.get("result")
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("Request to %s failed (attempt %s/%s): %s", path, attempt, retries, e)
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"Failed to GET {path} after {retries} attempts: {last_err}")


def get_products(contract_types: str = None) -> list:
    """List tradable products, optionally filtered by contract type(s)."""
    params = {"states": "live"}
    if contract_types:
        params["contract_types"] = contract_types
    return _get("/v2/products", params=params)


def get_tickers(contract_types: str = None) -> list:
    """Real-time ticker snapshot for all products (price, volume, 24h change)."""
    params = {}
    if contract_types:
        params["contract_types"] = contract_types
    return _get("/v2/tickers", params=params)


def get_candles(symbol: str, resolution: str, start: int, end: int) -> list:
    """
    Historical OHLC candles.
    Returns a list of dicts: {time, open, high, low, close, volume} sorted ascending by time.
    """
    params = {"symbol": symbol, "resolution": resolution, "start": start, "end": end}
    result = _get("/v2/history/candles", params=params)
    if not result:
        return []
    candles = sorted(result, key=lambda c: c["time"])
    return candles


def get_scannable_symbols(min_volume: float, contract_types: list) -> list:
    """
    Return symbols that are perpetual/dated futures, live, and whose 24h
    contract volume meets the configured minimum.
    """
    ct_param = ",".join(contract_types)
    products = get_products(contract_types=ct_param)
    products_by_symbol = {p["symbol"]: p for p in products if p.get("symbol")}

    tickers = get_tickers(contract_types=ct_param)
    scannable = []
    for t in tickers:
        symbol = t.get("symbol")
        if symbol not in products_by_symbol:
            continue
        volume = float(t.get("volume") or 0)
        if volume >= min_volume:
            scannable.append(symbol)
    return sorted(set(scannable))
