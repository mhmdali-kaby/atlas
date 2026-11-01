"""Daily scores of the Portfolio Manager, the Orchestrator and the Historian (ATL-197): golden days
with hand-computed metrics, the compliance alert, idempotent re-runs and the open-window skip.

Run `cd backend && php artisan test` first so the DB is migrated. Every test runs inside one
transaction that is rolled back.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents import scoring
from atlas_agents.agents import scores as hooks
from atlas_agents.pm.rules import RULE_ORDER
from atlas_agents.registry import discover_hooks
from atlas_agents.scorecards import historian, orchestrator
from atlas_agents.scorecards import portfolio_manager as pm
from atlas_agents.tools.framework import ToolContext
from atlas_shared.db import AgentScore, Candle, Cycle, JournalEntry, Proposal
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

D = Decimal
ENV = "demo"
RUN = uuid.uuid4().hex[:6].upper()
DAY = date(2031, 3, 10)  # Baghdad day under test
DAY_START = datetime(2031, 3, 9, 21, 0, tzinfo=UTC)  # 00:00 Baghdad on the 10th
DAY_END = datetime(2031, 3, 10, 21, 0, tzinfo=UTC)
T0 = datetime(2031, 3, 10, 5, 0, tzinfo=UTC)  # 08:00 Baghdad
AFTER = DAY_END + timedelta(minutes=15)  # the 00:15 job on the 11th
AGENTS = [pm.AGENT, orchestrator.AGENT, historian.AGENT]


def inst(name: str) -> str:
    return f"S{RUN}{name}-USDT-SWAP"


@pytest.fixture
def db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    conn = engine.connect()
    outer = conn.begin()
    factory = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    window = {"s": DAY_START - timedelta(days=2), "e": DAY_END + timedelta(days=4), "env": ENV}
    try:
        with factory() as s:
            s.execute(delete(AgentScore).where(AgentScore.agent.in_(AGENTS)))
            s.execute(
                text("DELETE FROM journal_entries WHERE env = :env AND agent = 'portfolio_manager'"), window
            )
            s.execute(
                text("DELETE FROM proposals WHERE env = :env AND created_at >= :s AND created_at < :e"),
                window,
            )
            s.execute(
                text("DELETE FROM cycles WHERE env = :env AND started_at >= :s AND started_at < :e"), window
            )
            s.execute(
                text(
                    "DELETE FROM warroom_messages WHERE env = :env AND created_at >= :s AND created_at < :e"
                ),
                window,
            )
            s.commit()
        yield factory
    finally:
        outer.rollback()
        conn.close()


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    def __call__(self, stream: str, envelope: EventEnvelope) -> None:
        self.events.append((stream, envelope))

    def kinds(self, kind: str) -> list[EventEnvelope]:
        return [e for _, e in self.events if e.type == "warroom.message" and e.payload.get("kind") == kind]


def _ctx(
    factory: sessionmaker[Session], now: datetime, agent: str, rec: Recorder | None = None
) -> ToolContext:
    return ToolContext(
        env=ENV, session_factory=factory, agent=agent, clock=lambda: now, publish=rec or Recorder()
    )


def _rows(factory: sessionmaker[Session], agent: str) -> list[AgentScore]:
    with factory() as s:
        return list(s.scalars(select(AgentScore).where(AgentScore.agent == agent, AgentScore.env == ENV)))


def _thread(s: Session) -> uuid.UUID:
    key = f"atl197-{RUN}"
    thread = s.execute(
        text("SELECT id FROM warroom_threads WHERE env = :e AND key = :k"), {"e": ENV, "k": key}
    ).scalar()
    if thread is None:
        thread = uuid.uuid4()
        s.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, :env, 'agent', :key, 't', now(), now())"
            ),
            {"id": thread, "env": ENV, "key": key},
        )
    return uuid.UUID(str(thread))


def _message(
    s: Session,
    *,
    author: str,
    at: datetime,
    author_type: str = "agent",
    kind: str = "text",
    card: dict[str, Any] | None = None,
    reply_to: uuid.UUID | None = None,
) -> uuid.UUID:
    mid = uuid.uuid4()
    s.execute(
        text(
            "INSERT INTO warroom_messages (id, env, thread_id, author_type, author, kind, content_md, card, "
            "reply_to, created_at, updated_at) "
            "VALUES (:id, :env, :t, :at, :a, :k, 'x', CAST(:card AS jsonb), :r, :ts, :ts)"
        ),
        {
            "id": mid,
            "env": ENV,
            "t": _thread(s),
            "at": author_type,
            "a": author,
            "k": kind,
            "card": json.dumps(card) if card is not None else None,
            "r": reply_to,
            "ts": at,
        },
    )
    return mid


# -- Portfolio Manager ------------------------------------------------------------------------------


def _trade(
    s: Session, name: str, outcome: str, realised_r: str | None, mfe_r: str | None, closed_at: datetime
) -> None:
    s.add(
        JournalEntry(
            env=ENV,
            agent=pm.AGENT,
            inst_id=inst(name),
            proposal_id=None,
            title=f"{name} {outcome}",
            data={
                "position_ref": f"{name}:{closed_at.isoformat()}",
                "outcome": outcome,
                "actual": {"realised_r": realised_r, "mfe_r": mfe_r, "closed_at": closed_at.isoformat()},
            },
            tags=[outcome],
            created_at=closed_at + timedelta(minutes=5),
            updated_at=closed_at + timedelta(minutes=5),
        )
    )


def _entry(s: Session, name: str, side: str, stop: str, at: datetime) -> uuid.UUID:
    pid = uuid.uuid4()
    s.add(
        Proposal(
            id=pid,
            env=ENV,
            kind="entry",
            author="strategist",
            inst_id=inst(name),
            side=side,
            order_type="limit",
            entry=D("100"),
            stop=D(stop),
            take_profits=[{"price": "110", "pct": "100"}],
            expected_r=D("2"),
            probability=D("0.55"),
            status="filled",
            created_at=at,
            updated_at=at,
        )
    )
    s.flush()
    return pid


def _pm_action(
    s: Session,
    name: str,
    kind: str,
    rule: str,
    at: datetime,
    *,
    stop: str | None = None,
    entry_id: uuid.UUID | None = None,
    status: str = "sent",
) -> uuid.UUID:
    pid = uuid.uuid4()
    s.add(
        Proposal(
            id=pid,
            env=ENV,
            kind=kind,
            author=pm.AGENT,
            inst_id=inst(name),
            stop=D(stop) if stop else None,
            close_pct=D("50") if kind == "partial_close" else None,
            invalidation=rule,
            rationale_md=rule,
            evidence=[{"type": "proposal", "id": str(entry_id)}] if entry_id else [],
            status=status,
            created_at=at,
            updated_at=at,
        )
    )
    s.flush()
    return pid


def _seed_trades(s: Session) -> None:
    _trade(s, "A", "win", "1.5", "2.0", T0)
    _trade(s, "B", "loss", "-1.0", "0.5", T0 + timedelta(hours=1))
    _trade(s, "C", "loss", "-1.3", "0", T0 + timedelta(hours=2))  # no MFE: out of r_captured
    _trade(s, "E", "scratch", "0.05", "0.5", T0 + timedelta(hours=3))
    _trade(s, "F", "loss", "-1.1", "0.2", T0 + timedelta(hours=4))  # exactly the limit: within
    _trade(s, "G", "win", "3", "3", DAY_END + timedelta(minutes=1))  # next day: not counted


def test_pm_golden_day_r_captured_and_losers_within(db: sessionmaker[Session]) -> None:
    with db() as s:
        _seed_trades(s)
        s.commit()
    rec = Recorder()
    data = pm.score_day(_ctx(db, AFTER, pm.AGENT, rec), DAY)
    assert data is not None
    # R captured: trades with mfe > 0 are A, B, E, F: (1.5 - 1.0 + 0.05 - 1.1) / (2 + 0.5 + 0.5 + 0.2)
    assert data["r_captured"] == pytest.approx(-0.55 / 3.2)
    assert data["r_captured_n"] == 4
    assert data["avg_realised_r"] == pytest.approx(-0.55 / 4)
    assert data["avg_mfe_r"] == pytest.approx(3.2 / 4)
    # Losers B (-1.0), C (-1.3), F (-1.1): B and F within 1.1R.
    assert (data["losers_n"], data["losers_within_n"]) == (3, 2)
    assert data["losers_within_rate"] == pytest.approx(2 / 3)
    assert data["compliance_rate"] is None and data["compliance_n"] == 0
    (row,) = _rows(db, pm.AGENT)
    assert row.day == DAY and row.n == 5
    assert row.hit_rate == D("0.6667") and row.brier_score is None
    assert rec.kinds("system_alert") == []


def _seed_actions(s: Session, *, widen: bool) -> None:
    entry = _entry(s, "L", "buy", "95", T0 - timedelta(hours=6))
    _pm_action(s, "L", "amend_stop", "breakeven", T0, stop="100.1", entry_id=entry)
    # A rejected amend never moved the stop: it is not "the stop before".
    _pm_action(s, "L", "amend_stop", "tp1_trail", T0 + timedelta(minutes=30), stop="104", status="rejected")
    _pm_action(s, "L", "amend_stop", "two_r_trail", T0 + timedelta(hours=1), stop="99" if widen else "102")
    _pm_action(s, "L", "close", "time_stop", T0 + timedelta(hours=2), entry_id=entry)
    short = _entry(s, "S", "sell", "105", T0 - timedelta(hours=3))
    _pm_action(s, "S", "partial_close", "event_reduce:abc", T0 + timedelta(hours=1), entry_id=short)
    _pm_action(s, "S", "amend_stop", "breakeven", T0 + timedelta(hours=2), stop="99.9", entry_id=short)


def test_a_clean_day_is_fully_compliant_and_posts_nothing(db: sessionmaker[Session]) -> None:
    with db() as s:
        _seed_actions(s, widen=False)
        s.commit()
    rec = Recorder()
    data = pm.score_day(_ctx(db, AFTER, pm.AGENT, rec), DAY)
    assert data is not None
    assert data["compliance_rate"] == 1.0 and data["compliance_n"] == 6
    assert data["violations"] == []
    assert "compliance_alert" not in data
    assert rec.events == []
    (row,) = _rows(db, pm.AGENT)
    assert row.n == 0 and row.hit_rate is None


def test_a_widened_stop_drops_compliance_and_posts_one_system_alert(db: sessionmaker[Session]) -> None:
    with db() as s:
        _seed_actions(s, widen=True)
        s.commit()
    rec = Recorder()
    data = pm.score_day(_ctx(db, AFTER, pm.AGENT, rec), DAY)
    assert data is not None
    assert data["compliance_rate"] == pytest.approx(5 / 6)
    (violation,) = data["violations"]
    assert violation["rule"] == "two_r_trail" and violation["previous_stop"] == "100.1"
    alerts = rec.kinds("system_alert")
    assert len(alerts) == 1
    assert alerts[0].payload["card"]["code"] == pm.ALERT_CODE
    assert alerts[0].payload["author"] == pm.AGENT
    assert len([e for stream, e in rec.events if stream == "atlas.system"]) == 1
    # Re-running the same day posts nothing more and keeps one row.
    again = Recorder()
    pm.score_day(_ctx(db, AFTER + timedelta(days=1), pm.AGENT, again), DAY)
    assert again.events == []
    (row,) = _rows(db, pm.AGENT)
    assert row.data["compliance_alert"]["proposal_ids"] == [violation["proposal_id"]]


def test_an_unknown_rule_or_amend_targets_is_a_violation() -> None:
    def action(kind: str, rule: str) -> pm.PmAction:
        return pm.PmAction("p", "X", kind, rule, D("1") if kind == "amend_stop" else None, "long", D("0.5"))

    assert pm.check_action(action("close", "time_stop"))[0] == "ok"
    assert pm.check_action(action("close", "made_up"))[0] == "violation"
    assert pm.check_action(action("amend_targets", "breakeven"))[0] == "violation"
    assert pm.check_action(action("amend_stop", "time_stop"))[0] == "violation"
    unverified = pm.PmAction("p", "X", "amend_stop", "breakeven", D("1"), None, None)
    assert pm.check_action(unverified)[0] == "unverified"
    # Every rule the scorer accepts is one pm.rules emits.
    assert {r for rules in pm.RULE_KINDS.values() for r in rules} <= set(RULE_ORDER)


# -- Orchestrator -----------------------------------------------------------------------------------


def _cycle(
    s: Session, start: datetime, closed: datetime | None, deadlines: dict[str, str] | None = None
) -> None:
    s.add(
        Cycle(
            env=ENV,
            kind="scheduled",
            state="cycle_closed" if closed else "voting",
            started_at=start,
            closed_at=closed,
            deadlines=deadlines or {},
            created_at=start,
            updated_at=closed or start,
        )
    )


def _seed_orchestrator(s: Session) -> None:
    m = timedelta(minutes=1)
    # Budget = 300 + 900 + 120 + 180 + 30 s = 25.5 min, grace 2 min.
    _cycle(s, T0 + 10 * m, T0 + 30 * m)  # deadline 05:37:30 -> on time
    _cycle(s, T0 + 40 * m, T0 + 81 * m, {"vote": (T0 + 80 * m).isoformat()})  # deadline 06:22 -> on time
    _cycle(s, T0 + 70 * m, T0 + 105 * m)  # deadline 06:37:30 -> late
    _cycle(s, T0 + 120 * m, None)  # never closed, deadline long past -> late
    _cycle(s, DAY_END - 10 * m, None)  # 23:50 Baghdad, deadline 21:17:30 -> pending at 21:15
    sec = timedelta(seconds=1)
    for offset, replies in ((0, [4]), (60, [10, 30]), (120, [20]), (180, [2]), (240, [])):
        asked = T0 + timedelta(minutes=offset)
        owner = _message(s, author="owner", author_type="human", at=asked)
        for k, delay in enumerate(replies):
            _message(s, author=orchestrator.AGENT, at=asked + delay * sec, reply_to=owner)
            if k == 0:
                _message(s, author="historian", at=asked + sec, reply_to=owner)  # not the Orchestrator
    for k in range(2):
        _message(s, author=orchestrator.AGENT, at=T0 + timedelta(hours=k + 1), card={"type": "loop_summary"})
    _message(s, author="strategist", at=T0, card={"type": "loop_summary"})


def test_orchestrator_golden_day(db: sessionmaker[Session]) -> None:
    with db() as s:
        _seed_orchestrator(s)
        # The live counter's row for the day: other keys are kept, the ack figures are recomputed.
        scoring.upsert_score(
            s,
            agent=orchestrator.AGENT,
            env=ENV,
            day=DAY,
            n=1,
            hit_rate=1.0,
            data={"ack_latency_seconds": [1.0], "loops_stopped": 9, "note": "kept"},
            now=T0,
        )
        s.commit()
    data = orchestrator.score_day(_ctx(db, AFTER, orchestrator.AGENT), DAY)
    assert data is not None
    assert (data["cycles_on_time"], data["cycles_late"], data["cycles_pending"]) == (2, 2, 1)
    assert data["on_time_rate"] == 0.5
    # Latencies 4, 10, 20, 2 s: median 7, p90 = 10 + 0.7 x (20 - 10) = 17.
    assert sorted(data["ack_latency_seconds"]) == [2.0, 4.0, 10.0, 20.0]
    assert data["ack_median_seconds"] == pytest.approx(7.0)
    assert data["ack_p90_seconds"] == pytest.approx(17.0)
    assert data["ack_unanswered"] == 1
    assert data["loops_stopped"] == 2
    assert data["note"] == "kept"
    (row,) = _rows(db, orchestrator.AGENT)
    assert row.n == 4 and row.hit_rate == D("0.5000")


def test_cycle_deadline_pure() -> None:
    budget, grace = timedelta(minutes=25), timedelta(minutes=2)
    c = orchestrator.CycleRow("c", "scheduled", T0, T0 + timedelta(minutes=27), {})
    assert orchestrator.cycle_status(c, AFTER, budget, grace) == "on_time"
    late = orchestrator.CycleRow("c", "scheduled", T0, T0 + timedelta(minutes=27, seconds=1), {})
    assert orchestrator.cycle_status(late, AFTER, budget, grace) == "late"
    assert orchestrator.cycle_budget() == timedelta(seconds=1530)


# -- Historian --------------------------------------------------------------------------------------

M5 = timedelta(minutes=5)


def _close(s: Session, inst_id: str, at: datetime, close: float) -> None:
    """A 5m bar closing at ``at`` with close ``close``."""
    c = D(str(close))
    s.add(
        Candle(env=ENV, inst_id=inst_id, bar="5m", ts=at - M5, o=c, h=c, l=c, c=c, vol=D(1), confirmed=True)
    )


def _verdict(inst_id: str | None, verdict: str, pcts: dict[str, float]) -> dict[str, Any]:
    return {
        "type": "history_verdict",
        "question": "q",
        "inst_id": inst_id,
        "bar": "1H",
        "period": None,
        "n": 40,
        "distribution": {h: {"median": 0, "p10": -1, "p90": 1, "pct_positive": p} for h, p in pcts.items()},
        "verdict": verdict,
        "key_stat": "k",
        "data_as_of": T0.isoformat(),
    }


def _seed_historian(s: Session) -> None:
    x = inst("H")
    for at, close in (
        (T0, 100),
        (T0 + timedelta(hours=1), 101),  # 1h up
        (T0 + timedelta(hours=4), 99),  # 4h down
        (T0 + timedelta(hours=24), 98),  # 24h down
        (T0 + timedelta(hours=72), 100),  # 72h flat: did not happen
    ):
        _close(s, x, at, close)
    card = _verdict(x, "supports", {"1h": 60, "4h": 70, "24h": 40, "72h": 55})
    _message(s, author=historian.AGENT, at=T0, kind="research_report", card=card)
    _message(s, author=historian.AGENT, at=T0, kind="research_report", card=_verdict(None, "contradicts", {}))


HIST_AFTER = DAY_END + timedelta(hours=72, minutes=15)


def test_historian_golden_brier(db: sessionmaker[Session]) -> None:
    with db() as s:
        _seed_historian(s)
        s.commit()
    data = historian.score_day(_ctx(db, HIST_AFTER, historian.AGENT), DAY)
    assert data is not None
    # (0.6-1)^2 + (0.7-0)^2 + (0.4-0)^2 + (0.55-0)^2 = 0.16 + 0.49 + 0.16 + 0.3025 = 1.1125, / 4
    assert data["brier_score"] == pytest.approx(0.278125)
    assert data["brier_n"] == 4
    assert data["mean_p"] == pytest.approx(0.5625)
    assert data["observed"] == pytest.approx(0.25)
    # Direction: 1h right, 4h wrong, 24h right (p < 0.5, fell), 72h wrong.
    assert data["direction_hit_rate"] == 0.5
    assert data["verdict_mix"] == {
        "supports": 1,
        "weakly_supports": 0,
        "does_not_support": 0,
        "contradicts": 1,
    }
    assert data["metrics"]["mean_p"] == data["mean_p"] and data["metrics"]["observed"] == data["observed"]
    (row,) = _rows(db, historian.AGENT)
    assert row.n == 4 and row.brier_score == D("0.278125") and row.hit_rate == D("0.5000")


def test_historian_day_is_skipped_until_the_longest_horizon_closes(db: sessionmaker[Session]) -> None:
    with db() as s:
        _seed_historian(s)
        s.commit()
    assert historian.score_day(_ctx(db, HIST_AFTER - timedelta(hours=1), historian.AGENT), DAY) is None
    assert _rows(db, historian.AGENT) == []


def test_running_every_scorer_twice_leaves_one_row_each(db: sessionmaker[Session]) -> None:
    with db() as s:
        _seed_trades(s)
        _seed_actions(s, widen=False)
        _seed_orchestrator(s)
        _seed_historian(s)
        s.commit()
    today = DAY + timedelta(days=4)
    now = HIST_AFTER
    for _ in range(2):
        scoring.score_days(_ctx(db, now, pm.AGENT), pm.AGENT, today=DAY + timedelta(days=1))
        scoring.score_days(
            _ctx(db, now, orchestrator.AGENT), orchestrator.AGENT, today=DAY + timedelta(days=1)
        )
        historian.score_days(_ctx(db, now, historian.AGENT), today)
    with db() as s:
        counts = dict(
            s.execute(
                select(AgentScore.agent, func.count())
                .where(AgentScore.agent.in_(AGENTS), AgentScore.env == ENV)
                .group_by(AgentScore.agent)
            ).all()
        )
    assert counts == {pm.AGENT: 1, orchestrator.AGENT: 1, historian.AGENT: 1}


def test_scoring_hooks_are_registered_at_0015() -> None:
    found = discover_hooks()
    for slug in AGENTS:
        (hook,) = [h for h in found[slug].schedule if h.name == "scoring"]
        assert hook.time_of_day().isoformat() == "00:15:00"
    assert pm.AGENT in scoring.SCORERS and orchestrator.AGENT in scoring.SCORERS
    assert hooks.score_portfolio_manager and hooks.score_orchestrator and hooks.score_historian
    assert int(historian.config()["lookback_days"]) * 24 > 72
