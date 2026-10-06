import json
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


def _log_step(run_id, phase: int, symbol: str, step: str, detail) -> None:
    if run_id is None:
        return
    try:
        text = detail if isinstance(detail, str) else json.dumps(detail, indent=2, default=str)
        db.add_analysis_detail(run_id, phase, symbol, step, text)
    except Exception as exc:
        log.warning("Failed to persist analysis detail (%s): %s", step, exc)


def _technical_analysis(client: UpstoxClient, symbol: str) -> dict:
    df = get_historical_data(client, symbol)
    return technicals.summarize(df)


def _fundamentals_summary(client: UpstoxClient, symbol: str, max_chars: int = 4000) -> str:
    try:
        isin = client.isin_for(symbol)
        sections = [
            ("Key ratios", client.key_ratios(isin)),
            ("Income statement", client.income_statement(isin)),
        ]
        lines = []
        for label, payload in sections:
            if not payload:
                continue
            if isinstance(payload, dict):
                for k, v in payload.items():
                    if isinstance(v, (dict, list)):
                        v = json.dumps(v, default=str)
                    lines.append(f"{label} · {k}: {v}")
            else:
                lines.append(f"{label}: {payload}")
        return "\n".join(lines)[:max_chars]
    except Exception as exc:
        log.warning("Fundamentals failed for %s: %s", symbol, exc)
        return ""


def _run_phase1(client: UpstoxClient, agent: GeminiAgent, holdings: list, errors: list,
                run_id: int = None, already_done: set = None) -> list:
    recs = []
    if not holdings:
        log.info("Phase 1: no holdings to analyze")
        _log_step(run_id, 1, "", "phase", "No holdings to analyze.")
        return recs
    _log_step(run_id, 1, "", "phase", f"Analyzing {len(holdings)} held stocks.")
    for holding in holdings:
        symbol = holding.ticker
        if already_done and symbol in already_done:
            _log_step(run_id, 1, symbol, "skipped", "Already evaluated on this run — resume skips it.")
            continue
        try:
            technical = _technical_analysis(client, symbol)
            if not technical:
                log.warning("Phase 1: no technicals for %s, skipping", symbol)
                _log_step(run_id, 1, symbol, "skipped", "No technicals available.")
                continue
            _log_step(run_id, 1, symbol, "technicals", technical)
            news = news_scraper.get_recent_news(symbol)
            _log_step(run_id, 1, symbol, "news", news)
            fundamentals = _fundamentals_summary(client, symbol)
            _log_step(run_id, 1, symbol, "fundamentals", fundamentals or "No fundamentals data.")
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
                fundamentals=fundamentals,
                on_log=lambda step, detail, s=symbol: _log_step(run_id, 1, s, step, detail),
            )
            rec = {
                "ticker": symbol,
                "action": result.action,
                "confidence_score": result.confidence_score,
                "target_price": result.target_price,
                "stop_loss": result.stop_loss,
                "entry_price": ltp,
                "quantity": 0,
                "budget": 0.0,
                "holding_period_days": result.holding_period_days,
                "exit_plan": result.exit_plan,
                "rationale": result.rationale,
            }
            recs.append(rec)
            _log_step(run_id, 1, symbol, "result", rec)
        except QuotaExceededError as exc:
            errors.append(f"Gemini quota exceeded during Phase 1 ({symbol}): {exc}")
            _log_step(run_id, 1, symbol, "error", f"Quota exceeded: {exc}")
            return recs
        except Exception as exc:
            errors.append(f"Phase 1 failed for {symbol}: {exc}")
            _log_step(run_id, 1, symbol, "error", str(exc))
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


