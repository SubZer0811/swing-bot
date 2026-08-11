import logging
from datetime import datetime

import pandas as pd

from upstox_client import UpstoxClient, UpstoxError

log = logging.getLogger(__name__)


def candles_to_dataframe(candles: list) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume"]
        )
    df = pd.DataFrame(
        candles, columns=["date", "open", "high", "low", "close", "volume", "oi"]
    )
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.drop(columns=["oi"]).set_index("date")
    df = df.astype(float)
    df = df.sort_index()
    return df


def get_historical_data(client: UpstoxClient, symbol: str, days: int = 60) -> pd.DataFrame:
    key = client.resolve_key(symbol)
    candles = client.historical_candles(key, days=days, interval="day")
    df = candles_to_dataframe(candles)
    if df.empty:
        log.warning("No historical data for %s", symbol)
    return df


def get_portfolio_prices(client: UpstoxClient, portfolio: list) -> dict:
    symbols = [p["symbol"] for p in portfolio]
    if not symbols:
        return {}
    return client.ltp_for_symbols(symbols)
