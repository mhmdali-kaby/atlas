"""trading_core.execution.order_store (ATL-142 review round 1): env-scoped matching, late pushes,
and client ids held by the other env."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_shared import market_data as md
from atlas_shared import testing
from atlas_shared.db import AlgoOrder, Fill, Order, make_engine
from sqlalchemy import Engine, delete, insert, select
from sqlalchemy.exc import OperationalError
from trading_core.execution import order_store

T0 = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(testing.db_settings())
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"test database is not reachable: {exc}")
    return eng


@pytest.fixture
def inst(engine: Engine) -> Iterator[str]:
    name = f"OS{uuid.uuid4().hex[:8].upper()}-USDT-SWAP"
    yield name
    with engine.begin() as conn:
        conn.execute(delete(Fill).where(Fill.inst_id == name))
        conn.execute(delete(AlgoOrder).where(AlgoOrder.inst_id == name))
        conn.execute(delete(Order).where(Order.inst_id == name))


def _row(
    inst: str, *, cl: str | None, ord_id: str, state: str, acc: str, at: datetime, **extra: str
) -> dict[str, Any]:
    return {
        "instType": "SWAP",
        "instId": inst,
        "ordId": ord_id,
        "clOrdId": cl or "",
        "px": "100",
        "sz": "1",
        "ordType": "limit",
        "side": "buy",
        "posSide": "net",
        "tdMode": "isolated",
        "accFillSz": acc,
        "avgPx": "100" if acc != "0" else "0",
        "state": state,
        "lever": "3",
        "fee": "0",
        "feeCcy": "USDT",
        "reduceOnly": "false",
        "cTime": str(md.to_ms(T0)),
        "uTime": str(md.to_ms(at)),
        **extra,
    }


def _external(engine: Engine, env: str, inst: str, cl: str, ord_id: str) -> Any:
    with engine.begin() as conn:
        return conn.execute(
            insert(Order)
            .values(
                env=env,
                cl_ord_id=cl,
                ord_id=ord_id,
                inst_id=inst,
                side="buy",
                pos_side="net",
                ord_type="limit",
                td_mode="isolated",
                px=Decimal("100"),
                sz=Decimal("1"),
                state="live",
                origin="external",
                purpose="entry",
                raw={},
                created_at=T0,
                updated_at=T0,
            )
            .returning(Order.id)
        ).scalar_one()


def test_matching_is_scoped_to_the_env_and_a_clashing_client_id_never_touches_the_other_env(
    engine: Engine, inst: str
) -> None:
    cl = f"x{uuid.uuid4().hex[:20]}"
    live_id = _external(engine, "live", inst, cl, "L-1")

    u = order_store.upsert_order(
        engine, "demo", _row(inst, cl=cl, ord_id="D-1", state="filled", acc="1", at=T0), T0
    )

    assert u is not None and u.origin == "external" and u.order_id != live_id
    with engine.connect() as conn:
        live = conn.execute(select(Order).where(Order.id == live_id)).mappings().one()
        demo = conn.execute(select(Order).where(Order.id == u.order_id)).mappings().one()
    assert live["state"] == "live" and live["ord_id"] == "L-1" and live["env"] == "live"
    assert demo["env"] == "demo" and demo["ord_id"] == "D-1" and demo["state"] == "filled"
    assert demo["cl_ord_id"] is None and demo["raw"]["clOrdId"] == cl  # kept in raw only

    # A later demo push for the same order matches the demo row by (env, ordId).
    again = order_store.upsert_order(
        engine,
        "demo",
        _row(inst, cl=cl, ord_id="D-1", state="filled", acc="1", at=T0 + timedelta(seconds=1)),
        T0,
    )
    assert again is not None and again.order_id == u.order_id


def test_a_late_push_does_not_move_state_or_filled_size_back_but_still_records_its_fill(
    engine: Engine, inst: str
) -> None:
    cl = f"x{uuid.uuid4().hex[:20]}"
    filled = _row(inst, cl=cl, ord_id="900", state="filled", acc="1", at=T0 + timedelta(seconds=2))
    first = order_store.upsert_order(engine, "demo", filled, T0)
    assert first is not None and first.state == "filled"

    late = _row(
        inst,
        cl=cl,
        ord_id="900",
        state="partially_filled",
        acc="0.4",
        at=T0 + timedelta(seconds=1),
        fillSz="0.4",
        fillPx="100",
        tradeId="T-late",
        fillTime=str(md.to_ms(T0 + timedelta(seconds=1))),
    )
    u = order_store.upsert_order(engine, "demo", late, T0 + timedelta(seconds=3))

    assert u is not None and u.late and not u.state_changed
    assert u.state == "filled" and u.filled_sz == Decimal("1")
    with engine.connect() as conn:
        row = conn.execute(select(Order).where(Order.id == first.order_id)).mappings().one()
        fills = conn.execute(select(Fill).where(Fill.inst_id == inst)).mappings().all()
    assert row["state"] == "filled" and row["filled_sz"] == Decimal("1")
    assert row["raw"]["uTime"] == filled["uTime"]
    assert [f["trade_id"] for f in fills] == ["T-late"]
    assert order_store.order_payload(u)["late"] is True


def test_algo_matching_is_env_scoped_and_late_pushes_are_ignored(engine: Engine, inst: str) -> None:
    algo_cl = f"a{uuid.uuid4().hex[:20]}"

    def algo(state: str, at: datetime, algo_id: str = "A-1") -> dict[str, Any]:
        return {
            "instType": "SWAP",
            "instId": inst,
            "algoId": algo_id,
            "algoClOrdId": algo_cl,
            "ordType": "conditional",
            "side": "sell",
            "posSide": "net",
            "sz": "1",
            "state": state,
            "slTriggerPx": "95",
            "slOrdPx": "-1",
            "reduceOnly": "true",
            "cTime": str(md.to_ms(T0)),
            "uTime": str(md.to_ms(at)),
        }

    live = order_store.upsert_algo(engine, "live", algo("live", T0, "LA-1"), T0)
    demo = order_store.upsert_algo(engine, "demo", algo("live", T0, "DA-1"), T0)
    assert live.algo_order_id != demo.algo_order_id and demo.origin == "external"

    order_store.upsert_algo(engine, "demo", algo("effective", T0 + timedelta(seconds=2), "DA-1"), T0)
    stale = order_store.upsert_algo(engine, "demo", algo("live", T0 + timedelta(seconds=1), "DA-1"), T0)
    assert stale.late and not stale.state_changed and stale.state == "effective"
    with engine.connect() as conn:
        rows = {
            r["env"]: r for r in conn.execute(select(AlgoOrder).where(AlgoOrder.inst_id == inst)).mappings()
        }
    assert rows["live"]["state"] == "live" and rows["live"]["algo_cl_ord_id"] == algo_cl
    assert rows["demo"]["state"] == "effective" and rows["demo"]["algo_cl_ord_id"] is None
