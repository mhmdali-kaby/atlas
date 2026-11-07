"""The OKX operations of one execution: place with retries, look up, protect, verify, close."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from atlas_shared.db import Order
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_core.execution.config import CANCEL_NOOP_CODES, ORDER_NOT_FOUND, ExecutorConfig
from trading_core.execution.plan import _algo_row, _Plan
from trading_core.execution.recorder import _OKX_ORDER_STATES, _Recorder
from trading_core.okx.errors import OkxClientError, OkxHttpError, OkxNetworkError, OkxResponseError
from trading_core.okx.trade import (
    AlgoOrderRequest,
    AttachedTpSl,
    OkxTradeClient,
    OkxTradeError,
    OrderRequest,
)
from trading_core.okx.trade_models import AlgoOrderDetail, OrderDetail

log = logging.getLogger("trading_core.execution")

Sleep = Callable[[float], Awaitable[None]]


def _retryable(err: Exception, cfg: ExecutorConfig) -> bool:
    if isinstance(err, (OkxNetworkError, OkxResponseError)):
        return True
    if isinstance(err, OkxHttpError):
        return err.status == 429 or err.status >= 500
    if isinstance(err, OkxTradeError):
        status = err.http_status
        return (status is not None and (status == 429 or status >= 500)) or err.s_code in cfg.retryable_codes
    return False


@dataclass
class _Placed:
    ord_id: str | None
    detail: OrderDetail | None = None


class _Executor:
    def __init__(
        self, plan: _Plan, trade: OkxTradeClient, rec: _Recorder, sleep: Sleep, mono: Callable[[], float]
    ):
        self.plan = plan
        self.cfg = plan.cfg
        self.trade = trade
        self.rec = rec
        self.sleep = sleep
        self.mono = mono

    async def lookup(self, cl_ord_id: str) -> OrderDetail | None:
        """The order OKX holds under ``cl_ord_id``, or None (absent, or OKX could not say)."""
        try:
            return await self.trade.get_order(self.plan.inst_id, cl_ord_id=cl_ord_id)
        except OkxTradeError as err:
            if err.s_code != ORDER_NOT_FOUND:
                log.warning("order lookup failed", extra={"code": err.s_code})
            return None
        except OkxClientError as err:
            log.warning("order lookup failed", extra={"error": type(err).__name__})
            return None

    async def place(self, req: OrderRequest) -> _Placed:
        """Send ``req`` with up to ``order_max_retries`` attempts and the same clOrdId; before every
        retry ask OKX whether the previous attempt landed. Raises the last error when it did not."""
        last: Exception | None = None
        for attempt in range(1, self.cfg.order_max_retries + 1):
            if attempt > 1:
                await self.sleep(self.cfg.backoff(attempt - 1))
                found = await self.lookup(req.cl_ord_id)
                if found is not None:
                    return _Placed(found.ord_id, found)
            try:
                ack = await self.trade.place_order(req)
                return _Placed(ack.ord_id or None)
            except OkxTradeError as err:
                if err.duplicate_cl_ord_id:
                    found = await self.lookup(req.cl_ord_id)
                    if found is not None:
                        return _Placed(found.ord_id, found)
                    last = err
                    continue
                if not _retryable(err, self.cfg):
                    raise
                last = err
            except OkxClientError as err:
                if not _retryable(err, self.cfg):
                    raise
                last = err
            log.warning("order attempt failed", extra={"attempt": attempt, "error": type(last).__name__})
        found = await self.lookup(req.cl_ord_id)
        if found is not None:
            return _Placed(found.ord_id, found)
        assert last is not None
        raise last

    async def idempotent(self, call: Callable[[], Awaitable[Any]]) -> Any:
        last: Exception | None = None
        for attempt in range(1, self.cfg.order_max_retries + 1):
            if attempt > 1:
                await self.sleep(self.cfg.backoff(attempt - 1))
            try:
                return await call()
            except OkxClientError as err:
                if not _retryable(err, self.cfg):
                    raise
                last = err
        assert last is not None
        raise last

    def entry_request(self, cl_ord_id: str, attach: bool) -> OrderRequest:
        p = self.plan
        attached: tuple[AttachedTpSl, ...] = ()
        if attach:
            attached = (
                AttachedTpSl(
                    sl_trigger_px=p.stop,
                    tp_trigger_px=p.tp1,
                    attach_algo_cl_ord_id=p.attach_id,
                ),
            )
        return OrderRequest(
            inst_id=p.inst_id,
            td_mode=p.td_mode,  # type: ignore[arg-type]
            side=p.side,  # type: ignore[arg-type]
            ord_type=p.ord_type,  # type: ignore[arg-type]
            sz=p.sz,
            cl_ord_id=cl_ord_id,
            px=p.px,
            pos_side=p.pos_side,  # type: ignore[arg-type]
            attach_algo_ords=attached,
        )

    async def protect_with_algo(self) -> AlgoOrderDetail | None:
        """Place the stand-alone OCO (or SL-only conditional) for a bare entry, retrying with the same
        algoClOrdId. Returns None when OKX refused it."""
        p = self.plan
        oco = p.tp1 is not None
        req = AlgoOrderRequest(
            inst_id=p.inst_id,
            td_mode=p.td_mode,  # type: ignore[arg-type]
            side=p.close_side,  # type: ignore[arg-type]
            ord_type="oco" if oco else "conditional",
            algo_cl_ord_id=p.oco_id,
            sz=p.sz,
            pos_side=p.pos_side,  # type: ignore[arg-type]
            reduce_only=True,
            sl_trigger_px=p.stop,
            tp_trigger_px=p.tp1,
        )
        at = self.rec.now()
        self.rec.algo(p.oco_id, "sending", new=_algo_row(p, uuid.uuid4(), p.oco_id, "order-algo", at))
        last: Exception | None = None
        for attempt in range(1, self.cfg.order_max_retries + 1):
            if attempt > 1:
                await self.sleep(self.cfg.backoff(attempt - 1))
                if (found := await self.find_algo(p.oco_id)) is not None:
                    self.rec.algo(p.oco_id, "live", algo_id=found.algo_id)
                    return found
            try:
                ack = await self.trade.place_algo_order(req)
                self.rec.algo(p.oco_id, "live", algo_id=ack.algo_id)
                return None
            except OkxClientError as err:
                last = err
                if not _retryable(err, self.cfg):
                    break
        code = last.s_code if isinstance(last, OkxTradeError) else None
        msg = last.human_message if isinstance(last, OkxTradeError) else str(last)
        self.rec.algo(p.oco_id, "failed", last_error_code=code, last_error_msg=msg)
        return None

    async def find_algo(self, algo_cl_ord_id: str, ord_id: str | None = None) -> AlgoOrderDetail | None:
        """A pending SL/OCO algo on OKX carrying a stop for this entry, or None. An ``effective`` algo
        has already triggered, so it no longer protects anything."""
        try:
            rows = await self.trade.get_algo_orders_pending("conditional,oco", inst_id=self.plan.inst_id)
        except OkxClientError as err:
            log.warning("algo lookup failed", extra={"error": type(err).__name__})
            return None
        for row in rows:
            ours = row.algo_cl_ord_id == algo_cl_ord_id or (ord_id is not None and row.ord_id == ord_id)
            if ours and row.sl_trigger_px is not None and row.state in ("live", "partially_effective"):
                return row
        return None

    async def stop_present(
        self, cl_ord_id: str, algo_cl_ord_id: str, attached: bool
    ) -> tuple[bool, OrderDetail | None]:
        detail = await self.lookup(cl_ord_id)
        filled = (detail.acc_fill_sz or Decimal(0)) if detail is not None else Decimal(0)
        algo = await self.find_algo(algo_cl_ord_id, detail.ord_id if detail is not None else None)
        if algo is not None:
            self.rec.algo(algo_cl_ord_id, "live", algo_id=algo.algo_id)
            return True, detail
        if attached and detail is not None and detail.state in ("live", "partially_filled"):
            # Not yet (fully) filled: OKX holds the stop on the order itself until the fill.
            for a in detail.attach_algo_ords:
                if (
                    a.sl_trigger_px is not None
                    and not a.fail_code
                    and (filled == 0 or detail.state == "partially_filled")
                ):
                    if a.attach_algo_id:
                        self.rec.algo(algo_cl_ord_id, "live", algo_id=a.attach_algo_id)
                    else:
                        self.rec.algo(algo_cl_ord_id, "live")
                    return True, detail
        return False, detail

    async def verify(
        self, cl_ord_id: str, algo_cl_ord_id: str, attached: bool
    ) -> tuple[bool, OrderDetail | None]:
        deadline = self.mono() + self.cfg.stop_verify_seconds
        while True:
            ok, detail = await self.stop_present(cl_ord_id, algo_cl_ord_id, attached)
            if ok:
                return True, detail
            if detail is not None and detail.state in ("canceled", "mmp_canceled") and not detail.acc_fill_sz:
                return False, detail
            if self.mono() >= deadline:
                return False, detail
            await self.sleep(self.cfg.poll_seconds)

    async def close_unprotected(self, cl_ord_id: str, detail: OrderDetail | None) -> Decimal:
        """Cancel what is left of the entry and market-close (reduce-only) whatever filled."""
        p = self.plan
        if detail is None or detail.state in ("live", "partially_filled"):
            try:
                await self.trade.cancel_order(p.inst_id, cl_ord_id=cl_ord_id)
            except OkxTradeError as err:
                if err.s_code not in CANCEL_NOOP_CODES:
                    log.warning("cancel of unprotected entry failed", extra={"code": err.s_code})
            except OkxClientError as err:
                log.warning("cancel of unprotected entry failed", extra={"error": type(err).__name__})
            detail = await self.lookup(cl_ord_id) or detail
        if detail is not None:
            self.sync_from_detail(p.order_id, detail)
        # If OKX could not tell us, close the whole size: reduce-only can never open a position.
        filled = (detail.acc_fill_sz or Decimal(0)) if detail is not None else p.sz
        if filled <= 0:
            return Decimal(0)
        at = self.rec.now()
        close_id = uuid.uuid4()
        close_row = Order(
            id=close_id,
            env=p.env,
            proposal_id=p.proposal_id,
            risk_check_id=p.risk_check_id,
            cl_ord_id=p.close_id,
            inst_id=p.inst_id,
            side=p.close_side,
            pos_side=p.pos_side,
            ord_type="market",
            td_mode=p.td_mode,
            sz=filled,
            state="new",
            reduce_only=True,
            origin="atlas",
            purpose="close",
            raw={"reason": "stop_unverified"},
            created_at=at,
            updated_at=at,
        )
        self.rec.new_order(close_row)
        with Session(self.rec.engine) as s:
            close_id = s.scalar(select(Order.id).where(Order.cl_ord_id == p.close_id)) or close_id
        self.rec.order(close_id, "sending")
        req = OrderRequest(
            inst_id=p.inst_id,
            td_mode=p.td_mode,  # type: ignore[arg-type]
            side=p.close_side,  # type: ignore[arg-type]
            ord_type="market",
            sz=filled,
            cl_ord_id=p.close_id,
            pos_side=p.pos_side,  # type: ignore[arg-type]
            reduce_only=True,
        )
        try:
            placed = await self.place(req)
            self.rec.order(close_id, "live", ord_id=placed.ord_id)
        except OkxClientError as err:
            code = err.s_code if isinstance(err, OkxTradeError) else None
            msg = err.human_message if isinstance(err, OkxTradeError) else str(err)
            self.rec.order(close_id, "failed", last_error_code=code, last_error_msg=msg)
        return filled

    def sync_from_detail(self, order_id: uuid.UUID, detail: OrderDetail) -> str:
        state = _OKX_ORDER_STATES.get(detail.state, "live")
        fields: dict[str, Any] = {"ord_id": detail.ord_id, "raw": detail.model_dump(mode="json")}
        if detail.acc_fill_sz is not None:
            fields["filled_sz"] = detail.acc_fill_sz
        if detail.avg_px:
            fields["avg_px"] = detail.avg_px
        if detail.fee is not None:
            fields["fee"] = detail.fee
            fields["fee_ccy"] = detail.fee_ccy
        self.rec.order(order_id, state, **fields)
        return state
