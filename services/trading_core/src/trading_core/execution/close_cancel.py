"""The owner closes one position or cancels one order (``POST /internal/positions/{inst_id}/close``,
``POST /internal/orders/{order_id}/cancel``).

- **Close**: a reduce-only market close (``trade/close-position``) of the position, then its algo
  orders (SL/TP/OCO) are cancelled. A close that failed keeps the stop.
- **Cancel an Atlas entry**: handed to ``EntryLifecycle.cancel``, which owns entry protection. Under
  the same per-order lock as fills and the timeout sweep it cancels the order, reads it back, and then
  either cancels the unfilled protection (proposal ``cancelled``) or, after a partial fill, keeps one
  stop amended to the filled size.
- **Cancel any other order**: a plain ``trade/cancel-order`` with the Executor's retries.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import redis
from atlas_shared import bus
from atlas_shared.db import Order
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from trading_core.execution.config import SYSTEM_STREAM
from trading_core.execution.control import (
    OPEN_ORDER_STATES,
    Control,
    ControlError,
    Item,
    Report,
    Sleep,
    ref,
)
from trading_core.execution.lifecycle import EntryCancel, EntryLifecycle
from trading_core.execution.okx_access import OkxFactory, open_client
from trading_core.okx.trade import OkxTradeClient


async def close_position(
    env: str,
    inst_id: str,
    pos_side: str | None = None,
    *,
    engine: Engine,
    redis_client: redis.Redis | None = None,
    client_factory: OkxFactory | None = None,
    app_key: str = "",
    sleep: Sleep = asyncio.sleep,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Market-close one position (reduce-only), then cancel that position's algo orders (SL/TP/OCO)."""
    clock = now or (lambda: datetime.now(UTC))
    rds = redis_client if redis_client is not None else bus.make_client()
    okx, cfg = open_client(engine, env, client_factory, app_key)
    if okx is None:
        raise ControlError("connection_missing", f"No OKX {env} connection is stored.", 409)
    report = Report(env, True, clock())
    ctl = Control(engine, env, rds, cfg, sleep, clock)
    async with okx:
        ctl.bind(okx)
        positions = await ctl.read(report, "positions", lambda: ctl.positions(inst_id))
        if positions is None:
            return {**report.payload(), "inst_id": inst_id, "status": "failed"}
        wanted = pos_side or None
        matches = [
            p
            for p in positions
            if p.inst_id == inst_id and (wanted is None or (p.pos_side or "net") == wanted)
        ]
        if not matches:
            raise ControlError("no_position", f"There is no open {inst_id} position.", 404)
        for position in matches:
            item = await ctl.close(position)
            report.items.append(item)
            if item.action == "failed":
                continue
            side = position.pos_side or "net"
            algos = await ctl.read(report, "pending algo orders", lambda: ctl.pending_algos(inst_id)) or []
            report.items.extend(
                await ctl.cancel_algos([ref(a) for a in algos if (a.pos_side or "net") == side])
            )
    payload = {**report.payload(), "inst_id": inst_id}
    payload["status"] = "closed" if payload["ok"] else "failed"
    if payload["ok"]:
        ctl.warroom("text", f"Closed the {inst_id} position at market on the owner's request.", None)
    else:
        message = "; ".join(f.get("message") or "failed" for f in payload["failed"])
        ctl.warroom(
            "system_alert",
            f"**Close of {inst_id} failed.** {message}",
            {"type": "position_close", "code": "position_close_failed", **payload},
        )
    ctl.publish(SYSTEM_STREAM, "system.position_close", payload)
    return payload


def _entry_item(inst_id: str, ref_: str | None, done: EntryCancel) -> Item:
    if done.status == "cancelled":
        return Item(
            "order", inst_id, ref_, "already_done" if done.noop else "cancelled", done.attempts, done.code
        )
    message = done.message or "OKX did not confirm the cancel."
    return Item("order", inst_id, ref_, "failed", done.attempts, done.code, message)


async def cancel_order(
    env: str,
    order_id: str,
    *,
    engine: Engine,
    redis_client: redis.Redis | None = None,
    client_factory: OkxFactory | None = None,
    app_key: str = "",
    sleep: Sleep = asyncio.sleep,
    now: Callable[[], datetime] | None = None,
    lifecycle: EntryLifecycle | None = None,
) -> dict[str, Any]:
    """Cancel one open order. An Atlas entry goes through ``EntryLifecycle.cancel`` (pass the app's
    instance so its per-order lock is shared with fills and the sweep)."""
    clock = now or (lambda: datetime.now(UTC))
    try:
        oid = uuid.UUID(str(order_id))
    except ValueError as exc:
        raise ControlError("order_not_found", "Not an order id.", 404) from exc
    with Session(engine) as s:
        row = s.scalar(select(Order).where(Order.id == oid, Order.env == env))
        if row is None:
            raise ControlError("order_not_found", "No such order.", 404)
        if row.state not in OPEN_ORDER_STATES:
            raise ControlError("order_not_open", f"The order is {row.state}.", 409)
        inst_id, ord_id, cl_ord_id = row.inst_id, row.ord_id, row.cl_ord_id
        is_entry = row.origin == "atlas" and row.purpose == "entry"
    if not ord_id and not cl_ord_id:
        raise ControlError("order_not_open", "The order has no OKX id.", 409)
    rds = redis_client if redis_client is not None else bus.make_client()
    okx, cfg = open_client(engine, env, client_factory, app_key)
    if okx is None:
        raise ControlError("connection_missing", f"No OKX {env} connection is stored.", 409)
    report = Report(env, False, clock())
    ctl = Control(engine, env, rds, cfg, sleep, clock)
    extra: dict[str, Any] = {"filled_sz": None, "protection": None}
    async with okx:
        ctl.bind(okx)
        if is_entry:
            lc = lifecycle or EntryLifecycle(
                engine, env, redis_client=rds, client_factory=client_factory, app_key=app_key, now=clock
            )
            done = await lc.cancel(
                oid,
                OkxTradeClient(okx),
                attempts=cfg.order_max_retries,
                backoff=cfg.backoff,
                sleep=sleep,
            )
            if done.status == "not_open":
                raise ControlError("order_not_open", "The order is no longer open.", 409)
            report.items.append(_entry_item(inst_id, ord_id or cl_ord_id, done))
            if done.filled_sz is not None:
                extra["filled_sz"] = format(done.filled_sz, "f")
            if done.protection is not None:
                extra["protection"] = {
                    "status": done.protection.status,
                    "target_sz": format(done.protection.target_sz, "f"),
                    "algo_cl_ord_id": done.protection.algo_cl_ord_id,
                    "canceled": list(done.protection.canceled),
                }
        else:
            report.items.append(await ctl.cancel_order(inst_id, ord_id, cl_ord_id))
    payload = {**report.payload(), "order_id": str(oid), "inst_id": inst_id, **extra}
    payload["status"] = "cancelled" if payload["ok"] else "failed"
    ctl.publish(SYSTEM_STREAM, "system.order_cancel", payload)
    if not payload["ok"]:
        message = "; ".join(f.get("message") or "failed" for f in payload["failed"])
        ctl.warroom(
            "system_alert",
            f"**Cancel of the {inst_id} order failed.** {message}",
            {"type": "order_cancel", "code": "order_cancel_failed", **payload},
        )
    return payload


__all__ = ["cancel_order", "close_position"]
