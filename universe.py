import csv
import io
import json
import logging
import time
from pathlib import Path

import requests

import config

log = logging.getLogger(__name__)

NIFTY500_CSV_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
UNIVERSE_CACHE = Path(config.UNIVERSE_CACHE)


def get_nifty500(cache_days: int = 7) -> list:
    cache = Path(UNIVERSE_CACHE)
    if cache.exists() and (time.time() - cache.stat().st_mtime) < cache_days * 86400:
        log.info("Using cached NIFTY 500 universe")
        with open(cache, "r", encoding="utf-8") as fh:
            return json.load(fh)
    log.info("Downloading NIFTY 500 constituents...")
    symbols = _fetch_nifty500()
    cache.parent.mkdir(exist_ok=True)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(symbols, fh)
    return symbols


def _fetch_nifty500() -> list:
    resp = requests.get(NIFTY500_CSV_URL, timeout=60)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    symbols = [row["Symbol"].strip() for row in reader if row.get("Symbol")]
    if not symbols:
        raise ValueError("NIFTY 500 CSV returned no symbols")
    return symbols


def filter_by_budget(client, symbols: list, budget: float) -> list:
    if budget <= 0:
        return []
    keys = []
    valid = []
    for sym in symbols:
        try:
            keys.append(client.resolve_key(sym))
            valid.append(sym)
        except Exception:
            continue
    quotes = client.batch_ltp(keys)
    by_symbol = {resp_key.split(":")[-1].upper(): data for resp_key, data in quotes.items()}
    affordable = []
    for sym in valid:
        data = by_symbol.get(sym.upper()) or {}
        ltp = data.get("last_price")
        if ltp and ltp <= budget:
            affordable.append({"symbol": sym, "ltp": ltp})
    affordable.sort(key=lambda x: x["ltp"])
    return affordable
