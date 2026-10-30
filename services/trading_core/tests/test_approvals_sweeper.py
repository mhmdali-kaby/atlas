"""ATL-162: the approval sweeper posts one reminder halfway through the window and rejects an
awaiting_owner proposal at approval_expires_at (reason approval_timeout), on a simulated clock.

Runs against this checkout's test DB (migrated by `cd backend && php artisan test`) and Redis DB
``REDIS_TEST_DB`` (default 15).
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import pytest
import redis
from atlas_shared import bus, testing
from atlas_shared.db import Proposal, make_engine
from sqlalchemy import Engine, delete, insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from trading_core.approvals import sweeper

ENV = "live"
# 12 April 2031, 10:00 UTC: a fixed simulated clock.
T0 = datetime(2031, 4, 12, 10, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=15)


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(testing.db_settings())
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"test DB is not reachable: {exc}")
    return eng


@pytest.fixture
def rds() -> Iterator[redis.Redis]:
    conn = bus.make_client({**os.environ, "REDIS_DB": os.environ.get("REDIS_TEST_DB", "15")})
    try:
        conn.ping()
    except redis.ConnectionError:
        pytest.fail("Redis is not running on REDIS_HOST:REDIS_PORT")
    conn.delete("atlas.proposals", "atlas.warroom")
    yield conn
    conn.delete("atlas.proposals", "atlas.warroom")
    conn.close()


@pytest.fixture
def proposal(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[uuid.UUID]:
    monkeypatch.setattr(sweeper, "_settings", lambda _engine, _env: {"approval_timeout_minutes": 15})
    inst_id = f"AP{uuid.uuid4().hex[:6].upper()}-USDT-SWAP"
    pid = uuid.uuid4()
    with engine.begin() as c:
        c.execute(
            insert(Proposal).values(
                id=pid,
                env=ENV,
                version=1,
                kind="entry",
                author="strategist",
                inst_id=inst_id,
                side="buy",
                order_type="limit",
                entry=D(4000),
                stop=D(3960),
                take_profits=[{"price": "4100", "pct": "100"}],
                size_suggestion_pct=D(5),
                leverage_suggestion=3,
                expected_r=D("2.5"),
                probability=D("0.5"),
                status="awaiting_owner",
                approval_expires_at=T0 + WINDOW,
                created_at=T0,
                updated_at=T0,
            )
        )
    yield pid
    with engine.begin() as c:
        c.execute(delete(Proposal).where(Proposal.id == pid))


def _events(rds: redis.Redis, stream: str, pid: uuid.UUID) -> list[dict[str, Any]]:
    out = []
    for _id, fields in rds.xrange(stream):
        env = json.loads(fields[b"envelope"] if b"envelope" in fields else fields["envelope"])
        payload = env["payload"]
        if payload.get("proposal_id") == str(pid) or (payload.get("card") or {}).get("proposal_id") == str(
            pid
        ):
            out.append(env)
    return out


def _row(engine: Engine, pid: uuid.UUID) -> Proposal:
    with Session(engine) as s:
        row = s.get(Proposal, pid)
        assert row is not None
        return row


def _sweep(engine: Engine, rds: redis.Redis, at: datetime) -> sweeper.SweepResult:
    return sweeper.sweep(ENV, engine=engine, redis_client=rds, now=lambda: at)


def test_reminder_at_halfway_then_reject_at_expiry(
    engine: Engine, rds: redis.Redis, proposal: uuid.UUID
) -> None:
    # Before halfway: nothing.
    assert str(proposal) not in _sweep(engine, rds, T0 + timedelta(minutes=7)).reminded
    assert _events(rds, "atlas.warroom", proposal) == []

    # Halfway (7m30s of 15m): one reminder, posted once.
    half = T0 + timedelta(minutes=7, seconds=30)
    assert str(proposal) in _sweep(engine, rds, half).reminded
    assert str(proposal) not in _sweep(engine, rds, half + timedelta(minutes=3)).reminded
    lines = _events(rds, "atlas.warroom", proposal)
    assert len(lines) == 1
    assert lines[0]["payload"]["author"] == "orchestrator"
    assert lines[0]["payload"]["card"]["type"] == "approval_reminder"
    assert "waiting for your approval" in lines[0]["payload"]["content_md"]
    reminders = [
        e for e in _events(rds, "atlas.proposals", proposal) if e["type"] == "proposal.approval_reminder"
    ]
    assert len(reminders) == 1
    row = _row(engine, proposal)
    assert row.status == "awaiting_owner" and row.approval_reminded_at == half

    # One second before expiry: still awaiting.
    assert str(proposal) not in _sweep(engine, rds, T0 + WINDOW - timedelta(seconds=1)).expired
    assert _row(engine, proposal).status == "awaiting_owner"

    # At approval_expires_at: rejected with reason approval_timeout.
    assert str(proposal) in _sweep(engine, rds, T0 + WINDOW).expired
    row = _row(engine, proposal)
    assert row.status == "rejected" and row.status_reason == "approval_timeout"
    assert row.decided_by == "system" and row.decided_at == T0 + WINDOW
    rejected = [e for e in _events(rds, "atlas.proposals", proposal) if e["type"] == "proposal.rejected"]
    assert len(rejected) == 1 and rejected[0]["payload"]["status_reason"] == "approval_timeout"
    assert [e["payload"]["card"]["type"] for e in _events(rds, "atlas.warroom", proposal)] == [
        "approval_reminder",
        "approval_timeout",
    ]

    # A later sweep does nothing more.
    later = _sweep(engine, rds, T0 + WINDOW + timedelta(minutes=5))
    assert str(proposal) not in later.expired and str(proposal) not in later.reminded


def test_a_proposal_the_owner_already_approved_is_never_expired(
    engine: Engine, rds: redis.Redis, proposal: uuid.UUID
) -> None:
    # The approval moved it to risk_checked (the re-check is running): the sweep must not touch it.
    with engine.begin() as c:
        c.execute(Proposal.__table__.update().where(Proposal.id == proposal).values(status="risk_checked"))
    result = _sweep(engine, rds, T0 + WINDOW + timedelta(minutes=1))
    assert str(proposal) not in result.expired
    assert _row(engine, proposal).status == "risk_checked"
