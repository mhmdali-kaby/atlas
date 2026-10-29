"""Agent runtime tables (ATL-78) through the SQLAlchemy models.

Needs the checkout's test DB migrated: run `cd backend && php artisan test` first.
"""

import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from atlas_shared import testing
from atlas_shared.db import (
    AgentNotebookEntry,
    AgentRun,
    AgentScore,
    AgentState,
    AgentUsageDaily,
    JournalEntry,
    PromptVersion,
    ResearchReport,
    add_usage,
    make_engine,
    usage_day,
)
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

TS = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
DAY = date(2026, 10, 29)
RUN = uuid.uuid4().hex[:8]
AGENT = f"test_{RUN}"


@pytest.fixture(scope="module")
def engine() -> Engine:
    return make_engine(testing.db_settings())


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s
        s.rollback()
        for model in (
            ResearchReport,
            AgentRun,
            AgentUsageDaily,
            AgentState,
            PromptVersion,
            AgentNotebookEntry,
            JournalEntry,
            AgentScore,
        ):
            s.execute(delete(model).where(model.agent == AGENT))
        s.commit()


def test_agent_run_and_research_report_round_trip(session: Session) -> None:
    run = AgentRun(
        env="demo",
        agent=AGENT,
        trigger="mention",
        correlation_id="cycle-2026-10-29",
        started_at=TS,
        model="claude-opus",
        prompt_version=3,
        tokens_in=1200,
        tokens_out=300,
        turns=4,
        cost_usd_notional=Decimal("0.123456"),
    )
    session.add(run)
    session.flush()
    report = ResearchReport(
        env="demo",
        agent=AGENT,
        agent_run_id=run.id,
        inst_id="BTC-USDT-SWAP",
        kind="regime_table",
        content_md="Trend.",
        data={"regime": "trend", "p": 0.8},
        confidence=Decimal("0.65"),
        valid_until=TS,
    )
    session.add(report)
    session.commit()
    session.expire_all()

    got_run = session.get(AgentRun, run.id)
    assert got_run is not None
    assert (got_run.status, got_run.tokens_in, got_run.turns) == ("running", 1200, 4)
    assert got_run.cost_usd_notional == Decimal("0.123456")
    got = session.get(ResearchReport, report.id)
    assert got is not None
    assert got.data == {"regime": "trend", "p": 0.8}
    assert got.confidence == Decimal("0.6500")
    assert got.agent_run_id == run.id


def test_state_prompt_notebook_journal_score_round_trip(session: Session) -> None:
    body = "ROLE: Historian."
    state = AgentState(agent=AGENT, env="demo", state="thinking", current_task="regime table")
    digest = hashlib.sha256(body.encode()).hexdigest()
    prompt = PromptVersion(agent=AGENT, version=1, sha256=digest, body=body)
    note = AgentNotebookEntry(env="demo", day=DAY, agent=AGENT, note_md="CPI at 15:30.", data={"k": 1})
    journal = JournalEntry(
        env="demo",
        agent=AGENT,
        inst_id="XAU-USDT-SWAP",
        title="XAU long",
        expected_md="+2R",
        actual_md="-1R",
        lessons_md="Wait for CPI.",
        tags=["pullback", "commodity", "loss"],
    )
    score = AgentScore(
        agent=AGENT,
        env="demo",
        day=DAY,
        n=12,
        hit_rate=Decimal("0.5833"),
        brier_score=Decimal("0.210000"),
        calibration={"0.6": [7, 12]},
        pnl_attributed_usd=Decimal("123.45"),
    )
    session.add_all([state, prompt, note, journal, score])
    session.commit()
    session.expire_all()

    got_state = session.get(AgentState, state.id)
    assert got_state is not None and got_state.state == "thinking"
    assert got_state.state_since is not None
    got_prompt = session.get(PromptVersion, prompt.id)
    assert got_prompt is not None and got_prompt.body == body and len(got_prompt.sha256) == 64
    got_note = session.get(AgentNotebookEntry, note.id)
    assert got_note is not None and got_note.data == {"k": 1} and got_note.day == DAY
    got_journal = session.get(JournalEntry, journal.id)
    assert got_journal is not None and got_journal.tags == ["pullback", "commodity", "loss"]
    assert got_journal.data == {}
    got_score = session.get(AgentScore, score.id)
    assert got_score is not None
    assert got_score.hit_rate == Decimal("0.5833")
    assert got_score.pnl_attributed_usd == Decimal("123.45")
    assert got_score.calibration == {"0.6": [7, 12]}
    assert got_score.data == {}


