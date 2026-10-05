import json
import logging
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import database as db
import pipeline
from agent import GeminiAgent, QuotaExceededError

logging.basicConfig(level=logging.INFO)
db.init_db()

st.set_page_config(
    page_title="Swing Trading Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items=None,
)

HIDE_CHROME = """
<style>
    #MainMenu {visibility: hidden;}
    header[data-testid="stHeader"] {visibility: hidden;}
    footer {visibility: hidden;}
    [data-testid="stToolbar"] {visibility: hidden;}
    [data-testid="stAppDeployButton"] {visibility: hidden;}
    .block-container {padding-top: 1rem; padding-bottom: 1rem;}
    @media (max-width: 768px) {
        .block-container {padding-left: 0.5rem; padding-right: 0.5rem;}
    }
    div[data-testid="stExpander"] details summary p {font-size: 1rem;}
</style>
"""
st.markdown(HIDE_CHROME, unsafe_allow_html=True)


@st.cache_resource
def start_scheduler() -> None:
    try:
        import scheduler

        scheduler.start()
    except Exception as exc:
        logging.getLogger(__name__).warning("Scheduler not started: %s", exc)


def _recommendation_df(recs) -> pd.DataFrame:
    rows = [
        {
            "ID": r.id,
            "Date": r.date,
            "Ticker": r.ticker,
            "Action": r.action,
            "Confidence": r.confidence_score,
            "Target": r.target_price,
            "Stop Loss": r.stop_loss,
            "Entry": r.entry_price,
            "Qty": r.quantity,
            "Budget": r.budget,
            "Exit Days": r.holding_period_days,
            "Exit Plan": r.exit_plan,
            "Status": r.status,
        }
        for r in recs
    ]
    return pd.DataFrame(rows)


def _run_and_report() -> bool:
    if pipeline.is_running():
        st.warning("An analysis is already running. This click was ignored.")
        return False
    started = pipeline.start_async_analysis()
    if not started:
        st.warning("An analysis is already running. This click was ignored.")
        return False
    st.success("Analysis started in the background. You can close this page; it will keep running.")
    return True


def _local(dt):
    return (dt + timedelta(hours=5, minutes=30)) if dt else dt


def _show_run_status() -> None:
    last = db.get_last_run()
    if not last:
        return
    if last.status == "Running":
        st.info(
            f"An analysis run is currently in progress (started {_local(last.started_at):%H:%M}). "
            "Refresh this page to see the latest results."
        )
    else:
        errors = last.errors or "[]"
        try:
            err_list = json.loads(errors)
        except Exception:
            err_list = []
        if err_list:
            for err in err_list:
                if "quota" in err.lower():
                    st.error(err)
                else:
                    st.warning(err)
        else:
            st.success(
                f"Last run ({_local(last.started_at):%H:%M}) → {last.phase1_count} portfolio + "
                f"{last.phase2_count} BUY recommendations."
            )


STEP_LABELS = {
    "phase": "Phase status",
    "universe": "Universe (NIFTY 500 + watchlist)",
    "affordable": "Affordable stocks (LTP within budget)",
    "scored": "All scored stocks (technical rank)",
    "shortlist": "Shortlist (top-N, sent to Gemini)",
    "technicals": "Technicals (latest bar)",
    "news": "News headlines",
    "fundamentals": "Fundamentals (Upstox)",
    "prompt": "Prompt sent to Gemini",
    "response": "Raw Gemini response",
    "result": "Stored recommendation",
    "error": "Error",
    "skipped": "Skipped",
}


def _render_stock_detail(ev) -> None:
    label = STEP_LABELS.get(ev.step, ev.step)
    st.markdown(f"**{label}**")
    if ev.step == "error":
        st.warning(ev.detail)
    elif ev.step == "phase":
        st.markdown(ev.detail)
    elif ev.step == "skipped":
        st.info(ev.detail)
    elif ev.step in ("result", "response", "technicals"):
        try:
            st.json(json.loads(ev.detail))
        except Exception:
            st.code(ev.detail)
    else:
        st.code(ev.detail)


def _render_run_event(ev) -> None:
    label = STEP_LABELS.get(ev.step, ev.step)
    if ev.step == "phase":
        st.markdown(ev.detail)
    elif ev.step == "error":
        st.warning(ev.detail)
    elif ev.step in ("universe", "affordable", "scored"):
        width = len(ev.detail.splitlines())
        with st.expander(f"{label} · {width} line{'s' if width != 1 else ''}"):
            try:
                payload = json.loads(ev.detail)
                st.json(payload)
            except Exception:
                st.code(ev.detail)
    else:
        with st.expander(label):
            try:
                st.json(json.loads(ev.detail))
            except Exception:
                st.code(ev.detail)


