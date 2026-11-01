"""Daily scores of the Technical Analyst and the Derivatives & Sentiment Analyst (ATL-195): golden
days with hand-computed metrics, the ambiguous-bar rule, idempotent re-runs and the open-window skip.

Run `cd backend && php artisan test` first so the DB is migrated. Every test runs inside one
transaction that is rolled back.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
from atlas_agents import scoring
from atlas_agents.agents import scores as hooks
from atlas_agents.registry import discover_hooks
from atlas_agents.scorecards import derivatives, technical
from atlas_agents.tools.framework import ToolContext
from atlas_shared.db import AgentScore, Candle
from atlas_shared.positioning import PositioningReport
from atlas_shared.technical import TechnicalSetup
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

ENV = "demo"
RUN = uuid.uuid4().hex[:6].upper()
DAY = date(2026, 10, 20)  # Baghdad day of the cards
T0 = datetime(2026, 10, 20, 5, 0, tzinfo=UTC)  # 08:00 Baghdad: every card's created_at
DAY_END = datetime(2026, 10, 20, 21, 0, tzinfo=UTC)  # 00:00 Baghdad on the 21st
TA_AFTER = DAY_END + timedelta(hours=24, minutes=15)  # TA windows (24h) closed
DERIV_AFTER = DAY_END + timedelta(hours=48, minutes=15)  # derivatives windows (48h) closed
AGENTS = [technical.AGENT, derivatives.AGENT]
Bar = tuple[float, float, float, float]  # o, h, l, c


def inst(name: str) -> str:
    return f"S{RUN}{name}-USDT-SWAP"


@pytest.fixture
def db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    conn = engine.connect()
    outer = conn.begin()
    factory = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    try:
        with factory() as s:
            s.execute(delete(AgentScore).where(AgentScore.agent.in_(AGENTS)))
            s.execute(
                text("DELETE FROM warroom_messages WHERE author = ANY(:a) AND kind = 'research_report'"),
                {"a": AGENTS},
            )
            s.commit()
        yield factory
    finally:
        outer.rollback()
        conn.close()


def _ctx(factory: sessionmaker[Session], now: datetime, agent: str) -> ToolContext:
    return ToolContext(env=ENV, session_factory=factory, agent=agent, clock=lambda: now)


def _candle(inst_id: str, bar: str, ts: datetime, ohlc: Bar) -> Candle:
    o, h, lo, c = (Decimal(str(x)) for x in ohlc)
    return Candle(
        env=ENV, inst_id=inst_id, bar=bar, ts=ts, o=o, h=h, l=lo, c=c, vol=Decimal(1), confirmed=True
    )


def _flat_history(s: Session, inst_id: str, bar: str, step: timedelta, count: int = 20) -> None:
    """``count`` bars closing by T0 at 100 with high 101 and low 99, so Wilder ATR = 2 exactly."""
    for k in range(count, 0, -1):
        s.add(_candle(inst_id, bar, T0 - step * k, (100, 101, 99, 100)))


def _path(s: Session, inst_id: str, bar: str, step: timedelta, bars: Sequence[tuple[int, Bar]]) -> None:
    """Bars opening at T0 + k x step."""
    for k, ohlc in bars:
        s.add(_candle(inst_id, bar, T0 + step * k, ohlc))


def _post(s: Session, author: str, card: dict[str, Any], at: datetime = T0) -> None:
    thread = s.execute(
        text("SELECT id FROM warroom_threads WHERE env = :e AND key = :k"), {"e": ENV, "k": f"atl195-{RUN}"}
    ).scalar()
    if thread is None:
        thread = uuid.uuid4()
        s.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, :env, 'agent', :key, 't', now(), now())"
            ),
            {"id": thread, "env": ENV, "key": f"atl195-{RUN}"},
        )
    s.execute(
        text(
            "INSERT INTO warroom_messages (id, env, thread_id, author_type, author, kind, content_md, card, "
            "created_at, updated_at) VALUES (:id, :env, :t, 'agent', :a, 'research_report', 'x', "
            "CAST(:card AS jsonb), :ts, :ts)"
        ),
        {"id": uuid.uuid4(), "env": ENV, "t": thread, "a": author, "card": json.dumps(card), "ts": at},
    )


def _rows(factory: sessionmaker[Session], agent: str) -> list[AgentScore]:
    with factory() as s:
        return list(s.scalars(select(AgentScore).where(AgentScore.agent == agent, AgentScore.env == ENV)))


# -- Technical Analyst ------------------------------------------------------------------------------

M15 = timedelta(minutes=15)
H1 = timedelta(hours=1)


def _setup(
    inst_id: str,
    direction: str,
    zone: tuple[float, float] | None,
    stop: float | None,
    tp1: float | None,
    expected_r: float | None,
    levels: Sequence[float],
) -> dict[str, Any]:
    none = direction == "none"
    card = TechnicalSetup(
        inst_id=inst_id,
        setup_type="none" if none else ("breakout" if direction == "long" else "breakdown"),
        direction=direction,
        entry_zone=None if zone is None else (Decimal(str(zone[0])), Decimal(str(zone[1]))),
        stop=None if stop is None else Decimal(str(stop)),
        targets=[] if tp1 is None else [{"price": Decimal(str(tp1)), "reason": "next zone"}],
        expected_r=None if expected_r is None else Decimal(str(expected_r)),
        quality_score=0.6,
        levels_used=[{"id": f"1H:z{i}", "price": Decimal(str(p))} for i, p in enumerate(levels)],
        data_as_of=T0 - timedelta(minutes=1),
    )
    return card.model_dump(mode="json")


def _seed_ta_instrument(s: Session, name: str, path: Sequence[tuple[int, Bar]]) -> str:
    inst_id = inst(name)
    _flat_history(s, inst_id, "1H", H1)  # ATR(1H) = 2, so tol = 0.25 x 2 = 0.5
    _flat_history(s, inst_id, "15m", M15, count=2)  # ref = 100 (the 15m close at T0)
    _path(s, inst_id, "15m", M15, path)
    return inst_id


def _seed_ta_golden(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        # A: long, zone 99-100, stop 97, TP1 104. Bar 0 trades into the zone from 100.5 (fill 100);
        # bar 1 reaches 104.5 -> TP1, realised R = (104 - 100) / 3. Levels: 98 support held (min
        # close 100 >= 97.5), 104 resistance broken (close 105 > 104.5).
        a = _seed_ta_instrument(
            s,
            "A",
            [(0, (100.5, 101, 99.5, 100)), (1, (100, 104.5, 99.8, 104.2)), (2, (104.2, 105.2, 104, 105))],
        )
        _post(s, technical.AGENT, _setup(a, "long", (99, 100), 97, 104, 2.0, [98, 104]))
        # B: short, zone 100-101, stop 103, TP1 96. Bar 0 fills at 100; bar 1 high 103.5 -> stop,
        # R = -1. Level 103 resistance held (max close 103.2 <= 103.5).
        b = _seed_ta_instrument(s, "B", [(0, (100, 100.8, 99.6, 100.5)), (1, (100.5, 103.5, 100.2, 103.2))])
        _post(s, technical.AGENT, _setup(b, "short", (100, 101), 103, 96, 1.5, [103]))
        # C: long, zone 95-96: price never trades below 99 -> never triggered. Level 95 held.
        c = _seed_ta_instrument(s, "C", [(0, (100, 101, 99, 100.5)), (1, (100.5, 101.5, 99.5, 101))])
        _post(s, technical.AGENT, _setup(c, "long", (95, 96), 93, 100, 2.0, [95]))
        s.commit()


def test_ta_golden_day(db: sessionmaker[Session]) -> None:
    _seed_ta_golden(db)
    data = technical.score_day(_ctx(db, TA_AFTER, technical.AGENT), DAY)
    assert data is not None
    assert data["target_before_stop_rate"] == 0.5 and data["target_before_stop_n"] == 2
    assert data["target_hits"] == 1 and data["stop_hits"] == 1 and data["not_triggered_n"] == 1
    assert data["level_hold_rate"] == 0.75 and data["level_hold_n"] == 4
    assert data["avg_realised_r"] == pytest.approx((4 / 3 - 1) / 2)
    assert data["realised_r_n"] == 2
    assert data["avg_expected_r"] == pytest.approx(1.75) and data["expected_r_n"] == 2
    assert data["avg_r_gap"] == pytest.approx((4 / 3 - 1) / 2 - 1.75)
    assert data["no_setup_n"] == 0 and data["no_setup_accuracy"] is None
    by_inst = {r["inst_id"]: r for r in data["setups"]}
    assert by_inst[inst("A")]["fill"] == 100.0 and by_inst[inst("A")]["outcome"] == "target"
    assert by_inst[inst("B")]["outcome"] == "stop" and by_inst[inst("C")]["outcome"] == "not_triggered"
    (row,) = _rows(db, technical.AGENT)
    assert row.day == DAY and row.n == 2 and row.hit_rate == Decimal("0.5000") and row.brier_score is None


def test_ta_no_setup_accuracy(db: sessionmaker[Session]) -> None:
    with db() as s:
        # Range 98-102 (tol 0.5): D's closes stay inside, E closes at 102.8 (> 102.5).
        d = _seed_ta_instrument(s, "D", [(0, (100, 102.4, 97.6, 101)), (1, (101, 102, 99, 102.4))])
        _post(s, technical.AGENT, _setup(d, "none", None, None, None, None, [98, 102, 90]))
        e = _seed_ta_instrument(s, "E", [(0, (100, 101, 99, 100.5)), (1, (100.5, 103, 100, 102.8))])
        _post(s, technical.AGENT, _setup(e, "none", None, None, None, None, [98, 102]))
        s.commit()
    data = technical.score_day(_ctx(db, TA_AFTER, technical.AGENT), DAY)
    assert data is not None
    assert data["no_setup_accuracy"] == 0.5 and data["no_setup_n"] == 2
    assert {r["inst_id"]: r["range"] for r in data["setups"]}[inst("D")] == [98.0, 102.0]
    (row,) = _rows(db, technical.AGENT)
    assert row.n == 0 and row.hit_rate is None  # no setup triggered: no target-before-stop sample


def _bars(rows: Sequence[Bar]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([T0 + M15 * k for k in range(len(rows))])
    return pd.DataFrame(rows, columns=["o", "h", "l", "c"], index=idx)


def test_a_bar_touching_both_stop_and_target_is_a_stop() -> None:
    # Long, zone 99-100, stop 97, TP1 104: the second bar's range 96.5-104.5 covers both.
    walk = technical.walk_setup(
        _bars([(100, 100.5, 99.5, 100), (100, 104.5, 96.5, 100)]), "long", (99, 100), 97, 104
    )
    assert walk.outcome == "stop" and walk.realised_r == -1.0
    # Short mirror: zone 100-101, stop 103, TP1 96.
    walk = technical.walk_setup(
        _bars([(100, 100.5, 99.5, 100), (100, 103.5, 95.5, 100)]), "short", (100, 101), 103, 96
    )
    assert walk.outcome == "stop"
    # The trigger bar itself: a stop touch counts, a target touch does not (order unknown).
    assert technical.walk_setup(_bars([(101, 101, 96, 98)]), "long", (99, 100), 97, 104).outcome == "stop"
    assert technical.walk_setup(_bars([(105, 105, 99.5, 100)]), "long", (99, 100), 97, 104).outcome == "open"


def test_a_bar_touching_both_in_the_db_day_scores_a_stop(db: sessionmaker[Session]) -> None:
    with db() as s:
        a = _seed_ta_instrument(s, "F", [(0, (100.5, 101, 99.5, 100)), (1, (100, 104.5, 96.5, 100))])
        _post(s, technical.AGENT, _setup(a, "long", (99, 100), 97, 104, 2.0, []))
        s.commit()
    data = technical.score_day(_ctx(db, TA_AFTER, technical.AGENT), DAY)
    assert data is not None and data["stop_hits"] == 1 and data["target_before_stop_rate"] == 0.0


def test_level_hold_and_range_pure() -> None:
    assert technical.level_held([100, 98.6], 100, 99, 0.5) is True
    assert technical.level_held([100, 98.4], 100, 99, 0.5) is False
    assert technical.level_held([100, 101.6], 100, 101, 0.5) is False
    assert technical.level_held([100], 100, 100, 0.5) is None
    assert technical.stated_range(100, [90, 98, 102, 110]) == (98, 102)
    assert technical.stated_range(100, [98, 99]) is None


# -- Derivatives & Sentiment ------------------------------------------------------------------------


def _report(inst_id: str, supports: dict[str, str], squeeze: dict[str, float]) -> dict[str, Any]:
    return PositioningReport(
        inst_id=inst_id,
        regime="flat",
        crowding_score=0.5,
        crowded_side="none",
        squeeze_risk=squeeze,
        sentiment_score=0.0,
        supports_direction=supports,
        data_as_of=T0 - timedelta(minutes=1),
    ).model_dump(mode="json")


def _seed_deriv_instrument(s: Session, name: str, path: Sequence[tuple[int, Bar]]) -> str:
    inst_id = inst(name)
    _flat_history(s, inst_id, "1D", timedelta(days=1))  # ATR(1D) = 2, so a squeeze needs 4
    _flat_history(s, inst_id, "1H", H1, count=2)  # p0 = 100
    _path(s, inst_id, "1H", H1, path)
    return inst_id


def _seed_deriv_golden(factory: sessionmaker[Session]) -> None:
    with factory() as s:
        # X: p1 = 103 (up): long supports + short opposes = 2 correct up calls. High 105 >= 104 ->
        # short squeeze happened; low 99.5 > 96 -> long squeeze did not.
        x = _seed_deriv_instrument(s, "X", [(0, (100, 105, 99.5, 104)), (23, (103, 103.5, 102.5, 103))])
        _post(
            s,
            derivatives.AGENT,
            _report(x, {"long": "supports", "short": "opposes"}, {"short_squeeze": 0.7, "long_squeeze": 0.1}),
        )
        # Y: p1 = 102 (up): long opposes = a down call, wrong. Low 99 > 96 -> no long squeeze.
        y = _seed_deriv_instrument(s, "Y", [(0, (100, 101.5, 99, 101)), (23, (102, 102.5, 101.5, 102))])
        _post(
            s, derivatives.AGENT, _report(y, {"long": "opposes", "short": "neutral"}, {"long_squeeze": 0.8})
        )
        # Z: no direction call; high 104.5 at hour 5 -> the 0.3 short squeeze happened.
        z = _seed_deriv_instrument(s, "Z", [(5, (100, 104.5, 100, 104)), (23, (101, 101.5, 100.5, 101))])
        _post(
            s, derivatives.AGENT, _report(z, {"long": "neutral", "short": "neutral"}, {"short_squeeze": 0.3})
        )
        s.commit()


def test_derivatives_golden_day(db: sessionmaker[Session]) -> None:
    _seed_deriv_golden(db)
    data = derivatives.score_day(_ctx(db, DERIV_AFTER, derivatives.AGENT), DAY)
    assert data is not None
    assert data["supports_n"] == 3 and data["supports_correct"] == 2
    assert data["supports_accuracy"] == pytest.approx(2 / 3)
    assert data["squeeze_calls_n"] == 2 and data["squeeze_call_hits"] == 1
    assert data["squeeze_call_hit_rate"] == 0.5
    # (0.1-0)^2 + (0.7-1)^2 + (0.8-0)^2 + (0.3-1)^2 = 0.01 + 0.09 + 0.64 + 0.49 = 1.23, / 4
    assert data["brier_score"] == pytest.approx(0.3075) and data["brier_n"] == 4
    assert data["mean_p"] == pytest.approx(1.9 / 4)
    assert data["observed"] == 0.5
    (row,) = _rows(db, derivatives.AGENT)
    assert row.n == 3 and row.hit_rate == Decimal("0.6667") and row.brier_score == Decimal("0.307500")
    assert row.data["mean_p"] == pytest.approx(0.475) and row.data["observed"] == 0.5


def test_brier_pure() -> None:
    out = derivatives.brier([(0.9, True), (0.2, False), (0.6, False)])
    assert out["brier_score"] == pytest.approx((0.01 + 0.04 + 0.36) / 3)
    assert out["mean_p"] == pytest.approx(1.7 / 3) and out["observed"] == pytest.approx(1 / 3)
    assert derivatives.direction_calls({"long": "supports", "short": "supports"}) == [
        ("long", "supports", 1),
        ("short", "supports", -1),
    ]


# -- idempotency, open windows, registration ----------------------------------------------------------


def test_running_the_job_twice_leaves_one_row_per_agent_per_day(db: sessionmaker[Session]) -> None:
    _seed_ta_golden(db)
    _seed_deriv_golden(db)
    today = date(2026, 10, 23)  # lookback 3: the 20th, 21st and 22nd
    for _ in range(2):
        for agent in AGENTS:
            results = scoring.score_days(_ctx(db, DERIV_AFTER, agent), agent, today)
            assert results[DAY] is not None
    with db() as s:
        counts = s.execute(
            select(AgentScore.agent, AgentScore.day, func.count())
            .where(AgentScore.env == ENV, AgentScore.agent.in_(AGENTS))
            .group_by(AgentScore.agent, AgentScore.day)
        ).all()
    assert sorted(counts) == sorted([(technical.AGENT, DAY, 1), (derivatives.AGENT, DAY, 1)])


def test_a_day_whose_window_is_open_writes_nothing(db: sessionmaker[Session]) -> None:
    _seed_ta_golden(db)
    _seed_deriv_golden(db)
    assert technical.score_day(_ctx(db, TA_AFTER - timedelta(minutes=30), technical.AGENT), DAY) is None
    # 24h after the day the TA window has closed, but the 48h squeeze window has not.
    assert derivatives.score_day(_ctx(db, TA_AFTER, derivatives.AGENT), DAY) is None
    assert _rows(db, technical.AGENT) == [] and _rows(db, derivatives.AGENT) == []
    # ... and the later run scores it.
    assert derivatives.score_day(_ctx(db, DERIV_AFTER, derivatives.AGENT), DAY) is not None


def test_scoring_hooks_are_registered_at_0015_for_both_agents() -> None:
    found = discover_hooks()
    for slug in AGENTS:
        (hook,) = [h for h in found[slug].schedule if h.name == "scoring"]
        assert hook.time_of_day().isoformat() == "00:15:00"
        assert slug in scoring.SCORERS
    assert hooks.score_technical_analyst is not None and hooks.score_derivatives_sentiment is not None
