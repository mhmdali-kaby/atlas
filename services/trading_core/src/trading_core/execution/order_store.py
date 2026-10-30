"""Upserts of OKX order and algo-order rows into ``orders`` / ``algo_orders`` / ``fills`` (ATL-142).

Plain functions over (engine, env, row, now), shared by the private WS feed and reconciliation (brief
§4.4), so both apply one rule set:

* An order is matched on (env, clOrdId), then (env, ordId); one Atlas does not know is inserted with
  origin ``external``. Algo orders the same on (env, algoClOrdId), then (env, algoId).
* A row whose ``uTime`` is older than the stored row's ``raw.uTime`` is a late push: it does not move
  state, size or fill totals back (a delayed ``partially_filled`` after ``filled``). Its fill, if any,
  is still recorded.
* ``fillSz > 0`` with a ``tradeId`` inserts one ``fills`` row, deduped on (env, instId, tradeId).

Nothing here talks to OKX or publishes events; callers build event payloads with
:func:`order_payload`, :func:`fill_payload` and :func:`algo_payload`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from atlas_shared import market_data as md
from atlas_shared.db import AlgoOrder, Fill, Order
from sqlalchemy import Engine, select, update
from sqlalchemy.dialects.postgresql import insert

from trading_core.okx.trade_models import AlgoOrderDetail, OrderDetail

logger = logging.getLogger("trading_core.execution.order_store")

ZERO = Decimal(0)
EXTERNAL = "external"

# orders.state CHECK: OKX's order states, with mmp_canceled folded into canceled.
ORDER_STATES = {
    "live": "live",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "canceled": "canceled",
    "mmp_canceled": "canceled",
}
ALGO_STATES = {
    "live",
    "pause",
    "effective",
    "partially_effective",
    "canceled",
    "order_failed",
    "partially_failed",
}


# -- parsing helpers ---------------------------------------------------------------------------------


def s(value: Any) -> str | None:
    return None if value is None else str(value)


def blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def dec(value: Any) -> Decimal | None:
    text = blank(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except ArithmeticError:
        return None


def pos_dec(value: Decimal | None) -> Decimal | None:
    """OKX sends ``-1`` for "market on trigger" and ``0``/``""`` for "whole position"."""
    return value if value is not None and value > 0 else None


def iso(ts: datetime | None) -> str | None:
    return None if ts is None else ts.isoformat().replace("+00:00", "Z")


def _bool(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _is_late(existing: Mapping[Any, Any] | None, u_time: datetime | None) -> bool:
    """True when the stored row was last updated by a newer OKX push than this one."""
    if existing is None or u_time is None:
        return False
    raw = existing.get("raw")
    stored = blank(raw.get("uTime")) if isinstance(raw, Mapping) else None
    if stored is None:
        return False
    try:
        return md.from_ms(stored) > u_time
    except (ValueError, OverflowError):
        return False


def _unclaimed(conn: Any, column: Any, cl: str | None, env: str) -> str | None:
    """The client id to store on an imported row. clOrdId columns are unique across envs; when the
    other env's row already holds this id, the import keeps it only in ``raw`` rather than touching or
    colliding with that row."""
    if not cl:
        return None
    if conn.execute(select(column).where(column == cl).limit(1)).first() is None:
        return cl
    logger.warning("imported order's client id is held by another env", extra={"env": env})
    return None


# -- orders ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderUpsert:
    detail: OrderDetail
    order_id: Any
    prev_state: str | None
    state: str | None
    filled_sz: Decimal | None
    origin: str
    late: bool
    new_fill: dict[str, Any] | None

    @property
    def state_changed(self) -> bool:
        return not self.late and self.state != self.prev_state


def upsert_order(engine: Engine, env: str, row: Mapping[str, Any], now: datetime) -> OrderUpsert | None:
    """Apply one OKX order row. None when an unknown row carries no size (nothing to import)."""
    detail = OrderDetail.model_validate(row)
    state = ORDER_STATES.get(detail.state)
    cl = detail.cl_ord_id
    with engine.begin() as conn:
        existing = None
        if cl:
            existing = (
                conn.execute(select(Order).where(Order.env == env, Order.cl_ord_id == cl).with_for_update())
                .mappings()
                .first()
            )
        if existing is None:
            existing = (
                conn.execute(
                    select(Order).where(Order.env == env, Order.ord_id == detail.ord_id).with_for_update()
                )
                .mappings()
                .first()
            )
        late = _is_late(existing, detail.u_time)
        values: dict[str, Any] = {"ord_id": detail.ord_id, "updated_at": now}
        if not late:
            values.update(
                avg_px=pos_dec(detail.avg_px), fee=detail.fee, fee_ccy=detail.fee_ccy, raw=dict(row)
            )
            if detail.lever is not None and detail.lever >= 1:
                values["lever"] = int(detail.lever)
            if detail.sz is not None and detail.sz > 0:
                values["sz"] = detail.sz
            if detail.acc_fill_sz is not None:
                values["filled_sz"] = detail.acc_fill_sz
            if state is not None:
                values["state"] = state
        if existing is None:
            if detail.sz is None or detail.sz <= 0:
                logger.warning("order push without a size skipped", extra={"ord_id": detail.ord_id})
                return None
            values.update(
                env=env,
                cl_ord_id=_unclaimed(conn, Order.cl_ord_id, cl, env),
                inst_id=detail.inst_id,
                side=detail.side,
                pos_side=detail.pos_side,
                ord_type=detail.ord_type,
                td_mode=detail.td_mode or "cash",
                px=pos_dec(detail.px),
                reduce_only=_bool(detail.reduce_only),
                origin=EXTERNAL,
                purpose="entry",
                created_at=now,
            )
            values.setdefault("state", "live")
            order_id = conn.execute(insert(Order).values(values).returning(Order.id)).scalar_one()
            prev_state: str | None = None
            origin = EXTERNAL
            filled: Decimal | None = values.get("filled_sz", ZERO)
        else:
            order_id = existing["id"]
            prev_state = existing["state"]
            origin = existing["origin"]
            conn.execute(update(Order).where(Order.id == order_id).values(values))
            filled = values.get("filled_sz", existing["filled_sz"])
        cur_state = values.get("state", prev_state)
        new_fill = _insert_fill(conn, env, order_id, detail, row, now)

    return OrderUpsert(
        detail=detail,
        order_id=order_id,
        prev_state=prev_state,
        state=cur_state,
        filled_sz=filled,
        origin=origin,
        late=late,
        new_fill=new_fill,
    )


def _insert_fill(
    conn: Any, env: str, order_id: Any, detail: OrderDetail, row: Mapping[str, Any], now: datetime
) -> dict[str, Any] | None:
    fill_sz = dec(row.get("fillSz"))
    trade_id = blank(row.get("tradeId"))
    if fill_sz is None or fill_sz <= 0 or trade_id is None:
        return None
    fill_ts = row.get("fillTime") or row.get("uTime")
    values = {
        "env": env,
        "order_id": order_id,
        "trade_id": trade_id,
        "ord_id": detail.ord_id,
        "cl_ord_id": detail.cl_ord_id,
        "inst_id": detail.inst_id,
        "side": detail.side,
        "pos_side": detail.pos_side,
        "fill_px": dec(row.get("fillPx")) or ZERO,
        "fill_sz": fill_sz,
        "fee": dec(row.get("fillFee")),
        "fee_ccy": blank(row.get("fillFeeCcy")),
        "exec_type": blank(row.get("execType")),
        "fill_pnl": dec(row.get("fillPnl")),
        "ts": md.from_ms(str(fill_ts)) if blank(fill_ts) else now,
        "raw": dict(row),
        "created_at": now,
        "updated_at": now,
    }
    inserted = conn.execute(
        insert(Fill)
        .values(values)
        .on_conflict_do_nothing(index_elements=[Fill.env, Fill.inst_id, Fill.trade_id])
        .returning(Fill.id)
    ).scalar()
    return None if inserted is None else {**values, "id": inserted}


def order_payload(u: OrderUpsert) -> dict[str, Any]:
    d = u.detail
    return {
        "order_id": str(u.order_id),
        "cl_ord_id": d.cl_ord_id,
        "ord_id": d.ord_id,
        "inst_id": d.inst_id,
        "side": d.side,
        "pos_side": d.pos_side,
        "ord_type": d.ord_type,
        "state": u.state,
        "okx_state": d.state,
        "px": s(pos_dec(d.px)),
        "sz": s(d.sz),
        "filled_sz": s(u.filled_sz),
        "avg_px": s(pos_dec(d.avg_px)),
        "fee": s(d.fee),
        "fee_ccy": d.fee_ccy,
        "origin": u.origin,
        "late": u.late,
        "u_time": iso(d.u_time),
    }


def fill_payload(u: OrderUpsert) -> dict[str, Any] | None:
    f = u.new_fill
    if f is None:
        return None
    return {
        "fill_id": str(f["id"]),
        "order_id": str(u.order_id),
        "trade_id": f["trade_id"],
        "ord_id": f["ord_id"],
        "cl_ord_id": f["cl_ord_id"],
        "inst_id": f["inst_id"],
        "side": f["side"],
        "pos_side": f["pos_side"],
        "fill_px": s(f["fill_px"]),
        "fill_sz": s(f["fill_sz"]),
        "fee": s(f["fee"]),
        "fee_ccy": f["fee_ccy"],
        "exec_type": f["exec_type"],
        "fill_pnl": s(f["fill_pnl"]),
        "ts": iso(f["ts"]),
    }


# -- algo orders -------------------------------------------------------------------------------------


def algo_shape(d: AlgoOrderDetail) -> dict[str, Any] | None:
    """kind + trigger/order prices per the algo_orders CHECKs; None for types Atlas does not store."""
    sl, tp = d.sl_trigger_px, d.tp_trigger_px
    if d.ord_type == "oco" or (d.ord_type == "conditional" and sl is not None and tp is not None):
        return _shape("oco", None, sl, tp, d.sl_ord_px)
    if d.ord_type == "conditional" and sl is not None:
        return _shape("sl", sl, sl, None, d.sl_ord_px)
    if d.ord_type == "conditional" and tp is not None:
        return _shape("tp", tp, None, tp, d.tp_ord_px)
    if d.ord_type == "trigger" and d.trigger_px is not None:
        return _shape("trigger", d.trigger_px, None, None, d.ord_px)
    return None


def _shape(
    kind: str, trigger: Decimal | None, sl: Decimal | None, tp: Decimal | None, ord_px: Decimal | None
) -> dict[str, Any]:
    return {
        "kind": kind,
        "trigger_px": trigger,
        "sl_trigger_px": sl,
        "tp_trigger_px": tp,
        "ord_px": pos_dec(ord_px),
    }


@dataclass(frozen=True)
class AlgoUpsert:
    detail: AlgoOrderDetail
    shape: dict[str, Any] | None
    algo_order_id: Any  # None when the type is not stored
    prev_state: str | None
    state: str | None
    origin: str | None
    late: bool

    @property
    def state_changed(self) -> bool:
        return self.algo_order_id is not None and not self.late and self.state != self.prev_state


def upsert_algo(engine: Engine, env: str, row: Mapping[str, Any], now: datetime) -> AlgoUpsert:
    d = AlgoOrderDetail.model_validate(row)
    state = d.state if d.state in ALGO_STATES else None
    shape = algo_shape(d)
    prev_state: str | None = None
    row_id: Any = None
    origin: str | None = None
    late = False
    with engine.begin() as conn:
        existing = None
        if d.algo_cl_ord_id:
            existing = (
                conn.execute(
                    select(AlgoOrder)
                    .where(AlgoOrder.env == env, AlgoOrder.algo_cl_ord_id == d.algo_cl_ord_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
        if existing is None:
            existing = (
                conn.execute(
                    select(AlgoOrder)
                    .where(AlgoOrder.env == env, AlgoOrder.algo_id == d.algo_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
        late = _is_late(existing, d.u_time)
        values: dict[str, Any] = {"algo_id": d.algo_id, "updated_at": now}
        if not late:
            values["raw"] = dict(row)
            if state is not None:
                values["state"] = state
        if existing is not None:
            row_id, prev_state, origin = existing["id"], existing["state"], existing["origin"]
            conn.execute(update(AlgoOrder).where(AlgoOrder.id == row_id).values(values))
        elif shape is not None:
            values.update(
                shape,
                env=env,
                algo_cl_ord_id=_unclaimed(conn, AlgoOrder.algo_cl_ord_id, d.algo_cl_ord_id, env),
                inst_id=d.inst_id,
                side=d.side,
                pos_side=d.pos_side,
                sz=pos_dec(d.sz),
                reduce_only=_bool(d.reduce_only) if d.reduce_only is not None else True,
                origin=EXTERNAL,
                created_at=now,
            )
            values.setdefault("state", "live")
            row_id = conn.execute(insert(AlgoOrder).values(values).returning(AlgoOrder.id)).scalar_one()
            origin = EXTERNAL
        else:
            logger.info("algo order type not stored", extra={"ord_type": d.ord_type, "algo_id": d.algo_id})
    return AlgoUpsert(
        detail=d,
        shape=shape,
        algo_order_id=row_id,
        prev_state=prev_state,
        state=values.get("state", prev_state),
        origin=origin,
        late=late,
    )


def algo_payload(u: AlgoUpsert) -> dict[str, Any]:
    d = u.detail
    return {
        "algo_order_id": s(u.algo_order_id),
        "algo_id": d.algo_id,
        "algo_cl_ord_id": d.algo_cl_ord_id,
        "inst_id": d.inst_id,
        "ord_type": d.ord_type,
        "kind": u.shape["kind"] if u.shape else None,
        "side": d.side,
        "pos_side": d.pos_side,
        "state": u.state,
        "okx_state": d.state,
        "sl_trigger_px": s(d.sl_trigger_px),
        "tp_trigger_px": s(d.tp_trigger_px),
        "trigger_px": s(d.trigger_px),
        "sz": s(pos_dec(d.sz)),
        "ord_id": d.ord_id,
        "origin": u.origin,
        "late": u.late,
        "u_time": iso(d.u_time),
    }


__all__ = [
    "ALGO_STATES",
    "EXTERNAL",
    "ORDER_STATES",
    "AlgoUpsert",
    "OrderUpsert",
    "algo_payload",
    "algo_shape",
    "fill_payload",
    "order_payload",
    "upsert_algo",
    "upsert_order",
]
