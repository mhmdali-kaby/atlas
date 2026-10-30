"""The kill switch (brief §10, decision #251), reached through ``POST /internal/system/kill``.

``kill(env, flatten=...)``, in this order:

1. ``trading_state.paused`` is set (reason "Kill switch") and ``system.trading_state`` published,
   before any request to OKX, so the Executor refuses anything it has not planned yet.
2. Every pending order on OKX is cancelled (``trade/cancel-order``, ten at a time), and every pending
   algo order **without a stop leg** (take-profit-only conditionals and trigger orders) is cancelled
   (``trade/cancel-algos``). Stop-loss conditionals and OCO orders are **kept**, so every open position
   stays protected on OKX (rule 3). The report counts them in ``stops_kept``.
3. ``orders-pending`` is read once more, and any order that was not in the first read is cancelled
   too: an Executor run that passed its paused check before step 1 may have placed its entry while the
   first read was in flight.
4. Only with ``flatten``: each open position is closed with ``trade/close-position`` (a market close
   that can only reduce), and once it is closed its remaining algo orders are cancelled. A position
   whose close failed keeps its stop.

The report is ``{cancelled, closed, stops_kept, failed[], items[]}`` (``execution.control.Report``).
The War Room gets one ``system_alert`` with the summary and ``atlas.system`` one ``system.kill``.

Nothing here places an opening order, withdraws or transfers (brief rules 1 and 5).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import redis
from atlas_shared import bus
from sqlalchemy import Engine

from trading_core.execution.config import SYSTEM_STREAM
from trading_core.execution.control import (
    Control,
    Item,
    Report,
    Sleep,
    algo_kind,
    has_stop,
    ref,
)
from trading_core.execution.okx_access import OkxFactory, open_client
from trading_core.execution.trading_state import set_paused
from trading_core.okx.models import Position
from trading_core.okx.trade_models import AlgoOrderDetail, OrderDetail

log = logging.getLogger("trading_core.execution.kill")


def _summary(report: dict[str, Any]) -> str:
    lines = [
        "**Kill switch.** Trading is paused.",
        f"Cancelled {report['cancelled_orders']} order(s) and {report['cancelled_algos']} take-profit/trigger"
        f" order(s); kept {report['stops_kept']} stop-loss order(s) on OKX.",
    ]
    if report["flatten"]:
        lines.append(f"Closed {report['closed']} position(s).")
    if report["failed"]:
        lines.append(f"{len(report['failed'])} item(s) failed:")
        for f in report["failed"][:10]:
            what = " ".join(str(f[k]) for k in ("inst_id", "ref") if f.get(k))
            lines.append(f"- {f['kind']} {what}: {f.get('message') or 'failed'}")
    return "\n".join(lines)


async def kill(
    env: str,
    *,
    flatten: bool,
    engine: Engine,
    redis_client: redis.Redis | None = None,
    client_factory: OkxFactory | None = None,
    app_key: str = "",
    sleep: Sleep = asyncio.sleep,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    clock = now or (lambda: datetime.now(UTC))
    rds = redis_client if redis_client is not None else bus.make_client()
    reason = "Kill switch (positions closed)." if flatten else "Kill switch."
    set_paused(engine, env, True, reason=reason, redis_client=rds, now=clock)
    report = Report(env, flatten, clock())
    okx, cfg = open_client(engine, env, client_factory, app_key)
    ctl = Control(engine, env, rds, cfg, sleep, clock)
    if okx is None:
        report.items.append(
            Item("read", None, "connection", "failed", 0, None, f"No OKX {env} connection is stored.")
        )
    else:
        async with okx:
            ctl.bind(okx)
            await _kill(ctl, report, flatten)
    payload = report.payload()
    log.warning(
        "kill switch",
        extra={
            "env": env,
            "flatten": flatten,
            "cancelled": payload["cancelled"],
            "closed": payload["closed"],
        },
    )
    payload["paused"] = True
    ctl.publish(SYSTEM_STREAM, "system.kill", payload)
    ctl.warroom("system_alert", _summary(payload), {"type": "kill_switch", "code": "kill_switch", **payload})
    return payload


def _order_key(order: OrderDetail) -> str:
    return order.ord_id or order.cl_ord_id or ""


async def _kill(ctl: Control, report: Report, flatten: bool) -> None:
    orders: list[OrderDetail] = await ctl.read(report, "pending orders", ctl.pending_orders) or []
    algos: list[AlgoOrderDetail] = await ctl.read(report, "pending algo orders", ctl.pending_algos) or []
    if orders:
        report.items.extend(await ctl.cancel_orders(orders))
    report.items.extend(await ctl.cancel_algos([ref(a) for a in algos if not has_stop(a)]))
    # An entry the Executor sent while the first read was in flight is not in it: read once more.
    seen = {_order_key(o) for o in orders}
    again: list[OrderDetail] = await ctl.read(report, "pending orders", ctl.pending_orders) or []
    late = [o for o in again if _order_key(o) not in seen]
    if late:
        report.items.extend(await ctl.cancel_orders(late))
    stops = [a for a in algos if has_stop(a)]
    released: set[str] = set()
    if flatten:
        positions: list[Position] = await ctl.read(report, "positions", ctl.positions) or []
        for position in positions:
            item = await ctl.close(position)
            report.items.append(item)
            if item.action == "failed":
                continue
            mine = [
                a
                for a in stops
                if a.inst_id == position.inst_id and (a.pos_side or "net") == (position.pos_side or "net")
            ]
            released.update(a.algo_id for a in mine)
            report.items.extend(await ctl.cancel_algos([ref(a) for a in mine]))
    for algo in stops:
        if algo.algo_id not in released:
            report.items.append(Item("algo", algo.inst_id, algo.algo_id, "kept", algo_kind=algo_kind(algo)))


__all__ = ["kill"]
