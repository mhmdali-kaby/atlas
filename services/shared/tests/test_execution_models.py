"""Executor tables (ATL-141) through the SQLAlchemy models: orders, algo_orders, fills,
trading_state. Sizes and prices come back as Decimal.

Needs the checkout's test DB migrated: run `cd backend && php artisan test` first.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from atlas_shared import testing
from atlas_shared.db import AlgoOrder, Fill, Order, TradingState, make_engine
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

INST = f"T{uuid.uuid4().hex[:8].upper()}-USDT-SWAP"


@pytest.fixture(scope="module")
def engine() -> Engine:
    return make_engine(testing.db_settings())


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s
        s.rollback()
        s.execute(delete(Fill).where(Fill.inst_id == INST))
        s.execute(delete(AlgoOrder).where(AlgoOrder.inst_id == INST))
        s.execute(delete(Order).where(Order.inst_id == INST))
        s.commit()


def _order(**overrides: object) -> Order:
    values: dict[str, object] = {
        "env": "demo",
        "cl_ord_id": "atl" + uuid.uuid4().hex[:20],
        "inst_id": INST,
        "side": "buy",
        "pos_side": "net",
        "ord_type": "limit",
        "td_mode": "isolated",
        "px": Decimal("68000.5"),
        "sz": Decimal("0.12"),
        "origin": "external",
        "purpose": "protect",
    }
    values.update(overrides)
    return Order(**values)


def test_order_round_trips_decimals_and_defaults(session: Session) -> None:
    order = _order(sz=Decimal("0.000000000000000001"))
    session.add(order)
    session.commit()

    row = session.scalars(select(Order).where(Order.id == order.id)).one()
    assert row.sz == Decimal("0.000000000000000001")
    assert row.filled_sz == Decimal(0)
    assert row.state == "new"
    assert row.reduce_only is False
    assert row.raw == {}


def test_duplicate_cl_ord_id_is_refused(session: Session) -> None:
    cl_ord_id = "atldup" + uuid.uuid4().hex[:12]
    session.add(_order(cl_ord_id=cl_ord_id))
    session.commit()
    session.add(_order(cl_ord_id=cl_ord_id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_atlas_entry_without_risk_check_is_refused(session: Session) -> None:
    session.add(_order(origin="atlas", purpose="entry"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_algo_order_and_fill_link_to_their_order(session: Session) -> None:
    order = _order(ord_id=uuid.uuid4().hex[:16])
    session.add(order)
    session.flush()
    session.add(
        AlgoOrder(
            env="demo",
            order_id=order.id,
            algo_cl_ord_id="atlsl" + uuid.uuid4().hex[:10],
            inst_id=INST,
            kind="sl",
            trigger_px=Decimal("66900"),
        )
    )
    session.add(
        Fill(
            env="demo",
            order_id=order.id,
            trade_id="1",
            ord_id=str(order.ord_id),
            inst_id=INST,
            side="buy",
            fill_px=Decimal("68000.5"),
            fill_sz=Decimal("0.12"),
            fee=Decimal("-0.408"),
            fee_ccy="USDT",
            ts=datetime(2026, 10, 30, 8, tzinfo=UTC),
        )
    )
    session.commit()

    algo = session.scalars(select(AlgoOrder).where(AlgoOrder.order_id == order.id)).one()
    fill = session.scalars(select(Fill).where(Fill.order_id == order.id)).one()
    assert algo.state == "new"
    assert algo.reduce_only is True
    assert algo.trigger_px == Decimal("66900")
    assert fill.fee == Decimal("-0.408")


def test_trading_state_model_maps_the_table(session: Session) -> None:
    rows = session.scalars(select(TradingState)).all()
    assert all(r.env in ("demo", "live") for r in rows)
