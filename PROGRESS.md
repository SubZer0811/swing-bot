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

### Phase 0 — Verify Upstox API (DONE 2026-08-11)

**Verified findings:**

1. **Token flow (headless server):** Standard OAuth2 authorization code flow
   (`/v2/login/authorization/dialog` -> code -> `/v2/login/authorization/token`).
   No browser needed if we use the **Analytics Token**: generated once from
   Upstox Developer Apps -> Analytics tab, **read-only, valid 1 year**, and
   market-data APIs work **without a static IP**. This is the right choice — the
   bot only reads market data; orders are manual AMO on the Upstox app.
   (Account-specific APIs — User/Portfolio/Orders — require a static IP; out of
   scope since orders are manual.)
2. **Refresh tokens: NOT supported.** Standard access token expires at
   **3:30 AM IST** regardless of generation time; Upstox staff confirmed no
   refresh-token grant. Hence the Analytics token (1-year) eliminates the
   daily-regeneration problem entirely.
3. **Fundamentals API: EXISTS** (launched 2026-05-11). Full suite keyed by ISIN:
   Company Profile, Balance Sheet, Cash Flow, Income Statement, Share Holdings,
   **Key Ratios (P/E, P/B, ROA, ROE, ROCE, EV/EBITDA + sector benchmark)**,
   Corporate Actions, Competitors. NO fallback to Screener.in needed.
4. **Rate limits (Standard APIs incl. historical candles):** 50 req/sec,
   500 req/min, 2000 req/30min per user. Generous for NIFTY-500 scanning.
5. **Batch quotes:** V3 `/v3/market-quote/ltp` and `/v3/market-quote/ohlc`
   support **up to 500 instrument keys per request** -> entire NIFTY-500
   universe in **ONE call** (not batches of 50).
6. **Instrument master:** JSON now preferred (CSV deprecated). URLs like
   `https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz`,
   refreshed ~6 AM daily. `instrument_key` = `NSE_EQ|INE...`; includes ISIN,
   lot_size, tick_size, trading_symbol, segment/type for filtering EQ only.
7. **Historical candles:** `GET /v2/historical-candle/{instrument_key}/day/{to_date}/{from_date}`
   — daily data up to 1 year, single instrument per request.
8. **Bonus:** Upstox has a native **News API** (max 30 instrument keys/request) —
   possible future replacement for the Google News RSS scraper.

### Phase 1 — Upstox client (`upstox_client.py`) — DONE (live-tested)
- [x] Auth from `.env`: `UPSTOX_API_KEY` + `UPSTOX_ANALYTICS_TOKEN` (1-year, read-only). No refresh logic needed.
- [x] Instrument master download (JSON, gz) + cache + ticker -> `NSE_EQ|INE...` mapping via ISIN.
- [x] `batch_quotes(instrument_keys)` — LTP for up to 500 keys per call.
- [x] `historical_candles(instrument_key, interval="day", days=60)`.
- [x] Fundamentals: `key_ratios(isin)`, `income_statement(isin)`, `company_profile(isin)`.
- [x] Remove `yfinance`, add `requests` (raw REST; avoid heavyweight SDK) to requirements.
- [x] LIVE TEST (2026-08-12, real Analytics token): instrument master (2463 EQ), LTP, 500-batch,
      62-day candles, ISIN, fundamentals all working. FIXED: V2 LTP deprecated -> V3
      (`/v3/market-quote/ltp`, keys are `NSE_EQ:SYMBOL`); fundamentals path is
      `/fundamentals/:isin/profile` (not `company-profile`).

### Phase 2 — Universe + budget filter — DONE (live-tested)
- [x] NIFTY 500 constituents via NSE archives CSV (cached 7 days).
- [x] Batch LTP over universe (ONE `/v3/market-quote/ltp` call) -> filter by `LTP <= daily budget`.
- [x] Fetch 60-day history only for affordable subset.
- [x] TEST: 500-stock universe, INR 2000 budget -> 387 affordable in a single call.

### Phase 3 — Analysis pipeline — DONE (data layers live-tested)
- [x] `data_fetcher.py` on Upstox client (OHLCV DataFrame).
- [x] `technicals.py`: SMA_20/50, RSI_14, CDL_DOJI/MARUBOZU/ENGULFING. Uses pandas-ta when
      available, else pure-pandas fallback (pandas-ta is unmaintained; PyPI 3.12-only, git
      clone blocked locally).
- [x] `database.py`: SQLAlchemy models (Recommendation, PortfolioHolding, WatchlistItem,
      DailyBudget) + CRUD. Tested: budget, watchlist, holdings, save/status.
- [x] `news_scraper.py` (Google News RSS).
- [x] `agent.py` (Gemini 2.5 Pro + Pydantic `StockRecommendation` + chat).
- [x] `pipeline.py`: run_daily_analysis() orchestrates Phase 1 (portfolio SELL/HOLD) + Phase 2
      (budget-filtered BUY). Per-stock try/except.
- [x] `pipeline.py`: run_daily_analysis() orchestrates Phase 1 (portfolio SELL/HOLD) + Phase 2
      (budget-filtered BUY). Per-stock try/except.
- [x] LIVE TEST Gemini (2026-08-13): full stack works (Upstox -> technicals -> news -> Gemini ->
      Pydantic). KEY FINDINGS:
      * `gemini-2.5-pro` is RETIRED for new accounts (404). Using `gemini-3.5-flash`
        (configurable via `GEMINI_MODEL` env). `gemini-3.1-pro-preview` available but quota-gated.
      * FREE TIER LIMIT: 20 generate_content requests/day on gemini-3.5-flash. Our pipeline needs
        ~10 (Phase 2 top-N) + portfolio (Phase 1) per run — fits, but no headroom. Upgrade to paid
        tier for production.
      * Added top-N pre-screen (PHASE2_TOP_N=10) since cheap technicals on the full affordable set
        is fine but Gemini must only see the shortlist. Ranked by Uptrend/RSI/engulfing/momentum.
      * Added 429 quota backoff/retry in GeminiAgent._generate.
