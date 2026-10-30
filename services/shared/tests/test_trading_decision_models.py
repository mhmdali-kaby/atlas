"""Phase 2 decision tables (ATL-124) through the SQLAlchemy models: cycles, proposals, votes,
risk_checks. Money, prices and sizes come back as Decimal.

Needs the checkout's test DB migrated: run `cd backend && php artisan test` first.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from atlas_shared import testing
from atlas_shared.db import Cycle, Proposal, RiskCheck, Vote, make_engine
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

TS = datetime(2026, 10, 30, 5, 10, tzinfo=UTC)
AUTHOR = f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def engine() -> Engine:
    return make_engine(testing.db_settings())


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s
        s.rollback()
        ids = select(Proposal.id).where(Proposal.author == AUTHOR)
        cycle_ids = select(Proposal.cycle_id).where(Proposal.author == AUTHOR)
        cycles = [c for c in s.scalars(cycle_ids) if c is not None]
        s.execute(delete(RiskCheck).where(RiskCheck.proposal_id.in_(ids)))
        s.execute(delete(Vote).where(Vote.proposal_id.in_(ids)))
        s.execute(delete(Proposal).where(Proposal.author == AUTHOR, Proposal.parent_id.is_not(None)))
        s.execute(delete(Proposal).where(Proposal.author == AUTHOR))
        s.execute(delete(Cycle).where(Cycle.id.in_(cycles)))
        s.commit()


def _proposal(cycle: Cycle | None = None, **overrides: object) -> Proposal:
    values: dict[str, object] = {
        "env": "demo",
        "cycle_id": cycle.id if cycle else None,
        "author": AUTHOR,
        "inst_id": "XAU-USDT-SWAP",
        "side": "buy",
        "order_type": "limit",
        "entry": Decimal("4012.35"),
        "entry_zone": ["4010.00", "4015.00"],
        "stop": Decimal("3988.10"),
        "take_profits": [{"price": "4060.00", "pct": "50"}, {"price": "4100.00", "pct": "50"}],
        "time_stop_minutes": 1440,
        "size_suggestion_pct": Decimal("2.5"),
        "leverage_suggestion": 3,
        "expected_r": Decimal("2.10"),
        "probability": Decimal("0.5500"),
        "rationale_md": "Pullback to support.",
        "invalidation": "1H close below 3990.",
        "evidence": [{"type": "research_report", "id": str(uuid.uuid4())}],
    }
    values.update(overrides)
    return Proposal(**values)


def test_one_row_of_each_table_round_trips_with_decimal_money(session: Session) -> None:
    cycle = Cycle(env="demo", kind="scheduled", started_at=TS, deadlines={"research": "2026-10-30T05:15:00Z"})
    session.add(cycle)
    session.flush()
    proposal = _proposal(cycle)
    session.add(proposal)
    session.flush()
    session.add(
        Vote(env="demo", proposal_id=proposal.id, agent="historian", vote="take", confidence=Decimal("0.7"))
    )
    session.add(
        RiskCheck(
            env="demo",
            proposal_id=proposal.id,
            settings_version=4,
            result="resized",
            checks=[{"check": "position_size", "passed": True, "detail": "capped by max_usdt_per_trade"}],
            final_size=Decimal("0.25"),
            final_notional_usd=Decimal("1003.0875"),
            leverage=3,
            reason="capped by max_usdt_per_trade",
            decision="awaiting_owner",
        )
    )
    session.commit()
    session.expire_all()

    p = session.scalars(select(Proposal).where(Proposal.id == proposal.id)).one()
    assert p.status == "draft"
    assert p.kind == "entry"
    assert p.version == 1
    assert p.parent_id is None
    assert p.cycle_id == cycle.id
    assert isinstance(p.entry, Decimal) and p.entry == Decimal("4012.35")
    assert isinstance(p.stop, Decimal) and p.stop == Decimal("3988.10")
    assert p.size_suggestion_pct == Decimal("2.5")
    assert p.probability == Decimal("0.55")
    assert p.entry_zone == ["4010.00", "4015.00"]

    c = session.scalars(select(Cycle).where(Cycle.id == cycle.id)).one()
    assert c.state == "cycle_start"
    assert c.closed_at is None
    assert c.deadlines == {"research": "2026-10-30T05:15:00Z"}

    v = session.scalars(select(Vote).where(Vote.proposal_id == proposal.id)).one()
    assert v.vote == "take" and v.confidence == Decimal("0.7")
    assert v.reason == ""

    r = session.scalars(select(RiskCheck).where(RiskCheck.proposal_id == proposal.id)).one()
    assert isinstance(r.final_size, Decimal) and r.final_size == Decimal("0.25")
    assert isinstance(r.final_notional_usd, Decimal) and r.final_notional_usd == Decimal("1003.0875")
    assert r.decision == "awaiting_owner"
    assert r.checks[0]["check"] == "position_size"


def test_a_revision_points_at_its_parent(session: Session) -> None:
    parent = _proposal()
    session.add(parent)
    session.flush()
    child = _proposal(version=2, parent_id=parent.id, stop=Decimal("3990.00"))
    session.add(child)
    session.commit()

    assert session.scalars(select(Proposal.parent_id).where(Proposal.id == child.id)).one() == parent.id


@pytest.mark.parametrize(
    "overrides",
    [{"status": "approved_by_chat"}, {"stop": None}, {"kind": "withdraw"}, {"probability": Decimal("1.5")}],
)
def test_the_database_refuses_invalid_proposals(session: Session, overrides: dict[str, object]) -> None:
    session.add(_proposal(**overrides))
    with pytest.raises(IntegrityError):
        session.flush()
