"""Order/algo state writes and their events on atlas.orders, atlas.system and atlas.warroom."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import redis
from atlas_shared import bus
from atlas_shared.db import AlgoOrder, Order, Proposal
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from trading_core.execution.config import (
    EXECUTABLE_PROPOSAL_STATUSES,
    ORDERS_STREAM,
    SOURCE,
    SYSTEM_STREAM,
    WARROOM_AUTHOR,
    WARROOM_SOURCE,
    WARROOM_STREAM,
)
from trading_core.execution.plan import _Plan

log = logging.getLogger("trading_core.execution")

_OKX_ORDER_STATES = {
    "live": "live",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "canceled": "canceled",
    "mmp_canceled": "canceled",
}


class _Recorder:
    """Writes order/algo transitions and publishes them; one short transaction per write."""

    def __init__(self, engine: Engine, rds: redis.Redis, plan: _Plan, now: Callable[[], datetime]) -> None:
        self.engine = engine
        self.rds = rds
        self.plan = plan
        self.now = now

    def _publish(self, stream: str, type_: str, payload: dict[str, Any], source: str = SOURCE) -> None:
        envelope = EventEnvelope.model_validate(
            {
                "type": type_,
                "env": self.plan.env,
                "source": source,
                "correlation_id": self.plan.correlation_id,
                "payload": payload,
            }
        )
        try:
            bus.publish(stream, envelope, client=self.rds)
        except redis.RedisError:
            log.warning("event publish failed", extra={"type": type_})

    def order(self, order_id: uuid.UUID, to_state: str, **fields: Any) -> None:
        at = self.now()
        with Session(self.engine) as s, s.begin():
            row = s.get(Order, order_id, with_for_update=True)
            if row is None:
                return
            before = row.state
            row.state = to_state
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = at
            payload = _order_payload(row)
        if before != to_state:
            self._publish(
                ORDERS_STREAM, "order.state_changed", {**payload, "from_state": before, "to_state": to_state}
            )

    def new_order(self, row: Order) -> None:
        with Session(self.engine) as s, s.begin():
            if s.scalar(select(Order.id).where(Order.cl_ord_id == row.cl_ord_id)) is None:
                s.add(row)
                s.flush()
                payload = _order_payload(row)
            else:
                return
        self._publish(
            ORDERS_STREAM,
            "order.state_changed",
            {**payload, "from_state": None, "to_state": payload["state"]},
        )

    def algo(self, cl_id: str, to_state: str, *, new: AlgoOrder | None = None, **fields: Any) -> None:
        at = self.now()
        with Session(self.engine) as s, s.begin():
            row = s.scalar(select(AlgoOrder).where(AlgoOrder.algo_cl_ord_id == cl_id).with_for_update())
            if row is None:
                if new is None:
                    return
                s.add(new)
                s.flush()
                row = new
                before = None
            else:
                before = row.state
            row.state = to_state
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = at
            payload = {
                "algo_order_id": str(row.id),
                "order_id": str(row.order_id) if row.order_id else None,
                "algo_id": row.algo_id,
                "algo_cl_ord_id": row.algo_cl_ord_id,
                "inst_id": row.inst_id,
                "kind": row.kind,
                "side": row.side,
                "sl_trigger_px": _s(row.sl_trigger_px if row.kind == "oco" else row.trigger_px),
                "tp_trigger_px": _s(row.tp_trigger_px),
                "sz": _s(row.sz),
                "state": row.state,
                "origin": row.origin,
            }
        if before != to_state:
            self._publish(
                ORDERS_STREAM,
                "algo_order.state_changed",
                {**payload, "from_state": before, "to_state": to_state},
            )

    def proposal_sent(self) -> None:
        with Session(self.engine) as s, s.begin():
            row = s.get(Proposal, self.plan.proposal_id, with_for_update=True)
            if row is not None and row.status in EXECUTABLE_PROPOSAL_STATUSES:
                row.status = "sent"
                row.updated_at = self.now()

    def warroom(self, kind: str, content_md: str, card: dict[str, Any]) -> None:
        self._publish(
            WARROOM_STREAM,
            "warroom.message",
            {
                "thread": {"kind": "main", "key": "main"},
                "author_type": "system",
                "author": WARROOM_AUTHOR,
                "kind": kind,
                "content_md": content_md,
                "card": card,
                "reply_to": None,
                "mentions": [],
                "reasoning_md": None,
                "tool_calls": None,
                "tokens_in": None,
                "tokens_out": None,
                "cost_usd": None,
            },
            source=WARROOM_SOURCE,
        )

    def alert(self, code: str, detail: str, card: dict[str, Any]) -> None:
        self._publish(SYSTEM_STREAM, "system.alert", {"code": code, "detail": detail, **card})
        self.warroom("system_alert", detail, {"code": code, **card})


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _order_payload(row: Order) -> dict[str, Any]:
    return {
        "order_id": str(row.id),
        "cl_ord_id": row.cl_ord_id,
        "ord_id": row.ord_id,
        "inst_id": row.inst_id,
        "side": row.side,
        "pos_side": row.pos_side,
        "ord_type": row.ord_type,
        "state": row.state,
        "px": _s(row.px),
        "sz": _s(row.sz),
        "filled_sz": _s(row.filled_sz),
        "avg_px": _s(row.avg_px),
        "origin": row.origin,
        "purpose": row.purpose,
        "reduce_only": row.reduce_only,
        "proposal_id": str(row.proposal_id) if row.proposal_id else None,
        "risk_check_id": str(row.risk_check_id) if row.risk_check_id else None,
    }


def _new_payload(engine: Engine, order_id: uuid.UUID) -> dict[str, Any]:
    with Session(engine) as s:
        row = s.get(Order, order_id)
        return _order_payload(row) if row is not None else {"order_id": str(order_id)}