### Phase 4 — UI (`app.py`) — DONE (boots, serves health 200)
- [x] Daily Recommendations tab (metrics, expandable cards, Executed/Rejected buttons).
- [x] Budget tab (set daily budget -> triggers Phase 2 recompute).
- [x] Portfolio tab (add/remove holdings).
- [x] Watchlist tab (add/remove, seeded from config).
- [x] Ask Gemini chat tab (grounded in today's recs).
- [x] Scheduler auto-start via @st.cache_resource; Run Analysis Now button.

### Phase 5 — Scheduler, Docker, deployment — DONE
- [x] `scheduler.py` (APScheduler, weekdays 15:45 Asia/Kolkata; start/stop verified).
- [x] Dockerfile, docker-compose.yml, deploy.sh, hooks.json (webhook reads WEBHOOK_SECRET from env).
- [x] DEPLOYMENT_GUIDE.md (TrueNAS Scale 25.04 + Tailscale, step-by-step).

## Remaining / known limitations
- Gemini free tier ~20 calls/day -> fits daily run (Phase 2 top-10 + portfolio), paid tier recommended.
- No live smoke test of full run_daily_analysis end-to-end with Gemini after the top-N change
  (quota exhausted during testing on 2026-08-13). Pre-screen verified standalone.

## Files to create (from original spec)
- app.py, scheduler.py, data_fetcher.py, technicals.py, news_scraper.py, agent.py,
  database.py, config.py, upstox_client.py (new), requirements.txt, Dockerfile,
  docker-compose.yml, deploy.sh, hooks.json, .env.example, .gitignore,
  DEPLOYMENT_GUIDE.md

## Changelog
- 2026-08-11: Initial plan recorded; decisions on budget model, two-phase analysis, Upstox data source.
- 2026-08-11: Phase 0 verified. Wins: 1-year read-only Analytics token (no daily refresh), native
  Fundamentals API (Key Ratios/Income/Profile, ISIN-keyed), batch LTP of 500 keys/call, generous rate
  limits. Standard access token expires 3:30 AM IST and has NO refresh grant — Analytics token avoids this.
- 2026-08-11: Phase 1 code written (config.py, upstox_client.py, requirements.txt, .env templates).
  Live test pending real Analytics token. NOTE: `.env` is gitignored (not committed).

- 2026-08-13: Dev workflow change: docker-compose swing-bot now bind-mounts ./:/app (no image rebuild needed for code edits; container restart picks up changes). Demo Analysis Run (id=1) deleted from DB.
- 2026-08-18: Switched default model to `gemini-3.6-flash`; removed temperature, added `thinking_level=high` for max reasoning (Gemini 3 guidance). Wired Upstox fundamentals (key-ratios + income-statement) into both Phase 1 and Phase 2 Gemini prompts via `_fundamentals_summary` (resolves prior limitation).
- 2026-08-18: Full run logging. New `analysis_details` table records per-run, per-stock events (phase, universe, affordable list, scored ranking, shortlist, technicals, news, fundamentals, exact prompt sent, raw Gemini response, stored result, errors). Analysis Log tab renders everything. `run_daily_analysis()` now auto-creates an `AnalysisRun` when invoked by the scheduler (was missing run records for scheduled jobs). Fixed bug where budget=0 crashed `_run_phase2` (returned `[]` instead of `([], [])`).
- 2026-08-18: END-TO-END SMOKE TEST PASSED (budget INR 2000): universe 501 (389 affordable), top-10 shortlist, gemini-3.6-flash + thinking high returned 7/10 valid BUY recs, fundamentals+logging verified. 3 stocks hit transient 503 "model high demand" — root cause: Google `ServerError` is NOT a `ClientError` subclass, so the 429-only retry never fired. FIXED: agent now retries both 429 (quota) and 503 (busy) with backoff, reading status from `.code`/`.status_code`. Verified 200 OK path post-fix.
- 2026-08-18: Exit timing. `StockRecommendation` gains `holding_period_days` (predicted days to sell) + `exit_plan` (target/stop/time/momentum exit trigger); system prompt now instructs exit-timing justification. `recommendations` table migrated (ALTER TABLE ADD COLUMN, idempotent in init_db); pipeline persists both fields; UI shows exit days + plan on cards and history. Live call verified (BUY, exit in 7d, plan returned); 503 retry fix also proven in that call (2 retries then 200).
- 2026-10-03/04: Live smoke test fired 503-429s on gemini-3.6-flash; agent now retries both 429 & 503 with growing backoff/throttle (0.15s per request); `technicals.add_indicators` switched to the pure-pandas fallback (kills TA-Lib noise); Analysis Run timestamps now rendered in local IST in the UI.
- 2026-10-05: Pending-recommendations table/cards now show Profit % / Loss % (`(target|entry|stop)` relative to entry), with a per-run JSON log-download button in the Analysis Log.
- 2026-10-06: Resumeable runs. Status semantic: `Completed` = zero errors; any error or aborted phase => `Incomplete`. `run_daily_analysis(..., resume=True)` reuses the existing run_id, re-marks Running, marks symbols with a `result` event as carried (skipped), recomputes Phase 2 technicals/shortlist, and only re-invokes Gemini for symbols lacking results; per-run "Resume this analysis" button in the Analysis Log gated on `Incomplete`.
