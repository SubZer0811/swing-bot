import gzip
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import requests

import config

log = logging.getLogger(__name__)

BASE_URL = "https://api.upstox.com/v2"
QUOTE_URL = "https://api.upstox.com/v3"
MAX_KEYS_PER_QUOTE = 500


class UpstoxError(Exception):
    pass


class UpstoxClient:
    def __init__(self, token: str = None):
        self.token = token or config.UPSTOX_ANALYTICS_TOKEN
        if not self.token:
            raise UpstoxError("UPSTOX_ANALYTICS_TOKEN is not set")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
            }
        )
        self._instruments = {}
        self._isin_to_key = {}

    def _get(self, url: str) -> dict:
        for attempt in range(4):
            try:
                resp = self.session.get(url)
                if resp.status_code == 401:
                    raise UpstoxError(
                        "Unauthorized: UPSTOX_ANALYTICS_TOKEN is invalid or expired"
                    )
                if resp.status_code == 429:
                    delay = 10 * (attempt + 1)
                    log.warning("Rate limited by Upstox, backing off %ss", delay)
                    time.sleep(delay)
                    if attempt == 3:
                        raise UpstoxError(f"Rate limited (429) fetching {url}")
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException:
                if attempt == 3:
                    raise UpstoxError(f"Failed to fetch {url}: {resp.status_code}")
                time.sleep(2)
            time.sleep(0.15)
        raise UpstoxError(f"Failed to fetch {url}")

    def load_instrument_master(self, force: bool = False) -> None:
        cache = Path(config.INSTRUMENTS_CACHE)
        fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < 86400
        if not force and fresh:
            with gzip.open(cache, "rt", encoding="utf-8") as fh:
                instruments = json.load(fh)
        else:
            log.info("Downloading NSE instrument master...")
            resp = requests.get(config.INSTRUMENT_MASTER_URL, timeout=120)
            resp.raise_for_status()
            instruments = json.loads(gzip.decompress(resp.content))
            with gzip.open(cache, "wt", encoding="utf-8") as fh:
                json.dump(instruments, fh)
        for entry in instruments:
            if entry.get("segment") != "NSE_EQ" or entry.get("instrument_type") != "EQ":
                continue
            symbol = entry.get("trading_symbol")
            key = entry.get("instrument_key")
            isin = entry.get("isin")
            if symbol and key:
                self._instruments[symbol.upper()] = entry
            if isin and key:
                self._isin_to_key[isin] = key

    def resolve_key(self, symbol: str) -> str:
        entry = self._instruments.get(symbol.upper())
        if entry:
            return entry["instrument_key"]
        for sym, data in self._instruments.items():
            if symbol.upper() in (sym, sym + "-EQ"):
                return data["instrument_key"]
        raise UpstoxError(f"Instrument key not found for symbol: {symbol}")

    def isin_for(self, symbol: str) -> str:
        entry = self._instruments.get(symbol.upper())
        if not entry:
            raise UpstoxError(f"ISIN not found for symbol: {symbol}")
        return entry["isin"]

    def symbols(self) -> list:
        return sorted(self._instruments.keys())

    def batch_ltp(self, keys: list) -> dict:
        out = {}
        for i in range(0, len(keys), MAX_KEYS_PER_QUOTE):
            chunk = keys[i : i + MAX_KEYS_PER_QUOTE]
            url = f"{QUOTE_URL}/market-quote/ltp?instrument_key=" + ",".join(chunk)
            data = self._get(url).get("data", {})
            out.update(data)
        return out

    def ltp_for_symbols(self, symbols: list) -> dict:
        keys = [self.resolve_key(s) for s in symbols]
        quotes = self.batch_ltp(keys)
        by_symbol = {}
        for resp_key, data in quotes.items():
            by_symbol[resp_key.split(":")[-1].upper()] = data.get("last_price")
        return {sym: by_symbol.get(sym.upper()) for sym in symbols}

    def historical_candles(
        self, key: str, days: int = config.HISTORICAL_DAYS, interval: str = "day"
    ) -> list:
        to_date = date.today()
        from_date = to_date - timedelta(days=days * 1.5)
        url = (
            f"{BASE_URL}/historical-candle/{key}/{interval}/{to_date.isoformat()}/"
            f"{from_date.isoformat()}"
        )
        return self._get(url).get("data", {}).get("candles", [])

    def fundamentals(self, isin: str, endpoint: str) -> dict:
        url = f"{BASE_URL}/fundamentals/{isin}/{endpoint}"
        return self._get(url).get("data", {})

    def key_ratios(self, isin: str) -> dict:
        return self.fundamentals(isin, "key-ratios")

    def company_profile(self, isin: str) -> dict:
        return self.fundamentals(isin, "profile")

    def income_statement(self, isin: str) -> dict:
        return self.fundamentals(isin, "income-statement")