def _render_analysis_log() -> None:
    st.header("Analysis Log")
    runs = db.get_recent_runs(limit=10)
    if not runs:
        st.info("No analysis runs yet. Run an analysis to see the shortlist and reasoning.")
        return
    for run in runs:
        details = db.get_run_details(run.id)
        with st.expander(
            f"Run #{run.id} · {_local(run.started_at):%Y-%m-%d %H:%M} · "
            f"budget ₹{run.budget:,.0f} · {run.status}"
        ):
            st.write(
                f"Phase 1 (portfolio): **{run.phase1_count}** · "
                f"Phase 2 (BUY): **{run.phase2_count}** · logged events: **{len(details)}**"
            )
            try:
                shortlist = json.loads(run.shortlist or "[]")
            except Exception:
                shortlist = []
            try:
                run_errors = json.loads(run.errors or "[]")
            except Exception:
                run_errors = []
            log_payload = {
                "run_id": run.id,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "status": run.status,
                "budget": run.budget,
                "phase1_count": run.phase1_count,
                "phase2_count": run.phase2_count,
                "shortlist": shortlist,
                "errors": run_errors,
                "events": [
                    {
                        "phase": d.phase,
                        "symbol": d.symbol,
                        "step": d.step,
                        "detail": d.detail,
                        "created_at": d.created_at.isoformat() if d.created_at else None,
                    }
                    for d in details
                ],
            }
            st.download_button(
                "Download run log as JSON",
                data=json.dumps(log_payload, indent=2, default=str),
                file_name=f"analysis_run_{run.id}_{run.started_at:%Y%m%d_%H%M}.json",
                mime="application/json",
                key=f"dl_run_{run.id}",
            )
            if not details:
                st.info("No detail records for this run.")
                continue
            for phase in (1, 2):
                phase_events = [d for d in details if d.phase == phase]
                if not phase_events:
                    continue
                st.subheader(
                    "Phase 1 — Portfolio (SELL/HOLD)" if phase == 1 else "Phase 2 — New BUYs"
                )
                for ev in phase_events:
                    if not ev.symbol:
                        _render_run_event(ev)
                stock_symbols = []
                for ev in phase_events:
                    if ev.symbol and ev.symbol not in stock_symbols:
                        stock_symbols.append(ev.symbol)
                for symbol in stock_symbols:
                    with st.expander(f"Analysis: {symbol}"):
                        for ev in phase_events:
                            if ev.symbol == symbol:
                                _render_stock_detail(ev)


def _render_today() -> None:
    st.header("Daily Recommendations (Today)")
    recs = db.get_today_recommendations()
    if not recs:
        st.info(
            "No recommendations for today yet. The 3:45 PM IST job or the "
            "'Run Analysis Now' button will populate this."
        )
        return

    buys = sum(1 for r in recs if r.action == "BUY")
    sells = sum(1 for r in recs if r.action == "SELL")
    holds = sum(1 for r in recs if r.action == "HOLD")
    c1, c2, c3 = st.columns(3)
    c1.metric("BUY", buys)
    c2.metric("SELL", sells)
    c3.metric("HOLD", holds)

    st.subheader("Pending Recommendations")
    pending = [r for r in recs if r.status == "Pending"]
    df = _recommendation_df(pending)
    if not df.empty:
        st.dataframe(
            df.drop(columns=["ID", "Date"]),
            use_container_width=True,
            hide_index=True,
        )

    for r in pending:
        with st.expander(f"{r.ticker} — {r.action} (conf {r.confidence_score})"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Entry", r.entry_price)
            c2.metric("Target", r.target_price)
            c3.metric("Stop Loss", r.stop_loss)
            c4.metric("Qty", r.quantity)
            st.write(f"**Budget basis:** ₹{r.budget:,.0f}" if r.budget else "**Budget basis:** n/a")
            if r.quantity and r.entry_price and r.target_price and r.stop_loss and r.entry_price > 0:
                profit_pct = (r.target_price - r.entry_price) / r.entry_price * 100
                loss_pct = (r.entry_price - r.stop_loss) / r.entry_price * 100
                st.write(
                    f"**Projected profit if target hits:** {profit_pct:.1f}% · "
                    f"**Projected loss if stop-loss hits:** {loss_pct:.1f}%"
                )
            if r.holding_period_days:
                st.write(f"**Expected exit:** ~{r.holding_period_days} days")
            if r.exit_plan:
                st.write(f"**Exit plan:** {r.exit_plan}")
            st.write(r.rationale)
            a, b = st.columns(2)
            if a.button("Mark Executed", key=f"exe_{r.id}"):
                db.update_recommendation_status(r.id, "Executed")
                st.rerun()
            if b.button("Mark Rejected", key=f"rej_{r.id}"):
                db.update_recommendation_status(r.id, "Rejected")
                st.rerun()


def _render_history() -> None:
    st.header("History & Performance")
    recs = db.get_historical_recommendations()
    if not recs:
        st.info("No historical recommendations yet.")
        return
    df = _recommendation_df(recs)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.subheader("Status breakdown")
    st.write(df["Status"].value_counts().rename_axis("Status").reset_index(name="Count"))


def _render_budget() -> None:
    st.header("Daily Budget")
    today = date.today()
    current = db.get_budget()
    st.write(f"Today ({today.isoformat()}) available budget: **₹{current:,.2f}**")
    new_budget = st.number_input(
        "Set budget for today (defaults to 0; changing it triggers Phase 2 recompute)",
        min_value=0.0,
        max_value=1_000_000.0,
        value=float(current),
        step=100.0,
    )
    if st.button("Update Budget & Recompute BUY Analysis"):
        db.set_budget(new_budget)
        _run_and_report()
        st.rerun()

def _render_portfolio() -> None:
    st.header("Portfolio")
    holdings = db.get_holdings()
    if holdings:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "ID": h.id,
                        "Ticker": h.ticker,
                        "Quantity": h.quantity,
                        "Avg Price": h.avg_price,
                    }
                    for h in holdings
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        for h in holdings:
            if st.button(f"Remove {h.ticker}", key=f"rm_{h.id}"):
                db.remove_holding(h.id)
                st.rerun()
    else:
        st.info("No holdings yet. Add one below.")
    st.subheader("Add holding")
    with st.form("add_holding"):
        col1, col2, col3 = st.columns(3)
        ticker = col1.text_input("Ticker (e.g. RELIANCE)")
        qty = col2.number_input("Quantity", min_value=1, step=1)
        avg_price = col3.number_input("Avg buy price", min_value=0.0, step=0.5)
        submitted = st.form_submit_button("Add")
        if submitted and ticker:
            db.add_holding(ticker.strip().upper(), int(qty), float(avg_price))
            st.rerun()


