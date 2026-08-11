import logging
from datetime import date

import database as db
import news_scraper
import technicals
import universe
from agent import GeminiAgent
from data_fetcher import get_historical_data
from upstox_client import UpstoxClient, UpstoxError

log = logging.getLogger(__name__)


def _technical_analysis(client: UpstoxClient, symbol: str) -> dict:
    df = get_historical_data(client, symbol)
    return technicals.summarize(df)


def _run_phase1(client: UpstoxClient, agent: GeminiAgent, holdings: list) -> list:
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
        except Exception as exc:
            log.exception("Phase 1 failed for %s: %s", symbol, exc)
    return recs


def _run_phase2(client: UpstoxClient, agent: GeminiAgent, budget: float) -> list:
    if budget <= 0:
        log.info("Phase 2: budget is 0, no BUY analysis")
        return []
    try:
        universe_symbols = universe.get_nifty500()
    except Exception as exc:
        log.exception("Failed to load universe: %s", exc)
        return []
    try:
        affordable = universe.filter_by_budget(client, universe_symbols, budget)
    except Exception as exc:
        log.exception("Failed to filter universe by budget: %s", exc)
        return []
    log.info("Phase 2: %d stocks affordable within INR %.0f", len(affordable), budget)
    recs = []
    for item in affordable:
        symbol = item["symbol"]
        try:
            technical = _technical_analysis(client, symbol)
            if not technical:
                continue
            news = news_scraper.get_recent_news(symbol)
            ltp = technical.get("close") or item["ltp"]
            qty = int(budget // ltp) if ltp else 0
            result = agent.analyze_stock(
                symbol, technical, news, budget=budget, holding_context=None
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
        except Exception as exc:
            log.exception("Phase 2 failed for %s: %s", symbol, exc)
    return recs


def run_daily_analysis() -> dict:
    db.init_db()
    client = UpstoxClient()
    client.load_instrument_master()
    agent = GeminiAgent()

    holdings = db.get_holdings()
    budget = db.get_budget()

    phase1 = _run_phase1(client, agent, holdings)
    phase2 = _run_phase2(client, agent, budget)

    all_recs = phase1 + phase2
    if all_recs:
        db.save_recommendations(all_recs)
        log.info("Saved %d recommendations", len(all_recs))
    else:
        log.info("No recommendations generated today")

    return {
        "date": date.today().isoformat(),
        "phase1_count": len(phase1),
        "phase2_count": len(phase2),
        "budget": budget,
        "holdings_count": len(holdings),
    }
