"""ATL-213 (decision #254): at autonomy 3-4 a send_now risk result is sent through trading_core's
signed ``POST /internal/proposals/{id}/execute`` with ``approved_by = "autonomy:<level>"``.

trading_core is a fake behind an httpx MockTransport on the real :class:`TradingCoreClient` (so the
path, the signature and the body are the real ones). It does to the row what the real route does:
re-check (a new risk_checks row, the proposal back to ``risk_checked``), then either "send" (200) or
refuse and close the proposal. Needs the checkout's test DB migrated: run
``cd backend && php artisan test`` first.
"""

from __future__ import annotations

import ast
import asyncio
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from atlas_agents.orchestrator import approval
from atlas_agents.orchestrator.trading_core import TradingCoreClient
from atlas_agents.tools.framework import ToolContext
from atlas_shared.db import Proposal, RiskCheck
from atlas_shared.events import EventEnvelope
from atlas_shared.internal_api import sign
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

NOW = datetime(2097, 6, 3, 9, 0, tzinfo=UTC)
ENV = "demo"
SECRET = "autonomy-test-secret"  # noqa: S105
D = Decimal


@dataclass
class FakeTradingCore:
    """The execute route: re-checks, then sends (``outcome="sent"``) or refuses."""

    engine: Engine
    outcome: str = "sent"  # sent | risk_rejected | needs_owner | execution_failed
    requests: list[httpx.Request] = field(default_factory=list)

    def _recheck(self, pid: uuid.UUID, *, result: str, decision: str | None) -> dict[str, Any]:
        rid = uuid.uuid4()
        with Session(self.engine) as s:
            s.add(
                RiskCheck(
                    id=rid,
                    env=ENV,
                    proposal_id=pid,
                    settings_version=1,
                    result=result,
                    checks=[],
                    decision=decision,
                    final_size=D("3") if result != "rejected" else None,
                    final_notional_usd=D("300") if result != "rejected" else None,
                    created_at=NOW + timedelta(seconds=1),
                    updated_at=NOW + timedelta(seconds=1),
                )
            )
            s.flush()
            s.execute(
                update(Proposal)
                .where(Proposal.id == pid, Proposal.status == "sent")
                .values(status="risk_checked")
            )
            s.commit()
        return {
            "risk_check_id": str(rid),
            "result": result,
            "decision": decision,
            "reason": "fresh check",
            "final_contracts": None if result == "rejected" else "3",
            "final_notional_usd": None if result == "rejected" else "300",
        }

    def _close(self, pid: uuid.UUID, status: str, reason: str) -> None:
        with Session(self.engine) as s:
            s.execute(
                update(Proposal)
                .where(Proposal.id == pid, Proposal.status == "risk_checked")
                .values(status=status, status_reason=reason, decided_by="autonomy:3")
            )
            s.commit()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        pid = uuid.UUID(request.url.path.split("/")[3])
        with Session(self.engine) as s:
            current = s.scalar(select(Proposal.status).where(Proposal.id == pid))
        if current != "sent":
            return httpx.Response(409, json={"ok": False, "status": "refused", "reason": "not_sent"})
        if self.outcome == "risk_rejected":
            risk = self._recheck(pid, result="rejected", decision=None)
            self._close(pid, "rejected", "risk_rejected")
            return httpx.Response(
                409,
                json={
                    "ok": False,
                    "status": "refused",
                    "reason": "risk_rejected",
                    "detail": "Circuit breaker active: daily_loss.",
                    "proposal_id": str(pid),
                    "risk_check": risk,
                },
            )
        if self.outcome == "needs_owner":
            risk = self._recheck(pid, result="approved", decision="awaiting_owner")
            return httpx.Response(
                409,
                json={"ok": False, "status": "refused", "reason": "needs_owner", "risk_check": risk},
            )
        risk = self._recheck(pid, result="approved", decision="send_now")
        if self.outcome == "execution_failed":
            self._close(pid, "rejected", "execution_okx_refused")
            return httpx.Response(
                409,
                json={
                    "ok": False,
                    "status": "refused",
                    "reason": "okx_refused",
                    "detail": "OKX refused the order (51008: insufficient balance).",
                    "risk_check": risk,
                },
            )
        with Session(self.engine) as s:  # the Executor's proposal_sent()
            s.execute(update(Proposal).where(Proposal.id == pid).values(status="sent"))
            s.commit()
        return httpx.Response(
            200,
            json={
                "ok": True,
                "status": "sent",
                "reason": "sent",
                "proposal_id": str(pid),
                "risk_check": risk,
            },
        )

    def client(self) -> TradingCoreClient:
        return TradingCoreClient(
            base_url="http://trading-core.test", secret=SECRET, transport=httpx.MockTransport(self.handler)
        )


