# Swing Trading Bot — Progress Tracker

## Project Overview

Human-in-the-Loop Swing Trading Bot for the Indian Stock Market (NSE). Containerized
Streamlit WebUI deployed via Docker Compose on TrueNAS Scale, accessed via Tailscale.

- Background scheduler (APScheduler) runs weekdays at 3:45 PM IST.
- EOD data via **Upstox API** (source of truth).
- Technical indicators computed locally (pandas-ta).
- News headlines via Google News RSS.
- Gemini 2.5 Pro returns BUY/SELL/HOLD recommendations (Pydantic schema).
- Results saved to SQLite; user reviews in Streamlit UI and places AMO manually on Upstox.

## Decisions & Design (from discussions)

### Budget model
- Daily available funds default to **0**.
- User sets the day's budget in the UI (no persistent wallet / no Add-Funds flow).
- Changing the budget triggers a **Phase 2 (BUY) recompute** using the new budget.
- Recommendations are **never discarded** — affordability is a computed annotation, not a filter on whether a recommendation exists.

### Two-phase analysis
1. **Phase 1 — Portfolio (SELL/HOLD):** Gemini does technical + fundamental analysis on all stocks currently held. Never skipped.
2. **Phase 2 — New BUYs:** universe (NIFTY 500) → filter by `LTP <= daily budget` → technicals on affordable subset only → top-N pre-screen → Gemini for fundamentals + final BUY decision.

### Data source
- **Upstox API** for all market data (prices, historical candles, fundamentals).
- Universe = NIFTY 500 constituents (cached).
- Budget filter runs on batch quotes BEFORE fetching full history (keeps API calls low).

### Technicals (per stock)
- Indicators: SMA_20, SMA_50, RSI_14 (+ crossover/trend state, 5-day momentum).
- Candlesticks (pandas-ta): CDL_DOJI, CDL_MARUBOZU, CDL_ENGULFING (100 / -100 / 0 -> readable labels).
- Input dictionary to Gemini = latest bar indicators + pattern labels + 60-day trend context.

### Interaction model
- Gemini: automated analysis + interactive "Ask Gemini" chat (free-form questions with today's data as context).
- User (HITL): final decision-maker; reviews recommendations, places AMO manually on Upstox, marks Executed/Rejected.
- SELL = exit existing long position (no futures/shorting for now).

### Portfolio handling
- Held stocks can also appear as BUY candidates (add to position).
- Watchlist = seeded 15 NSE majors + user-managed add/remove in UI.

## Flags / concerns (resolved or pending)

| # | Concern | Status |
|---|---------|--------|
| 1 | Universe source + rate limits | Resolved -> Upstox batch quotes (50/call), NIFTY 500 |
| 2 | Budget filter lot-aware + buffer | Deferred |
| 3 | Cap on affordable set (top-N pre-screen) | Deferred (add later) |
| 4 | Fundamental data source | To verify in Phase 0 |
| 5 | Add-Funds triggers heavy re-run | Resolved -> no Add-Funds flow; budget change recomputes Phase 2 |
| 6 | Daily budget = 0 -> Phase 2 empty | Resolved -> Phase 1 runs regardless; UI shows "set budget to unlock BUYs" |
| 7 | Held stock as BUY candidate | Resolved -> yes, allowed |
| 8 | Gap risk on budget filter | Prompt handles gap-up/down risk |

## Implementation Plan

### Phase 0 — Verify Upstox API (next step)
- [ ] OAuth token flow on headless server (generate token locally -> paste into `.env`).
- [ ] Refresh-token auto-renewal (access token expires in 1 day).
- [ ] Fundamentals endpoint availability (`/v2/fundamentals/{instrument_key}`); fallback = Screener.in.
- [ ] Rate limits + batch-quote limits (~50 instruments/call).
- [ ] Instrument master download + ticker -> instrument key mapping.

### Phase 1 — Upstox client (`upstox_client.py`)
- [ ] Auth from `.env` (`UPSTOX_API_KEY`, `UPSTOX_ACCESS_TOKEN`/refresh), auto-refresh + retry.
- [ ] Instrument master download + cache + `RELIANCE.NS` -> `NSE_EQ|INE002A01018` mapping.
- [ ] `batch_quotes(instruments)` — live LTP, batches of 50.
- [ ] `historical_candles(instrument, days=60)`.
- [ ] Remove `yfinance`, add `requests` to requirements.

### Phase 2 — Universe + budget filter
- [ ] NIFTY 500 constituents (cached).
- [ ] Batch LTP over universe -> filter by `LTP <= daily budget`.
- [ ] Fetch 60-day history only for affordable subset.

### Phase 3 — Analysis pipeline
- [ ] Rewrite `data_fetcher.py` onto Upstox client.
- [ ] `technicals.py` (SMA/RSI/candlesticks).
- [ ] Phase 1 (portfolio): technicals + fundamentals -> Gemini -> SELL/HOLD.
- [ ] Phase 2 (BUY): technicals on affordable subset -> top-N pre-screen -> Gemini -> BUY list.
- [ ] Daily budget setting in UI; change triggers Phase 2 recompute.

### Phase 4 — UI (`app.py`)
- [ ] Portfolio tab.
- [ ] Watchlist tab.
- [ ] BUY recommendations w/ affordability badges.
- [ ] Budget input + recompute trigger.
- [ ] History & performance tab.
- [ ] Ask Gemini chat tab.

### Phase 5 — Scheduler, Docker, deployment
- [ ] `scheduler.py` (weekdays 15:45 IST).
- [ ] Dockerfile, docker-compose.yml, deploy.sh, hooks.json.
- [ ] DEPLOYMENT_GUIDE.md (TrueNAS Scale 25.04 + Tailscale).

## Files to create (from original spec)
- app.py, scheduler.py, data_fetcher.py, technicals.py, news_scraper.py, agent.py,
  database.py, config.py, upstox_client.py (new), requirements.txt, Dockerfile,
  docker-compose.yml, deploy.sh, hooks.json, .env.example, .gitignore,
  DEPLOYMENT_GUIDE.md

## Changelog
- 2026-08-11: Initial plan recorded; decisions on budget model, two-phase analysis, Upstox data source.
