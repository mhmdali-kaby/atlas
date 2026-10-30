"""ATL-172: closed positions (account/positions-history) and fills (trade/fills-history) sync.

DB tests run against this checkout's test DB (decision #239; migrated by `cd backend && php artisan
test`). OKX is mocked with respx using the recorded account_positions_history fixture and
okx_trade/fills_history_swap.json; SPOT and FUTURES answer with no rows.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from atlas_shared import testing
from atlas_shared.db import (
    Fill,
    Order,
    PositionClosed,
    Proposal,
    RiskCheck,
    SyncStatus,
    make_engine,
)
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session
from trading_core.okx import OkxClient, OkxCredentials
from trading_core.sync.history import STREAM, sync_history

TESTS = Path(__file__).parent
BASE = "https://www.okx.com"
API = "/api/v5/"
ENV = "demo"
T0 = datetime(2026, 10, 30, 9, 0, 0, tzinfo=UTC)
ENTRY_ORD_ID = "2900000000000000001"
INSTS = ("ETH-USDT-SWAP", "BTC-USDT-SWAP")
AUTHOR = "atl172_strategist"
CREDS = OkxCredentials(api_key="test-key-0000", secret="atlas-test-only-okx-secret", passphrase="test-pass")  # noqa: S106


def _load(path: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return body


POSITIONS = _load(TESTS / "fixtures" / "okx" / "account_positions_history.json")
FILLS = _load(TESTS / "fixtures" / "okx_trade" / "fills_history_swap.json")
EMPTY: dict[str, Any] = {"code": "0", "msg": "", "data": []}


def clock(*stamps: datetime) -> Callable[[], datetime]:
    it = iter(stamps)
    return lambda: next(it)


@pytest.fixture(scope="module")
def engine() -> Engine:
    return make_engine(testing.db_settings())


def _wipe(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            delete(PositionClosed).where(PositionClosed.env == ENV, PositionClosed.inst_id.in_(INSTS))
        )
        conn.execute(delete(Fill).where(Fill.env == ENV, Fill.inst_id.in_(INSTS)))
        conn.execute(delete(Order).where(Order.env == ENV, Order.ord_id == ENTRY_ORD_ID))
        conn.execute(delete(Proposal).where(Proposal.env == ENV, Proposal.author == AUTHOR))
        conn.execute(delete(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == STREAM))


@pytest.fixture
def db(engine: Engine) -> Iterator[Engine]:
    _wipe(engine)
    yield engine
    _wipe(engine)


def mock_okx(router: respx.MockRouter) -> dict[str, respx.Route]:
    def fills(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("instType") == "SWAP" and request.url.params.get("after") is None:
            return httpx.Response(200, json=FILLS)
        return httpx.Response(200, json=EMPTY)

    def positions(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("after") is None:
            return httpx.Response(200, json=POSITIONS)
        return httpx.Response(200, json=EMPTY)

    return {
        "time": router.get(API + "public/time").respond(
            json={"code": "0", "msg": "", "data": [{"ts": str(int(time.time() * 1000))}]}
        ),
        "positions": router.get(API + "account/positions-history").mock(side_effect=positions),
        "fills": router.get(API + "trade/fills-history").mock(side_effect=fills),
    }


def _count(engine: Engine, model: Any) -> int:
    with engine.connect() as conn:
        n: int | None = conn.scalar(
            select(func.count()).select_from(model).where(model.env == ENV, model.inst_id.in_(INSTS))
        )
    return n or 0


def _atlas_entry(engine: Engine) -> uuid.UUID:
    """An Atlas entry order (proposal + risk check) for the ETH long, carrying the OKX ordId of the
    fixture's opening fill."""
    with Session(engine) as s:
        proposal = Proposal(
            env=ENV,
            author=AUTHOR,
            inst_id="ETH-USDT-SWAP",
            side="buy",
            order_type="limit",
            entry=Decimal("3301.5"),
            stop=Decimal("3280"),
            status="filled",
        )
        s.add(proposal)
        s.flush()
        check = RiskCheck(
            env=ENV,
            proposal_id=proposal.id,
            settings_version=1,
            result="approved",
            checks=[],
            decision="send_now",
        )
        s.add(check)
        s.flush()
        s.add(
            Order(
                env=ENV,
                proposal_id=proposal.id,
                risk_check_id=check.id,
                cl_ord_id="atl" + uuid.uuid4().hex[:20],
                ord_id=ENTRY_ORD_ID,
                inst_id="ETH-USDT-SWAP",
                side="buy",
                pos_side="net",
                ord_type="limit",
                td_mode="isolated",
                px=Decimal("3301.5"),
                sz=Decimal("2"),
                filled_sz=Decimal("2"),
                state="filled",
                origin="atlas",
                purpose="entry",
            )
        )
        s.commit()
        return proposal.id


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_recorded_history_is_upserted_and_a_second_sync_adds_no_rows(
    respx_mock: respx.MockRouter, db: Engine
) -> None:
    routes = mock_okx(respx_mock)
    now = clock(T0, T0 + timedelta(minutes=5))

    async with OkxClient(ENV, CREDS) as okx:
        first = await sync_history(ENV, okx=okx, engine=db, now=now)
    assert first.incremental is False
    assert (first.positions_new, first.fills_new) == (1, 3)
    assert _count(db, PositionClosed) == 1 and _count(db, Fill) == 3
    inst_types = {c.request.url.params["instType"] for c in routes["fills"].calls}
    assert inst_types == {"SPOT", "SWAP", "FUTURES"}

    async with OkxClient(ENV, CREDS) as okx:
        second = await sync_history(ENV, okx=okx, engine=db, now=now)
    assert second.incremental is True
    assert (second.positions_new, second.fills_new) == (0, 0)
    assert _count(db, PositionClosed) == 1 and _count(db, Fill) == 3

    with Session(db) as s:
        pos = s.scalars(select(PositionClosed).where(PositionClosed.env == ENV)).one()
        assert (pos.side, pos.pos_side, pos.close_type) == ("long", "net", "2")
        assert pos.size == Decimal("2") and pos.entry_px == Decimal("3301.5")
        assert pos.exit_px == Decimal("3350.12") and pos.realised_pnl == Decimal("0.2904")
        assert pos.fee == Decimal("-0.67") and pos.funding_fee == Decimal("-0.012")
        assert pos.opened_at == datetime.fromtimestamp(1793100000, UTC)
        assert pos.closed_at == datetime.fromtimestamp(1793110000, UTC)
        assert pos.origin == "external" and pos.proposal_id is None and pos.agent is None
        close = s.scalars(select(Fill).where(Fill.trade_id == "1900000002")).one()
        assert close.fill_pnl == Decimal("0.9724") and close.order_id is None
        assert close.side == "sell" and close.fee == Decimal("-0.67")
        status = s.scalars(select(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == STREAM)).one()
        assert status.last_error is None and status.reconciled_at == T0 + timedelta(minutes=5)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_a_position_opened_by_an_atlas_entry_is_linked_to_its_proposal(
    respx_mock: respx.MockRouter, db: Engine
) -> None:
    mock_okx(respx_mock)
    proposal_id = _atlas_entry(db)

    async with OkxClient(ENV, CREDS) as okx:
        await sync_history(ENV, okx=okx, engine=db, now=clock(T0))

    with Session(db) as s:
        pos = s.scalars(select(PositionClosed).where(PositionClosed.env == ENV)).one()
        assert pos.proposal_id == proposal_id
        assert (pos.agent, pos.origin) == (AUTHOR, "atlas")
        entry_fill = s.scalars(select(Fill).where(Fill.trade_id == "1900000001")).one()
        assert entry_fill.order_id is not None


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_a_failed_read_writes_nothing_but_the_error(respx_mock: respx.MockRouter, db: Engine) -> None:
    mock_okx(respx_mock)
    respx_mock.get(API + "account/positions-history").respond(
        json={"code": "50011", "msg": "Too Many Requests", "data": []}
    )

    with pytest.raises(Exception):  # noqa: B017 - whichever OkxError the client raises
        async with OkxClient(ENV, CREDS) as okx:
            await sync_history(ENV, okx=okx, engine=db, now=clock(T0))

    assert _count(db, PositionClosed) == 0 and _count(db, Fill) == 0
    with Session(db) as s:
        status = s.scalars(select(SyncStatus).where(SyncStatus.env == ENV, SyncStatus.stream == STREAM)).one()
        assert status.last_error and status.last_success_at is None