@dataclass
class World:
    engine: Engine
    inst: str
    rec: Recorder

    def ctx(self) -> ToolContext:
        return ToolContext(
            env=ENV,
            session_factory=sessionmaker(self.engine),
            agent="orchestrator",
            correlation_id="cycle-test",
            clock=lambda: NOW,
            publish=self.rec,
        )

    def proposal(
        self, *, kind: str = "entry", decision: str = "send_now"
    ) -> tuple[uuid.UUID, dict[str, Any]]:
        pid, rid = uuid.uuid4(), uuid.uuid4()
        with Session(self.engine) as s:
            s.add(
                Proposal(
                    id=pid,
                    env=ENV,
                    kind=kind,
                    author="strategist",
                    inst_id=self.inst,
                    side="buy",
                    order_type="limit",
                    entry=D("100"),
                    stop=D("95"),
                    take_profits=[{"price": "110", "pct": "100"}],
                    status="risk_checked",
                    created_at=NOW - timedelta(minutes=5),
                    updated_at=NOW - timedelta(minutes=1),
                )
            )
            s.flush()
            s.add(
                RiskCheck(
                    id=rid,
                    env=ENV,
                    proposal_id=pid,
                    settings_version=1,
                    result="approved",
                    checks=[],
                    decision=decision,
                    final_size=D("3"),
                    final_notional_usd=D("300"),
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            s.commit()
        body = {
            "ok": True,
            "proposal_id": str(pid),
            "risk_check_id": str(rid),
            "result": "approved",
            "decision": decision,
            "reason": "All nine checks passed.",
            "final_contracts": "3",
            "final_notional_usd": "300",
            "leverage": 2,
            "checks": [],
        }
        return pid, body

    def row(self, pid: uuid.UUID) -> Proposal:
        with Session(self.engine) as s:
            row = s.get(Proposal, pid)
            assert row is not None
            s.expunge(row)
            return row

    def lines(self) -> list[EventEnvelope]:
        return [e for e in self.rec.of("warroom.message") if e.payload.get("kind") == "text"]


@pytest.fixture
def world(engine: Engine) -> Iterator[World]:
    inst = f"A{uuid.uuid4().hex[:8].upper()}-USDT-SWAP"
    yield World(engine, inst, Recorder())
    with engine.begin() as conn:
        conn.execute(delete(Proposal).where(Proposal.inst_id == inst))


def _apply(w: World, pid: uuid.UUID, body: dict[str, Any], tc: FakeTradingCore, level: int = 3) -> Any:
    return approval.apply_risk_result(
        w.ctx(), pid, body, cycle_id=None, source="system:orchestrator", client=tc.client(), autonomy=level
    )


# -- AC1: send_now → exactly one signed execute call with approved_by autonomy:3 -----------------------


async def test_send_now_calls_execute_once_with_autonomy_level_and_no_approval_window(world: World) -> None:
    pid, body = world.proposal()
    tc = FakeTradingCore(world.engine)
    status, expires, row = await _apply(world, pid, body, tc)

    assert (status, expires) == ("sent", None) and row is not None
    assert len(tc.requests) == 1
    req = tc.requests[0]
    assert req.method == "POST" and req.url.path == f"/internal/proposals/{pid}/execute"
    assert json.loads(req.content) == {"approved_by": "autonomy:3"}
    h = req.headers
    assert h["X-Atlas-Signature"] == sign(
        SECRET, h["X-Atlas-Timestamp"], h["X-Atlas-Nonce"], "POST", req.url.path, req.content
    )
    stored = world.row(pid)
    assert stored.status == "sent" and stored.approval_expires_at is None
    lines = world.lines()
    assert len(lines) == 1
    text = lines[0].payload["content_md"]
    assert "autonomy 3" in text and "300" in text and "Sent automatically" in text
    assert lines[0].payload["card"]["approved_by"] == "autonomy:3"
    # No owner approval card: nothing went to awaiting_owner.
    assert world.rec.of("proposal.awaiting_owner") == []


async def test_level_4_sends_as_autonomy_4(world: World) -> None:
    pid, body = world.proposal()
    tc = FakeTradingCore(world.engine)
    status, _e, _r = await _apply(world, pid, body, tc, level=4)
    assert status == "sent"
    assert [json.loads(r.content) for r in tc.requests] == [{"approved_by": "autonomy:4"}]


# -- AC2: the fresh re-check rejects → nothing sent, rejected/risk_rejected, a War Room line -----------


async def test_a_rejecting_recheck_sends_nothing_and_closes_the_proposal(world: World) -> None:
    pid, body = world.proposal()
    tc = FakeTradingCore(world.engine, outcome="risk_rejected")
    status, expires, _row = await _apply(world, pid, body, tc)

    assert (status, expires) == ("rejected", None)
    assert len(tc.requests) == 1
    stored = world.row(pid)
    assert (stored.status, stored.status_reason) == ("rejected", "risk_rejected")
    lines = world.lines()
    assert len(lines) == 1
    assert "did not send" in lines[0].payload["content_md"]
    assert "risk_rejected" in lines[0].payload["content_md"]
    assert "Circuit breaker" in lines[0].payload["content_md"]


async def test_an_executor_refusal_is_closed_with_its_reason(world: World) -> None:
    pid, body = world.proposal()
    tc = FakeTradingCore(world.engine, outcome="execution_failed")
    status, _e, _r = await _apply(world, pid, body, tc)
    assert status == "rejected"
    assert world.row(pid).status_reason == "execution_okx_refused"
    assert "okx_refused" in world.lines()[0].payload["content_md"]


async def test_a_fresh_check_that_asks_for_the_owner_opens_the_approval_window(world: World) -> None:
    pid, body = world.proposal()
    tc = FakeTradingCore(world.engine, outcome="needs_owner")
    status, expires, _r = await _apply(world, pid, body, tc)
    assert status == "awaiting_owner" and expires is not None
    stored = world.row(pid)
    assert stored.status == "awaiting_owner" and stored.approval_expires_at is not None
    events = world.rec.of("proposal.awaiting_owner")
    assert len(events) == 1 and events[0].payload["decision"] == "awaiting_owner"
    assert len(tc.requests) == 1


async def test_trading_core_unreachable_closes_the_unsent_proposal(world: World) -> None:
    pid, body = world.proposal()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = TradingCoreClient(base_url="http://x.test", secret=SECRET, transport=httpx.MockTransport(boom))
    status, _e, _r = await approval.apply_risk_result(
        world.ctx(), pid, body, cycle_id=None, source="s", client=client, autonomy=3
    )
    assert status == "rejected"
    stored = world.row(pid)
    assert (stored.status, stored.status_reason) == ("rejected", "execution_unavailable")
    assert "could not send" in world.lines()[0].payload["content_md"]


async def test_a_timeout_after_trading_core_sent_leaves_the_proposal_sent(world: World) -> None:
    """The route re-checked and the Executor placed the order (status ``sent`` again), but the answer
    came back after the client timeout: the proposal must stay sent, never rejected."""
    pid, body = world.proposal()
    tc = FakeTradingCore(world.engine)

    def sent_then_timeout(request: httpx.Request) -> httpx.Response:
        tc.handler(request)  # re-check + the Executor's proposal_sent()
        raise httpx.ReadTimeout("no answer in time", request=request)

    client = TradingCoreClient(
        base_url="http://trading-core.test", secret=SECRET, transport=httpx.MockTransport(sent_then_timeout)
    )
    status, _e, _r = await approval.apply_risk_result(
        world.ctx(), pid, body, cycle_id=None, source="s", client=client, autonomy=3
    )
    assert len(tc.requests) == 1
    stored = world.row(pid)
    assert stored.status == "sent" and stored.status_reason is None
    assert status == "sent"
    lines = world.lines()
    assert len(lines) == 1
    assert "outcome is unknown" in lines[0].payload["content_md"]
    assert "reconciliation" in lines[0].payload["content_md"]
    assert lines[0].payload["card"]["status"] == "unknown"


async def test_a_timeout_after_a_rejecting_recheck_keeps_trading_cores_close(world: World) -> None:
    pid, body = world.proposal()
    tc = FakeTradingCore(world.engine, outcome="risk_rejected")

    def rejected_then_timeout(request: httpx.Request) -> httpx.Response:
        tc.handler(request)
        raise httpx.ReadTimeout("no answer in time", request=request)

    client = TradingCoreClient(
        base_url="http://trading-core.test",
        secret=SECRET,
        transport=httpx.MockTransport(rejected_then_timeout),
    )
    status, _e, _r = await approval.apply_risk_result(
        world.ctx(), pid, body, cycle_id=None, source="s", client=client, autonomy=3
    )
    stored = world.row(pid)
    assert (status, stored.status, stored.status_reason) == ("rejected", "rejected", "risk_rejected")


# -- AC3: idempotent ------------------------------------------------------------------------------------


async def test_a_repeated_apply_never_executes_twice(world: World) -> None:
    pid, body = world.proposal()
    tc = FakeTradingCore(world.engine)
    first = await _apply(world, pid, body, tc)
    second = await _apply(world, pid, body, tc)
    assert first[0] == "sent" and second[2] is None
    assert len(tc.requests) == 1


async def test_concurrent_applies_execute_once(world: World) -> None:
    pid, body = world.proposal()
    tc = FakeTradingCore(world.engine)
    results = await asyncio.gather(*[_apply(world, pid, body, tc) for _ in range(4)])
    assert len(tc.requests) == 1
    assert sum(1 for r in results if r[2] is not None) == 1
    # A duplicate that lands while trading_core re-checks must not open an approval window.
    assert world.row(pid).status == "sent" and world.rec.of("proposal.awaiting_owner") == []


async def test_a_stale_body_after_a_newer_check_does_not_send(world: World) -> None:
    """The proposal is back at risk_checked with a newer check than the body's (e.g. the route
    re-checked it): replaying the old send_now body must not execute."""
    pid, body = world.proposal()
    with Session(world.engine) as s:
        s.add(
            RiskCheck(
                env=ENV,
                proposal_id=pid,
                settings_version=1,
                result="approved",
                checks=[],
                decision="awaiting_owner",
                created_at=NOW + timedelta(seconds=5),
                updated_at=NOW + timedelta(seconds=5),
            )
        )
        s.commit()
    tc = FakeTradingCore(world.engine)
    _status, expires, row = await _apply(world, pid, body, tc)
    assert (row, expires, tc.requests) == (None, None, [])
    assert world.row(pid).status == "risk_checked"  # a superseded body moves nothing


# -- levels 1-2 and non-send decisions never call execute ---------------------------------------------


@pytest.mark.parametrize(
    ("level", "decision", "expected"),
    [
        (2, "awaiting_owner", "awaiting_owner"),
        (2, "send_now", "awaiting_owner"),  # a stale send_now at level 2 still asks the owner
        (3, "awaiting_owner", "awaiting_owner"),  # above the ask-above amount
        (1, "advise_only", "advise_only"),
    ],
)
async def test_no_execute_without_send_now_at_3_or_4(
    world: World, level: int, decision: str, expected: str
) -> None:
    pid, body = world.proposal(decision=decision)
    tc = FakeTradingCore(world.engine)
    status, _e, _r = await _apply(world, pid, body, tc, level=level)
    assert status == expected and tc.requests == []
    assert world.lines() == []


async def test_a_pm_close_at_level_3_goes_through_the_same_route(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas_agents.pm import route as pm_route

    pid, body = world.proposal(kind="close")
    with Session(world.engine) as s:
        s.execute(update(Proposal).where(Proposal.id == pid).values(status="under_review"))
        s.commit()

    tc = FakeTradingCore(world.engine)

    class Risk(TradingCoreClient):
        async def risk_evaluate(self, proposal_id: str) -> dict[str, Any]:
            with Session(world.engine) as s:
                s.execute(update(Proposal).where(Proposal.id == pid).values(status="risk_checked"))
                s.commit()
            return body

    client = Risk(
        base_url="http://trading-core.test", secret=SECRET, transport=httpx.MockTransport(tc.handler)
    )
    # The shared settings row is not ours to change: the PM path runs at an explicit level 3.
    orig = approval.apply_risk_result

    async def at_level_3(*a: Any, **k: Any) -> Any:
        return await orig(*a, **{**k, "autonomy": 3})

    monkeypatch.setattr(approval, "apply_risk_result", at_level_3)
    status = await pm_route.route(world.ctx(), str(pid), client)
    assert status == "sent"
    assert len(tc.requests) == 1
    assert "close on" in world.lines()[0].payload["content_md"]


# -- AC4: the only order path from the agents is trading_core's execute route ------------------------

AGENTS_SRC = Path(__file__).resolve().parents[1] / "src" / "atlas_agents"
APPROVAL = AGENTS_SRC / "orchestrator" / "approval.py"
# Names that would put an order on OKX or call the Executor without the fresh risk check.
FORBIDDEN_NAMES = {
    "execute_position_action",
    "place_order",
    "place_batch_orders",
    "place_algo_order",
    "close_position",
    "amend_order",
    "OkxClient",
    "OkxTradeClient",
}
FORBIDDEN_PATHS = ("/api/v5/", "/internal/execute/", "trade/order", "trade/close-position")


def order_path_offences(source: str) -> list[str]:
    """Imports of trading_core/OKX code, calls of Executor/OKX functions and OKX or raw Executor
    paths in ``source``."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            mods = [node.module or ""] if isinstance(node, ast.ImportFrom) else [a.name for a in node.names]
            hits += [f"import {m}" for m in mods if m.split(".")[0] in ("trading_core", "okx", "ccxt")]
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "execute":
                hits.append("execute()")  # trading_core.execution.executor.execute (not session.execute)
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else ""
            )
            if name in FORBIDDEN_NAMES:
                hits.append(f"{name}()")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            hits += [f"path {p}" for p in FORBIDDEN_PATHS if p in node.value]
    return hits


def test_the_agents_package_has_no_order_path_but_the_execute_route() -> None:
    offenders = {
        str(p.relative_to(AGENTS_SRC)): hits
        for p in AGENTS_SRC.rglob("*.py")
        if (hits := order_path_offences(p.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_apply_risk_result_reaches_trading_core_only_through_execute_proposal() -> None:
    tree = ast.parse(APPROVAL.read_text(encoding="utf-8"))
    calls = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "client"
    }
    assert calls == {"execute_proposal"}
    from atlas_agents.orchestrator import trading_core as tc_mod

    assert tc_mod.EXECUTE_PATH == "/internal/proposals/{proposal_id}/execute"


@pytest.mark.parametrize(
    "bad",
    [
        "from trading_core.execution.executor import execute\n"
        "async def f(r):\n    await execute(r, 'autonomy:3')\n",
        "async def f(c):\n    await c.place_order(1)\n",
        "import trading_core.okx.client\n",
        "P = '/internal/execute/{risk_check_id}'\n",
        "def f(c):\n    return OkxClient(c)\n",
    ],
)
def test_the_order_path_guard_catches_a_direct_call(bad: str) -> None:
    assert order_path_offences(bad)