def _render_watchlist() -> None:
    st.header("Watchlist")
    items = db.get_watchlist()
    if not items:
        db.set_watchlist(config_defaults())
        items = db.get_watchlist()
    st.write("Your watchlist (add/remove as needed):")
    st.write(", ".join(items))
    with st.form("add_watch"):
        new_ticker = st.text_input("Add ticker")
        submitted = st.form_submit_button("Add")
        if submitted and new_ticker:
            ticker = new_ticker.strip().upper()
            if ticker and ticker not in items:
                db.set_watchlist(items + [ticker])
                st.rerun()
    remove_ticker = st.selectbox("Remove ticker", ["--"] + items)
    if st.button("Remove") and remove_ticker != "--":
        db.set_watchlist([t for t in items if t != remove_ticker])
        st.rerun()


def _render_chat() -> None:
    st.header("Ask Gemini")
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    for role, msg in st.session_state.chat_history:
        with st.chat_message(role):
            st.write(msg)
    prompt = st.chat_input("Ask about today's data, e.g. 'Why is TCS a HOLD?'")
    if prompt:
        st.session_state.chat_history.append(("user", prompt))
        with st.chat_message("user"):
            st.write(prompt)
        try:
            recs = db.get_today_recommendations()
            context = "\n".join(
                f"{r.ticker}: {r.action} conf={r.confidence_score} target={r.target_price} "
                f"sl={r.stop_loss}\n{r.rationale}"
                for r in recs
            )
            agent = GeminiAgent()
            answer = agent.chat(prompt, context)
            st.session_state.chat_history.append(("assistant", answer))
            with st.chat_message("assistant"):
                st.write(answer)
        except QuotaExceededError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Gemini chat failed: {exc}")


def config_defaults():
    import config

    return list(config.WATCHLIST)


def main() -> None:
    st.title("Swing Trading Bot")
    st.caption("NSE (NIFTY 500 universe) · weekday EOD analysis at 3:45 PM IST · budget-aware")

    start_scheduler()

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
        ["Recommendations", "Budget", "Portfolio", "Watchlist", "Analysis Log", "Ask Gemini"]
    )
    with tab1:
        col_a, col_b = st.columns([4, 1])
        with col_b:
            if st.button("Run Analysis Now"):
                _run_and_report()
                st.rerun()
        _show_run_status()
        _render_today()
    with tab2:
        _render_budget()
    with tab3:
        _render_portfolio()
    with tab4:
        _render_watchlist()
    with tab5:
        _render_analysis_log()
    with tab6:
        _render_chat()


if __name__ == "__main__":
    main()
