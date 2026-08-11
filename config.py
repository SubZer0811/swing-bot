import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "trading_bot.db"
INSTRUMENTS_CACHE = DATA_DIR / "nse_instruments.json.gz"
UNIVERSE_CACHE = DATA_DIR / "nifty500.json"

UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY", "")
UPSTOX_ANALYTICS_TOKEN = os.getenv("UPSTOX_ANALYTICS_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

GEMINI_MODEL = "gemini-2.5-pro"

INSTRUMENT_MASTER_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
)
HISTORICAL_DAYS = 60

WATCHLIST = [
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "TATAMOTORS",
    "INFY",
    "ICICIBANK",
    "BHARTIARTL",
    "SBIN",
    "LT",
    "ITC",
    "HINDUNILVR",
    "AXISBANK",
    "MARUTI",
    "SUNPHARMA",
    "HCLTECH",
]
