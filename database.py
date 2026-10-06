import json
import logging
from datetime import date, datetime
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import text

import config

log = logging.getLogger(__name__)

Base = declarative_base()

DB_URL = f"sqlite:///{config.DB_PATH}"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True)
    date = Column(Date, index=True, default=date.today)
    ticker = Column(String, index=True)
    action = Column(String)
    confidence_score = Column(Integer)
    target_price = Column(Float)
    stop_loss = Column(Float)
    entry_price = Column(Float)
    quantity = Column(Integer)
    budget = Column(Float)
    rationale = Column(Text)
    holding_period_days = Column(Integer, nullable=True)
    exit_plan = Column(Text, nullable=True)
    status = Column(String, default="Pending")


class PortfolioHolding(Base):
    __tablename__ = "portfolio"

    id = Column(Integer, primary_key=True)
    ticker = Column(String, index=True)
    quantity = Column(Integer)
    avg_price = Column(Float)


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True)
    ticker = Column(String, index=True)


class DailyBudget(Base):
    __tablename__ = "daily_budget"

    id = Column(Integer, primary_key=True)
    date = Column(Date, index=True, default=date.today)
    amount = Column(Float, default=0.0)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, default="Running")
    budget = Column(Float, default=0.0)
    phase1_count = Column(Integer, default=0)
    phase2_count = Column(Integer, default=0)
    shortlist = Column(Text, default="[]")
    errors = Column(Text, default="[]")


class AnalysisDetail(Base):
    __tablename__ = "analysis_details"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, index=True)
    phase = Column(Integer, default=0)
    symbol = Column(String, default="")
    step = Column(String, default="")
    detail = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_recommendations()


_RECOMMENDATION_MIGRATIONS = [
    ("holding_period_days", "INTEGER"),
    ("exit_plan", "TEXT"),
]


def _migrate_recommendations() -> None:
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(recommendations)"))}
        for name, coltype in _RECOMMENDATION_MIGRATIONS:
            if name not in cols:
                conn.execute(
                    text(f"ALTER TABLE recommendations ADD COLUMN {name} {coltype}")
                )
                log.info("Migrated recommendations: added column %s", name)


def _session():
    return SessionLocal()


def save_recommendations(recs: list) -> None:
    with _session() as session:
        for rec in recs:
            session.add(
                Recommendation(
                    date=rec.get("date") or date.today(),
                    ticker=rec["ticker"],
                    action=rec["action"],
                    confidence_score=rec.get("confidence_score"),
                    target_price=rec.get("target_price"),
                    stop_loss=rec.get("stop_loss"),
                    entry_price=rec.get("entry_price"),
                    quantity=rec.get("quantity"),
                    budget=rec.get("budget"),
                    rationale=rec.get("rationale", ""),
                    holding_period_days=rec.get("holding_period_days"),
                    exit_plan=rec.get("exit_plan"),
                    status="Pending",
                )
            )
        session.commit()


def get_today_recommendations():
    with _session() as session:
        return (
            session.query(Recommendation)
            .filter(Recommendation.date == date.today())
            .all()
        )


def update_recommendation_status(rec_id: int, status: str) -> None:
    with _session() as session:
        rec = session.query(Recommendation).get(rec_id)
        if rec:
            rec.status = status
            session.commit()


def get_historical_recommendations():
    with _session() as session:
        return (
            session.query(Recommendation)
            .order_by(Recommendation.date.desc(), Recommendation.id.desc())
            .all()
        )


def get_holdings() -> list:
    with _session() as session:
        return session.query(PortfolioHolding).all()


def add_holding(ticker: str, quantity: int, avg_price: float) -> None:
    with _session() as session:
        session.add(PortfolioHolding(ticker=ticker, quantity=quantity, avg_price=avg_price))
        session.commit()


def remove_holding(holding_id: int) -> None:
    with _session() as session:
        holding = session.query(PortfolioHolding).get(holding_id)
        if holding:
            session.delete(holding)
            session.commit()


def get_watchlist() -> list:
    with _session() as session:
        return [w.ticker for w in session.query(WatchlistItem).all()]


def set_watchlist(tickers: list) -> None:
    with _session() as session:
        session.query(WatchlistItem).delete()
        for t in tickers:
            session.add(WatchlistItem(ticker=t))
        session.commit()


def get_budget() -> float:
    with _session() as session:
        row = (
            session.query(DailyBudget)
            .filter(DailyBudget.date == date.today())
            .order_by(DailyBudget.id.desc())
            .first()
        )
        return row.amount if row else 0.0


def set_budget(amount: float) -> None:
    with _session() as session:
        session.add(DailyBudget(date=date.today(), amount=amount))
        session.commit()


def create_analysis_run(budget: float) -> int:
    with _session() as session:
        run = AnalysisRun(status="Running", budget=budget)
        session.add(run)
        session.commit()
        return run.id


def complete_analysis_run(run_id: int, phase1: int, phase2: int,
                          shortlist: list, errors: list) -> None:
    with _session() as session:
        run = session.query(AnalysisRun).get(run_id)
        if run:
            run.finished_at = datetime.utcnow()
            run.status = "Completed" if not errors else "Incomplete"
            run.phase1_count = phase1
            run.phase2_count = phase2
            run.shortlist = json.dumps(shortlist)
            run.errors = json.dumps(errors)
            session.commit()


def get_last_run():
    with _session() as session:
        return (
            session.query(AnalysisRun)
            .order_by(AnalysisRun.id.desc())
            .first()
        )


def get_recent_runs(limit: int = 10):
    with _session() as session:
        return (
            session.query(AnalysisRun)
            .order_by(AnalysisRun.id.desc())
            .limit(limit)
            .all()
        )


def add_analysis_detail(run_id: int, phase: int, symbol: str, step: str, detail: str) -> None:
    with _session() as session:
        session.add(
            AnalysisDetail(
                run_id=run_id, phase=phase, symbol=symbol, step=step, detail=detail
            )
        )
        session.commit()


def get_run_details(run_id: int) -> list:
    with _session() as session:
        return (
            session.query(AnalysisDetail)
            .filter(AnalysisDetail.run_id == run_id)
            .order_by(AnalysisDetail.id.asc())
            .all()
        )


def get_successful_symbols(run_id: int, phase: int) -> set:
    with _session() as session:
        rows = (
            session.query(AnalysisDetail)
            .filter(
                AnalysisDetail.run_id == run_id,
                AnalysisDetail.phase == phase,
                AnalysisDetail.step == "result",
            )
            .all()
        )
        return {r.symbol for r in rows if r.symbol}


def set_run_status(run_id: int, status: str) -> None:
    with _session() as session:
        run = session.query(AnalysisRun).get(run_id)
        if run:
            run.status = status
            session.commit()
