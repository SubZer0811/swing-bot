import logging
import threading
from datetime import date

import database as db
import news_scraper
import technicals
import universe
from agent import GeminiAgent, QuotaExceededError
from data_fetcher import get_historical_data
from upstox_client import UpstoxClient, UpstoxError

import config

log = logging.getLogger(__name__)

_run_lock = threading.Lock()
_running = False


def _technical_analysis(client: UpstoxClient, symbol: str) -> dict:
    df = get_historical_data(client, symbol)
    return technicals.summarize(df)


def _run_phase1(client: UpstoxClient, agent: GeminiAgent, holdings: list, errors: list) -> list:
    recs = []
    if not holdings:
        log.info("Phase 1: no holdings to analyze")
        return recs
    for holding in holdings:
        symbol = holding.ticker
        try:
            technical = _technical_analysis(client, symbol)
            if not technical:
                log.warning("Phase 1: no technicals for %s, skipping", symbol)
                continue
            news = news_scraper.get_recent_news(symbol)
            ltp = technical.get("close")
            context = {
                "quantity": holding.quantity,
                "avg_price": holding.avg_price,
                "ltp": ltp,
            }
            result = agent.analyze_stock(
                symbol,
                technical,
                news,
                budget=0.0,
                holding_context=context,
            )
            recs.append(
                {
                    "ticker": symbol,
                    "action": result.action,
                    "confidence_score": result.confidence_score,
                    "target_price": result.target_price,
                    "stop_loss": result.stop_loss,
                    "entry_price": ltp,
                    "quantity": 0,
                    "budget": 0.0,
                    "rationale": result.rationale,
                }
            )
        except QuotaExceededError as exc:
            errors.append(f"Gemini quota exceeded during Phase 1 ({symbol}): {exc}")
            return recs
        except Exception as exc:
            errors.append(f"Phase 1 failed for {symbol}: {exc}")
            log.exception("Phase 1 failed for %s: %s", symbol, exc)
    return recs


def _tech_score(tech: dict) -> float:
    score = 0.0
    trend = tech.get("trend")
    if trend == "Uptrend":
        score += 3
    elif trend == "Downtrend":
        score -= 2
    rsi = tech.get("rsi_14")
    if rsi is not None:
        if 55 <= rsi <= 70:
            score += 2
        elif rsi > 75:
            score -= 2
        elif rsi < 35:
            score -= 1
    eng = tech.get("engulfing")
    if eng == "Bullish":
        score += 2
    elif eng == "Bearish":
        score -= 2
    mom = tech.get("5d_momentum_pct") or 0
    score += max(-2, min(2, mom / 2))
    return score


def _run_phase2(client: UpstoxClient, agent: GeminiAgent, budget: float, errors: list) -> list:
    if budget <= 0:
        log.info("Phase 2: budget is 0, no BUY analysis")
        return []
    try:
        universe_symbols = universe.get_nifty500()
    except Exception as exc:
        errors.append(f"Failed to load universe: {exc}")
        log.exception("Failed to load universe: %s", exc)
        return [], []
    watchlist = db.get_watchlist()
    if not watchlist:
        watchlist = list(config.WATCHLIST)
    universe_symbols = list(dict.fromkeys(universe_symbols + watchlist))
    log.info("Phase 2: universe size (NIFTY 500 + watchlist): %d", len(universe_symbols))
    try:
        affordable = universe.filter_by_budget(client, universe_symbols, budget)
    except Exception as exc:
        errors.append(f"Failed to filter universe by budget: {exc}")
        log.exception("Failed to filter universe by budget: %s", exc)
        return [], []
    log.info("Phase 2: %d stocks affordable within INR %.0f", len(affordable), budget)

    scored = []
    for item in affordable:
        symbol = item["symbol"]
        try:
            technical = _technical_analysis(client, symbol)
            if technical:
                scored.append({"symbol": symbol, "ltp": technical.get("close") or item["ltp"], "tech": technical})
        except Exception as exc:
            log.warning("Technicals failed for %s: %s", symbol, exc)
    scored.sort(key=lambda x: _tech_score(x["tech"]), reverse=True)
    shortlist = scored[: config.PHASE2_TOP_N]
    log.info("Phase 2: shortlisted top %d by technical score", len(shortlist))

    shortlist_meta = [
        {
            "symbol": item["symbol"],
            "ltp": item["ltp"],
            "score": round(_tech_score(item["tech"]), 2),
            "trend": item["tech"].get("trend"),
            "rsi": item["tech"].get("rsi_14"),
            "engulfing": item["tech"].get("engulfing"),
            "marubozu": item["tech"].get("marubozu"),
            "doji": item["tech"].get("doji"),
        }
        for item in shortlist
    ]

    recs = []
    for item in shortlist:
        symbol = item["symbol"]
        ltp = item["ltp"]
        try:
            news = news_scraper.get_recent_news(symbol)
            qty = int(budget // ltp) if ltp else 0
            result = agent.analyze_stock(
                symbol, item["tech"], news, budget=budget, holding_context=None
            )
            recs.append(
                {
                    "ticker": symbol,
                    "action": result.action,
                    "confidence_score": result.confidence_score,
                    "target_price": result.target_price,
                    "stop_loss": result.stop_loss,
                    "entry_price": ltp,
                    "quantity": qty if result.action == "BUY" else 0,
                    "budget": budget,
                    "rationale": result.rationale,
                }
            )
        except QuotaExceededError as exc:
            errors.append(f"Gemini quota exceeded during Phase 2 ({symbol}): {exc}")
            return recs, shortlist_meta
        except Exception as exc:
            errors.append(f"Phase 2 failed for {symbol}: {exc}")
            log.exception("Phase 2 failed for %s: %s", symbol, exc)
    return recs, shortlist_meta


def run_daily_analysis(run_id: int = None, shortlist: list = None) -> dict:
    db.init_db()
    client = UpstoxClient()
    client.load_instrument_master()
    agent = GeminiAgent()

    holdings = db.get_holdings()
    budget = db.get_budget()
    errors = []

    phase1 = _run_phase1(client, agent, holdings, errors)
    phase2, shortlist_meta = _run_phase2(client, agent, budget, errors)
    if shortlist is not None:
        shortlist.extend(shortlist_meta)

    all_recs = phase1 + phase2
    if all_recs:
        db.save_recommendations(all_recs)
        log.info("Saved %d recommendations", len(all_recs))
    else:
        log.info("No recommendations generated today")

    result = {
        "date": date.today().isoformat(),
        "phase1_count": len(phase1),
        "phase2_count": len(phase2),
        "budget": budget,
        "holdings_count": len(holdings),
        "errors": errors,
        "shortlist": shortlist_meta,
    }
    if run_id is not None:
        db.complete_analysis_run(run_id, len(phase1), len(phase2), shortlist_meta, errors)
    return result


def is_running() -> bool:
    return _running


def start_async_analysis() -> bool:
    global _running
    if not _run_lock.acquire(blocking=False):
        return False
    if _running:
        _run_lock.release()
        return False
    _running = True
    run_id = db.create_analysis_run(db.get_budget())
    threading.Thread(
        target=_run_in_background, args=(run_id,), daemon=True
    ).start()
    return True


def _run_in_background(run_id: int) -> None:
    global _running
    try:
        run_daily_analysis(run_id=run_id)
    except Exception as exc:
        log.exception("Background analysis failed: %s", exc)
        db.complete_analysis_run(run_id, 0, 0, [], [str(exc)])
    finally:
        _running = False
        _run_lock.release()