def test_jsonb_and_tags_default_when_omitted(session: Session) -> None:
    report = ResearchReport(env="demo", agent=AGENT, kind="note", content_md="x")
    note = AgentNotebookEntry(env="demo", day=DAY, agent=AGENT, note_md="x")
    journal = JournalEntry(env="demo", agent=AGENT, title="t")
    score = AgentScore(agent=AGENT, env="live", day=DAY)
    session.add_all([report, note, journal, score])
    session.commit()
    session.expire_all()

    assert session.get(ResearchReport, report.id).data == {}  # type: ignore[union-attr]
    assert session.get(AgentNotebookEntry, note.id).data == {}  # type: ignore[union-attr]
    got_journal = session.get(JournalEntry, journal.id)
    assert got_journal is not None and got_journal.data == {} and got_journal.tags == []
    got_score = session.get(AgentScore, score.id)
    assert got_score is not None and got_score.calibration == {} and got_score.data == {}


def test_add_usage_accumulates_into_one_row(session: Session) -> None:
    add_usage(session, agent=AGENT, env="demo", day=DAY, tokens_in=100, tokens_out=10, turns=2, runs=1)
    row = add_usage(session, agent=AGENT, env="demo", day=DAY, tokens_in=50, tokens_out=5, turns=3, runs=1)
    add_usage(session, agent=AGENT, env="live", day=DAY, tokens_in=7)
    session.commit()

    rows = session.scalars(
        select(AgentUsageDaily).where(AgentUsageDaily.agent == AGENT, AgentUsageDaily.env == "demo")
    ).all()
    assert len(rows) == 1
    assert (rows[0].tokens_in, rows[0].tokens_out, rows[0].turns, rows[0].runs) == (150, 15, 5, 2)
    assert row.tokens_in == 150
    live = session.scalars(
        select(AgentUsageDaily).where(AgentUsageDaily.agent == AGENT, AgentUsageDaily.env == "live")
    ).one()
    assert live.tokens_in == 7


def test_usage_day_is_the_baghdad_date() -> None:
    # 22:30 UTC is 01:30 the next day in Baghdad (UTC+3).
    assert usage_day(datetime(2026, 10, 29, 22, 30, tzinfo=UTC)) == date(2026, 10, 30)
    assert usage_day(datetime(2026, 10, 29, 20, 59, tzinfo=UTC)) == date(2026, 10, 29)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"version": 1, "sha256": "a" * 64}, {"version": 1, "sha256": "b" * 64}),
        ({"version": 1, "sha256": "a" * 64}, {"version": 2, "sha256": "a" * 64}),
    ],
)
def test_prompt_versions_unique_per_agent(
    session: Session, first: dict[str, object], second: dict[str, object]
) -> None:
    session.add(PromptVersion(agent=AGENT, body="x", **first))
    session.commit()
    session.add(PromptVersion(agent=AGENT, body="y", **second))
    with pytest.raises(IntegrityError):
        session.commit()


def test_agent_state_unique_per_agent_and_env(session: Session) -> None:
    session.add(AgentState(agent=AGENT, env="demo"))
    session.commit()
    session.add(AgentState(agent=AGENT, env="demo"))
    with pytest.raises(IntegrityError):
        session.commit()