def _run_phase2(client: UpstoxClient, agent: GeminiAgent, budget: float, errors: list,
                run_id: int = None, already_done: set = None):
    if budget <= 0:
        log.info("Phase 2: budget is 0, no BUY analysis")
        _log_step(run_id, 2, "", "phase", "Budget is 0 — no BUY analysis.")
        return [], []
    _log_step(run_id, 2, "", "phase", f"Phase 2 started (budget INR {budget:,.0f}).")
    try:
        universe_symbols = universe.get_nifty500()
    except Exception as exc:
        errors.append(f"Failed to load universe: {exc}")
        _log_step(run_id, 2, "", "error", f"Failed to load universe: {exc}")
        log.exception("Failed to load universe: %s", exc)
        return [], []
    watchlist = db.get_watchlist()
    if not watchlist:
        watchlist = list(config.WATCHLIST)
    universe_symbols = list(dict.fromkeys(universe_symbols + watchlist))
    log.info("Phase 2: universe size (NIFTY 500 + watchlist): %d", len(universe_symbols))
    _log_step(run_id, 2, "", "universe", universe_symbols)
    try:
        affordable = universe.filter_by_budget(client, universe_symbols, budget)
    except Exception as exc:
        errors.append(f"Failed to filter universe by budget: {exc}")
        _log_step(run_id, 2, "", "error", f"Failed to filter universe by budget: {exc}")
        log.exception("Failed to filter universe by budget: %s", exc)
        return [], []
    log.info("Phase 2: %d stocks affordable within INR %.0f", len(affordable), budget)
    _log_step(
        run_id, 2, "", "affordable",
        f"{len(affordable)} stocks within budget:\n"
        + "\n".join(f"{a['symbol']} @ {a['ltp']}" for a in affordable),
    )

    scored = []
    for item in affordable:
        symbol = item["symbol"]
        try:
            technical = _technical_analysis(client, symbol)
            if technical:
                scored.append({"symbol": symbol, "ltp": technical.get("close") or item["ltp"], "tech": technical})
        except Exception as exc:
            _log_step(run_id, 2, symbol, "error", f"Technicals failed: {exc}")
            log.warning("Technicals failed for %s: %s", symbol, exc)
    scored.sort(key=lambda x: _tech_score(x["tech"]), reverse=True)
    shortlist = scored[: config.PHASE2_TOP_N]
    log.info("Phase 2: shortlisted top %d by technical score", len(shortlist))

    scored_meta = [
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
        for item in scored
    ]
    _log_step(run_id, 2, "", "scored", scored_meta)

    shortlist_meta = scored_meta[: config.PHASE2_TOP_N]
    _log_step(run_id, 2, "", "shortlist", shortlist_meta)
    _log_step(run_id, 2, "", "phase", f"Shortlisted top {len(shortlist)} by technical score.")

    recs = []
    for item in shortlist:
        symbol = item["symbol"]
        if already_done and symbol in already_done:
            _log_step(run_id, 2, symbol, "skipped", "Already evaluated on this run — resume skips it.")
            continue
        ltp = item["ltp"]
        try:
            _log_step(run_id, 2, symbol, "technicals", item["tech"])
            news = news_scraper.get_recent_news(symbol)
            _log_step(run_id, 2, symbol, "news", news)
            fundamentals = _fundamentals_summary(client, symbol)
            _log_step(run_id, 2, symbol, "fundamentals", fundamentals or "No fundamentals data.")
            qty = int(budget // ltp) if ltp else 0
            result = agent.analyze_stock(
                symbol,
                item["tech"],
                news,
                budget=budget,
                holding_context=None,
                fundamentals=fundamentals,
                on_log=lambda step, detail, s=symbol: _log_step(run_id, 2, s, step, detail),
            )
            rec = {
                "ticker": symbol,
                "action": result.action,
                "confidence_score": result.confidence_score,
                "target_price": result.target_price,
                "stop_loss": result.stop_loss,
                "entry_price": ltp,
                "quantity": qty if result.action == "BUY" else 0,
                "budget": budget,
                "holding_period_days": result.holding_period_days,
                "exit_plan": result.exit_plan,
                "rationale": result.rationale,
            }
            recs.append(rec)
            _log_step(run_id, 2, symbol, "result", rec)
        except QuotaExceededError as exc:
            errors.append(f"Gemini quota exceeded during Phase 2 ({symbol}): {exc}")
            _log_step(run_id, 2, symbol, "error", f"Quota exceeded: {exc}")
            return recs, shortlist_meta
        except Exception as exc:
            errors.append(f"Phase 2 failed for {symbol}: {exc}")
            _log_step(run_id, 2, symbol, "error", str(exc))
            log.exception("Phase 2 failed for %s: %s", symbol, exc)
    return recs, shortlist_meta


def run_daily_analysis(run_id: int = None, shortlist: list = None, resume: bool = False) -> dict:
    db.init_db()
    client = UpstoxClient()
    client.load_instrument_master()
    agent = GeminiAgent()

    if run_id is None:
        run_id = db.create_analysis_run(db.get_budget())
    elif resume:
        try:
            db.set_run_status(run_id, "Running")
        except Exception as exc:
            log.warning("Failed to mark run %s Running: %s", run_id, exc)

    holdings = db.get_holdings()
    budget = db.get_budget()
    errors = []

    carried1 = db.get_successful_symbols(run_id, 1) if resume else set()
    carried2 = db.get_successful_symbols(run_id, 2) if resume else set()

    phase1 = _run_phase1(client, agent, holdings, errors, run_id, already_done=carried1)
    phase2, shortlist_meta = _run_phase2(client, agent, budget, errors, run_id, already_done=carried2)
    if shortlist is not None:
        shortlist.extend(shortlist_meta)

    all_recs = phase1 + phase2
    if all_recs:
        db.save_recommendations(all_recs)
        log.info("Saved %d recommendations", len(all_recs))

    phase1_total = len(carried1) + len(phase1)
    phase2_total = len(carried2) + len(phase2)

    result = {
        "date": date.today().isoformat(),
        "phase1_count": phase1_total,
        "phase2_count": phase2_total,
        "budget": budget,
        "holdings_count": len(holdings),
        "errors": errors,
        "shortlist": shortlist_meta,
    }
    db.complete_analysis_run(run_id, phase1_total, phase2_total, shortlist_meta, errors)
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


def start_async_resume(run_id: int) -> bool:
    global _running
    if not _run_lock.acquire(blocking=False):
        return False
    if _running:
        _run_lock.release()
        return False
    _running = True
    threading.Thread(target=_run_in_background_resume, args=(run_id,), daemon=True).start()
    return True


def _run_in_background_resume(run_id: int) -> None:
    global _running
    try:
        run_daily_analysis(run_id=run_id, resume=True)
    except Exception as exc:
        log.exception("Background resume failed: %s", exc)
        db.complete_analysis_run(run_id, 0, 0, [], [str(exc)])
    finally:
        _running = False
        _run_lock.release()