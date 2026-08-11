import logging

import pandas as pd

try:
    import pandas_ta as ta

    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False

log = logging.getLogger(__name__)


def _sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, 1e-9)
    return 100 - (100 / (1 + rs))


def _pattern(df: pd.DataFrame, name: str) -> pd.Series:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    out = pd.Series(0.0, index=df.index)
    body = (c - o).abs()
    rng = h - l
    rng_safe = rng.replace(0.0, 1e-9)
    if name == "doji":
        out[(body / rng_safe) < 0.1] = 100.0
    elif name == "marubozu":
        upper = h - pd.concat([c, o], axis=1).max(axis=1)
        lower = pd.concat([o, l], axis=1).min(axis=1) - l
        upper_w = upper / rng_safe
        lower_w = lower / rng_safe
        bullish = (c > o) & (upper_w < 0.05) & (lower_w < 0.05)
        bearish = (c < o) & (upper_w < 0.05) & (lower_w < 0.05)
        out[bullish] = 100.0
        out[bearish] = -100.0
    elif name == "engulfing":
        prev_o = o.shift(1)
        prev_c = c.shift(1)
        bull = (c > o) & (prev_c < prev_o) & (c >= prev_o) & (o <= prev_c)
        bear = (c < o) & (prev_c > prev_o) & (c <= prev_o) & (o >= prev_c)
        out[bull] = 100.0
        out[bear] = -100.0
    return out


def _apply_pandas_ta(df: pd.DataFrame) -> pd.DataFrame:
    df["SMA_20"] = ta.sma(df["close"], length=20)
    df["SMA_50"] = ta.sma(df["close"], length=50)
    df["RSI_14"] = ta.rsi(df["close"], length=14)
    df["CDL_DOJI"] = ta.cdl_pattern(df["open"], df["high"], df["low"], df["close"], name="doji")
    df["CDL_MARUBOZU"] = ta.cdl_pattern(
        df["open"], df["high"], df["low"], df["close"], name="marubozu"
    )
    df["CDL_ENGULFING"] = ta.cdl_pattern(
        df["open"], df["high"], df["low"], df["close"], name="engulfing"
    )
    return df


def _apply_fallback(df: pd.DataFrame) -> pd.DataFrame:
    df["SMA_20"] = _sma(df["close"], 20)
    df["SMA_50"] = _sma(df["close"], 50)
    df["RSI_14"] = _rsi(df["close"], 14)
    df["CDL_DOJI"] = _pattern(df, "doji")
    df["CDL_MARUBOZU"] = _pattern(df, "marubozu")
    df["CDL_ENGULFING"] = _pattern(df, "engulfing")
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 50:
        log.warning("Not enough rows for indicators (%d)", len(df))
        return df
    df = df.copy()
    if HAS_PANDAS_TA:
        try:
            return _apply_pandas_ta(df)
        except Exception as exc:
            log.warning("pandas-ta failed, using fallback: %s", exc)
    return _apply_fallback(df)


def pattern_label(value) -> str:
    if value is None or pd.isna(value) or value == 0:
        return "None"
    if value > 0:
        return "Bullish"
    if value < 0:
        return "Bearish"
    return "None"


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    df = add_indicators(df)
    last = df.iloc[-1]
    close_5 = df["close"].iloc[-6] if len(df) > 5 else df["close"].iloc[0]
    return {
        "date": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1]),
        "open": float(last["open"]),
        "high": float(last["high"]),
        "low": float(last["low"]),
        "close": float(last["close"]),
        "volume": float(last["volume"]),
        "sma_20": round(float(last["SMA_20"]), 2) if pd.notna(last["SMA_20"]) else None,
        "sma_50": round(float(last["SMA_50"]), 2) if pd.notna(last["SMA_50"]) else None,
        "rsi_14": round(float(last["RSI_14"]), 2) if pd.notna(last["RSI_14"]) else None,
        "close_vs_sma20": float(last["close"]) - float(last["SMA_20"])
        if pd.notna(last["SMA_20"])
        else None,
        "doji": pattern_label(last["CDL_DOJI"]),
        "marubozu": pattern_label(last["CDL_MARUBOZU"]),
        "engulfing": pattern_label(last["CDL_ENGULFING"]),
        "5d_momentum_pct": round(
            (float(last["close"]) - float(close_5)) / float(close_5) * 100, 2
        )
        if float(close_5) > 0
        else None,
        "trend": (
            "Uptrend"
            if float(last["SMA_20"]) > float(last["SMA_50"])
            else "Downtrend"
            if float(last["SMA_20"]) < float(last["SMA_50"])
            else "Neutral"
        ),
    }
