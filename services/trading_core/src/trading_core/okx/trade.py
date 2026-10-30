"""Typed async OKX v5 trade and account-write calls for the Executor (ATL-141, brief §4.2, §11).

Only the Executor (trading_core/execution) may build an ``OkxTradeClient``; agents never reach this
module (brief rule 1). It reuses ``OkxClient``'s signing, ``x-simulated-trading`` header in demo,
per-endpoint rate limits and retries. Retries resend the identical body, so a placed order keeps its
``clOrdId`` (rule 8): a retry of an order that did reach OKX comes back as sCode 51016 (duplicated
clOrdId), which ``OkxTradeError.duplicate_cl_ord_id`` flags so the caller can look the order up instead.

Sizes and prices are ``Decimal`` in and plain decimal strings on the wire; a ``float`` is refused.
OKX refusals raise ``OkxTradeError`` carrying ``s_code``, OKX's ``s_msg`` and an owner-facing
``human_message`` (``trading_core.okx.error_messages``). Batch calls return every row instead, so one
refused item does not hide the others.

There are no withdrawal or transfer calls here or anywhere in Atlas (brief rule 5).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from atlas_shared.defaults import DEFAULTS

from trading_core.okx.client import Cursor, OkxClient, _cursor_ms
from trading_core.okx.error_messages import human_message
from trading_core.okx.errors import OkxError
from trading_core.okx.trade_models import (
    AlgoAck,
    AlgoOrderDetail,
    AmendAck,
    ClosePositionAck,
    FillDetail,
    LeverageResult,
    MaxSize,
    OrderAck,
    OrderDetail,
)

Side = Literal["buy", "sell"]
PosSide = Literal["net", "long", "short"]
TdMode = Literal["isolated", "cross", "cash"]
OrdType = Literal["market", "limit", "post_only", "fok", "ioc"]
AlgoOrdType = Literal["conditional", "oco", "trigger"]
MgnMode = Literal["isolated", "cross"]
TriggerPxType = Literal["last", "index", "mark"]

CL_ORD_ID = re.compile(r"^[A-Za-z0-9]{1,32}$")
"""OKX clOrdId / algoClOrdId: 1-32 case-sensitive alphanumerics."""

MARKET_PX = "-1"
"""OKX's ``tpOrdPx`` / ``slOrdPx`` / ``orderPx`` value for "market on trigger"."""

DUPLICATE_CL_ORD_ID = "51016"


def new_cl_ord_id(prefix: str = "atl") -> str:
    """A fresh 32-character client order ID: ``prefix`` + random hex. Mint once, reuse on retry."""
    if not prefix.isalnum() or not 1 <= len(prefix) <= 8:
        raise ValueError("prefix must be 1-8 alphanumerics")
    return (prefix + uuid.uuid4().hex)[:32]


class OkxTradeError(OkxError):
    """OKX refused a trade or account-write call. ``code`` is the item's sCode when OKX sent one,
    otherwise the top-level code; ``human_message`` is what the War Room shows the owner."""

    def __init__(
        self,
        code: str,
        msg: str,
        *,
        path: str = "",
        http_status: int | None = None,
        cl_ord_id: str | None = None,
        ord_id: str | None = None,
        algo_id: str | None = None,
    ) -> None:
        super().__init__(code, msg, path=path, http_status=http_status)
        self.s_code = code
        self.s_msg = msg
        self.cl_ord_id = cl_ord_id
        self.ord_id = ord_id
        self.algo_id = algo_id
        self.human_message = human_message(code, msg)

    @property
    def duplicate_cl_ord_id(self) -> bool:
        return self.s_code == DUPLICATE_CL_ORD_ID


# ---- requests ------------------------------------------------------------------------------------


def _dec(name: str, value: Decimal, *, positive: bool = True) -> str:
    if isinstance(value, float) or not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite() or (positive and value <= 0):
        raise ValueError(f"{name} must be a finite {'positive ' if positive else ''}Decimal, got {value}")
    return format(value, "f")


def _opt_dec(name: str, value: Decimal | None) -> str | None:
    return None if value is None else _dec(name, value)


def _cl_id(name: str, value: str | None) -> str | None:
    if value is not None and not CL_ORD_ID.match(value):
        raise ValueError(f"{name} must be 1-32 alphanumerics, got {value!r}")
    return value


def _px_or_market(name: str, value: Decimal | None) -> str:
    return MARKET_PX if value is None else _dec(name, value)


def _compact(body: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if v is not None}


@dataclass(frozen=True)
class AttachedTpSl:
    """A server-side TP and/or SL sent with the entry (``attachAlgoOrds``, brief rule 3).

    ``*_ord_px`` None = market on trigger. ``sz`` None = the whole order (OKX's default).
    """

    sl_trigger_px: Decimal | None = None
    tp_trigger_px: Decimal | None = None
    sl_ord_px: Decimal | None = None
    tp_ord_px: Decimal | None = None
    attach_algo_cl_ord_id: str | None = None
    sl_trigger_px_type: TriggerPxType = "last"
    tp_trigger_px_type: TriggerPxType = "last"
    sz: Decimal | None = None

    def to_okx(self) -> dict[str, Any]:
        if self.sl_trigger_px is None and self.tp_trigger_px is None:
            raise ValueError("an attached TP/SL needs a stop-loss or a take-profit trigger price")
        body: dict[str, Any] = {
            "attachAlgoClOrdId": _cl_id("attach_algo_cl_ord_id", self.attach_algo_cl_ord_id),
            "sz": _opt_dec("sz", self.sz),
        }
        if self.sl_trigger_px is not None:
            body |= {
                "slTriggerPx": _dec("sl_trigger_px", self.sl_trigger_px),
                "slOrdPx": _px_or_market("sl_ord_px", self.sl_ord_px),
                "slTriggerPxType": self.sl_trigger_px_type,
            }
        if self.tp_trigger_px is not None:
            body |= {
                "tpTriggerPx": _dec("tp_trigger_px", self.tp_trigger_px),
                "tpOrdPx": _px_or_market("tp_ord_px", self.tp_ord_px),
                "tpTriggerPxType": self.tp_trigger_px_type,
            }
        return _compact(body)


@dataclass(frozen=True)
class OrderRequest:
    """One order for trade/order or trade/batch-orders. ``px`` is required for every type but market."""

    inst_id: str
    td_mode: TdMode
    side: Side
    ord_type: OrdType
    sz: Decimal
    cl_ord_id: str
    px: Decimal | None = None
    pos_side: PosSide | None = None
    reduce_only: bool = False
    tag: str | None = None
    attach_algo_ords: Sequence[AttachedTpSl] = field(default_factory=tuple)

    def to_okx(self) -> dict[str, Any]:
        if _cl_id("cl_ord_id", self.cl_ord_id) is None:
            raise ValueError("cl_ord_id is required (brief rule 8)")
        if self.ord_type != "market" and self.px is None:
            raise ValueError(f"a {self.ord_type} order needs px")
        body: dict[str, Any] = {
            "instId": self.inst_id,
            "tdMode": self.td_mode,
            "side": self.side,
            "posSide": self.pos_side,
            "ordType": self.ord_type,
            "sz": _dec("sz", self.sz),
            "px": _opt_dec("px", self.px) if self.ord_type != "market" else None,
            "clOrdId": self.cl_ord_id,
            "tag": self.tag,
            "reduceOnly": True if self.reduce_only else None,
        }
        if self.attach_algo_ords:
            body["attachAlgoOrds"] = [a.to_okx() for a in self.attach_algo_ords]
        return _compact(body)


@dataclass(frozen=True)
class AlgoOrderRequest:
    """One trade/order-algo request.

    ``conditional`` = one TP or SL (or both, OKX treats both as one-cancels-the-other), ``oco`` = both
    TP and SL, ``trigger`` = an order sent when ``trigger_px`` is reached. ``sz`` None with
    ``close_fraction`` Decimal("1") closes the whole position. ``*_ord_px`` None = market.
    """

    inst_id: str
    td_mode: TdMode
    side: Side
    ord_type: AlgoOrdType
    algo_cl_ord_id: str
    sz: Decimal | None = None
    close_fraction: Decimal | None = None
    pos_side: PosSide | None = None
    reduce_only: bool = True
    sl_trigger_px: Decimal | None = None
    sl_ord_px: Decimal | None = None
    tp_trigger_px: Decimal | None = None
    tp_ord_px: Decimal | None = None
    trigger_px: Decimal | None = None
    order_px: Decimal | None = None
    sl_trigger_px_type: TriggerPxType = "last"
    tp_trigger_px_type: TriggerPxType = "last"
    trigger_px_type: TriggerPxType = "last"
    tag: str | None = None

    def to_okx(self) -> dict[str, Any]:
        if _cl_id("algo_cl_ord_id", self.algo_cl_ord_id) is None:
            raise ValueError("algo_cl_ord_id is required (brief rule 8)")
        if (self.sz is None) == (self.close_fraction is None):
            raise ValueError("give exactly one of sz and close_fraction")
        body: dict[str, Any] = {
            "instId": self.inst_id,
            "tdMode": self.td_mode,
            "side": self.side,
            "posSide": self.pos_side,
            "ordType": self.ord_type,
            "sz": _opt_dec("sz", self.sz),
            "closeFraction": _opt_dec("close_fraction", self.close_fraction),
            "reduceOnly": True if self.reduce_only else None,
            "algoClOrdId": self.algo_cl_ord_id,
            "tag": self.tag,
        }
        if self.ord_type == "trigger":
            if self.trigger_px is None:
                raise ValueError("a trigger order needs trigger_px")
            body |= {
                "triggerPx": _dec("trigger_px", self.trigger_px),
                "orderPx": _px_or_market("order_px", self.order_px),
                "triggerPxType": self.trigger_px_type,
            }
            return _compact(body)
        if self.ord_type == "oco" and (self.sl_trigger_px is None or self.tp_trigger_px is None):
            raise ValueError("an oco order needs both sl_trigger_px and tp_trigger_px")
        if self.sl_trigger_px is None and self.tp_trigger_px is None:
            raise ValueError("a conditional order needs sl_trigger_px or tp_trigger_px")
        if self.sl_trigger_px is not None:
            body |= {
                "slTriggerPx": _dec("sl_trigger_px", self.sl_trigger_px),
                "slOrdPx": _px_or_market("sl_ord_px", self.sl_ord_px),
                "slTriggerPxType": self.sl_trigger_px_type,
            }
        if self.tp_trigger_px is not None:
            body |= {
                "tpTriggerPx": _dec("tp_trigger_px", self.tp_trigger_px),
                "tpOrdPx": _px_or_market("tp_ord_px", self.tp_ord_px),
                "tpTriggerPxType": self.tp_trigger_px_type,
            }
        return _compact(body)


# ---- client --------------------------------------------------------------------------------------


def _check(ack: OrderAck | AmendAck | AlgoAck, path: str) -> None:
    if not ack.ok:
        raise OkxTradeError(
            ack.s_code,
            ack.s_msg,
            path=path,
            cl_ord_id=getattr(ack, "cl_ord_id", None),
            ord_id=getattr(ack, "ord_id", None),
            algo_id=getattr(ack, "algo_id", None),
        )


def ack_error(ack: OrderAck | AmendAck | AlgoAck, path: str = "") -> OkxTradeError | None:
    """The typed error for a refused batch row, or None when OKX accepted it."""
    try:
        _check(ack, path)
    except OkxTradeError as err:
        return err
    return None


def _one(rows: list[Any], path: str) -> Any:
    if not rows:
        raise OkxTradeError("empty", "OKX returned no result", path=path)
    return rows[0]


def _id_pair(ord_id: str | None, cl_ord_id: str | None) -> dict[str, str]:
    if (ord_id is None) == (cl_ord_id is None):
        raise ValueError("give exactly one of ord_id and cl_ord_id")
    return {"ordId": ord_id} if ord_id is not None else {"clOrdId": str(_cl_id("cl_ord_id", cl_ord_id))}


def _page_limit(endpoint: str, limit: int | None) -> str | None:
    if limit is None:
        return None
    cap = int(DEFAULTS["okx_rest"]["page_limits"][endpoint])
    if not 1 <= limit <= cap:
        raise ValueError(f"{endpoint} limit must be 1..{cap}, got {limit}")
    return str(limit)


class OkxTradeClient:
    """Trade and account-write calls over an authenticated ``OkxClient`` (same env, key, limiter).

    The retry count is the wrapped client's ``RetryPolicy.max_attempts``; the Executor builds that
    client with ``max_attempts = settings.order_max_retries``.
    """

    def __init__(self, client: OkxClient) -> None:
        self._client = client

    @property
    def env(self) -> str:
        return self._client.env

    async def _post(self, endpoint: str, payload: object, *, partial_ok: bool = True) -> list[Any]:
        try:
            return await self._client._post(endpoint, payload, partial_ok=partial_ok)
        except OkxTradeError:
            raise
        except OkxError as err:
            raise OkxTradeError(err.code, err.msg, path=endpoint, http_status=err.http_status) from err

    async def _get(self, endpoint: str, params: Mapping[str, str | None]) -> list[Any]:
        try:
            return await self._client._get(endpoint, params, signed=True)
        except OkxError as err:
            raise OkxTradeError(err.code, err.msg, path=endpoint, http_status=err.http_status) from err

    # ---- orders ----------------------------------------------------------------------------------

    async def place_order(self, order: OrderRequest) -> OrderAck:
        """POST trade/order. Raises ``OkxTradeError`` when OKX refuses it."""
        path = "trade/order"
        ack = OrderAck.model_validate(_one(await self._post(path, order.to_okx()), path))
        _check(ack, path)
        return ack

    async def place_batch_orders(self, orders: Sequence[OrderRequest]) -> list[OrderAck]:
        """POST trade/batch-orders. Returns one row per order, accepted or not (see ``ack_error``)."""
        path = "trade/batch-orders"
        cap = int(DEFAULTS["okx_rest"]["page_limits"][path])
        if not 1 <= len(orders) <= cap:
            raise ValueError(f"{path} takes 1..{cap} orders, got {len(orders)}")
        rows = await self._post(path, [o.to_okx() for o in orders])
        return [OrderAck.model_validate(r) for r in rows]

    async def amend_order(
        self,
        inst_id: str,
        *,
        ord_id: str | None = None,
        cl_ord_id: str | None = None,
        new_sz: Decimal | None = None,
        new_px: Decimal | None = None,
        req_id: str | None = None,
        cxl_on_fail: bool = False,
    ) -> AmendAck:
        """POST trade/amend-order (new size and/or price)."""
        path = "trade/amend-order"
        if new_sz is None and new_px is None:
            raise ValueError("give new_sz or new_px")
        body = _compact(
            {
                "instId": inst_id,
                **_id_pair(ord_id, cl_ord_id),
                "newSz": _opt_dec("new_sz", new_sz),
                "newPx": _opt_dec("new_px", new_px),
                "reqId": _cl_id("req_id", req_id),
                "cxlOnFail": True if cxl_on_fail else None,
            }
        )
        ack = AmendAck.model_validate(_one(await self._post(path, body), path))
        _check(ack, path)
        return ack

    async def cancel_order(
        self, inst_id: str, *, ord_id: str | None = None, cl_ord_id: str | None = None
    ) -> OrderAck:
        """POST trade/cancel-order."""
        path = "trade/cancel-order"
        body = {"instId": inst_id, **_id_pair(ord_id, cl_ord_id)}
        ack = OrderAck.model_validate(_one(await self._post(path, body), path))
        _check(ack, path)
        return ack

    async def close_position(
        self,
        inst_id: str,
        mgn_mode: MgnMode,
        *,
        pos_side: PosSide | None = None,
        ccy: str | None = None,
        auto_cxl: bool = False,
        cl_ord_id: str | None = None,
        tag: str | None = None,
    ) -> ClosePositionAck:
        """POST trade/close-position: market-close the whole position. ``auto_cxl`` cancels pending
        close orders that would otherwise block it."""
        path = "trade/close-position"
        body = _compact(
            {
                "instId": inst_id,
                "mgnMode": mgn_mode,
                "posSide": pos_side,
                "ccy": ccy,
                "autoCxl": True if auto_cxl else None,
                "clOrdId": _cl_id("cl_ord_id", cl_ord_id),
                "tag": tag,
            }
        )
        return ClosePositionAck.model_validate(_one(await self._post(path, body, partial_ok=False), path))

    async def get_order(
        self, inst_id: str, *, ord_id: str | None = None, cl_ord_id: str | None = None
    ) -> OrderDetail:
        """GET trade/order: one order's current state."""
        path = "trade/order"
        rows = await self._get(path, {"instId": inst_id, **_id_pair(ord_id, cl_ord_id)})
        return OrderDetail.model_validate(_one(rows, path))

    async def get_orders_pending(
        self,
        *,
        inst_type: str | None = None,
        inst_id: str | None = None,
        ord_type: str | None = None,
        state: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int | None = None,
    ) -> list[OrderDetail]:
        """GET trade/orders-pending: open orders, newest first. ``after``/``before`` are ordId cursors."""
        path = "trade/orders-pending"
        params = {
            "instType": inst_type,
            "instId": inst_id,
            "ordType": ord_type,
            "state": state,
            "after": after,
            "before": before,
            "limit": _page_limit(path, limit),
        }
        return [OrderDetail.model_validate(r) for r in await self._get(path, params)]

    async def get_orders_history(
        self,
        inst_type: str,
        *,
        inst_id: str | None = None,
        ord_type: str | None = None,
        state: str | None = None,
        after: str | None = None,
        before: str | None = None,
        begin: Cursor = None,
        end: Cursor = None,
        limit: int | None = None,
    ) -> list[OrderDetail]:
        """GET trade/orders-history: finished orders of the last 7 days. ``begin``/``end`` bound cTime."""
        path = "trade/orders-history"
        params = {
            "instType": inst_type,
            "instId": inst_id,
            "ordType": ord_type,
            "state": state,
            "after": after,
            "before": before,
            "begin": _cursor_ms(begin),
            "end": _cursor_ms(end),
            "limit": _page_limit(path, limit),
        }
        return [OrderDetail.model_validate(r) for r in await self._get(path, params)]

    # ---- algo orders -----------------------------------------------------------------------------

    async def place_algo_order(self, order: AlgoOrderRequest) -> AlgoAck:
        """POST trade/order-algo (a stand-alone TP/SL, OCO or trigger order)."""
        path = "trade/order-algo"
        ack = AlgoAck.model_validate(_one(await self._post(path, order.to_okx()), path))
        _check(ack, path)
        return ack

    async def cancel_algos(self, algos: Sequence[tuple[str, str]]) -> list[AlgoAck]:
        """POST trade/cancel-algos with ``(inst_id, algo_id)`` pairs. Returns one row per algo order."""
        path = "trade/cancel-algos"
        cap = int(DEFAULTS["okx_rest"]["page_limits"][path])
        if not 1 <= len(algos) <= cap:
            raise ValueError(f"{path} takes 1..{cap} algo orders, got {len(algos)}")
        rows = await self._post(path, [{"instId": inst, "algoId": algo} for inst, algo in algos])
        return [AlgoAck.model_validate(r) for r in rows]

    async def get_algo_orders_pending(
        self,
        ord_type: str,
        *,
        inst_type: str | None = None,
        inst_id: str | None = None,
        algo_id: str | None = None,
        algo_cl_ord_id: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int | None = None,
    ) -> list[AlgoOrderDetail]:
        """GET trade/orders-algo-pending. ``ord_type`` is required by OKX: "conditional", "oco",
        "trigger", or "conditional,oco" for both TP/SL kinds."""
        path = "trade/orders-algo-pending"
        params = {
            "ordType": ord_type,
            "instType": inst_type,
            "instId": inst_id,
            "algoId": algo_id,
            "algoClOrdId": algo_cl_ord_id,
            "after": after,
            "before": before,
            "limit": _page_limit(path, limit),
        }
        return [AlgoOrderDetail.model_validate(r) for r in await self._get(path, params)]

    # ---- fills -----------------------------------------------------------------------------------

    async def get_fills(
        self,
        *,
        inst_type: str | None = None,
        inst_id: str | None = None,
        ord_id: str | None = None,
        after: str | None = None,
        before: str | None = None,
        begin: Cursor = None,
        end: Cursor = None,
        limit: int | None = None,
    ) -> list[FillDetail]:
        """GET trade/fills: fills of the last 3 days, newest first (``after``/``before``: billId cursors)."""
        return await self._fills("trade/fills", inst_type, inst_id, ord_id, after, before, begin, end, limit)

    async def get_fills_history(
        self,
        inst_type: str,
        *,
        inst_id: str | None = None,
        ord_id: str | None = None,
        after: str | None = None,
        before: str | None = None,
        begin: Cursor = None,
        end: Cursor = None,
        limit: int | None = None,
    ) -> list[FillDetail]:
        """GET trade/fills-history: fills of the last 3 months (``inst_type`` required by OKX)."""
        return await self._fills(
            "trade/fills-history", inst_type, inst_id, ord_id, after, before, begin, end, limit
        )

    async def _fills(
        self,
        path: str,
        inst_type: str | None,
        inst_id: str | None,
        ord_id: str | None,
        after: str | None,
        before: str | None,
        begin: Cursor,
        end: Cursor,
        limit: int | None,
    ) -> list[FillDetail]:
        params = {
            "instType": inst_type,
            "instId": inst_id,
            "ordId": ord_id,
            "after": after,
            "before": before,
            "begin": _cursor_ms(begin),
            "end": _cursor_ms(end),
            "limit": _page_limit(path, limit),
        }
        return [FillDetail.model_validate(r) for r in await self._get(path, params)]

    # ---- account writes / sizing -----------------------------------------------------------------

    async def set_leverage(
        self, inst_id: str, lever: int, mgn_mode: MgnMode, *, pos_side: PosSide | None = None
    ) -> LeverageResult:
        """POST account/set-leverage for one instrument."""
        path = "account/set-leverage"
        if isinstance(lever, bool) or not isinstance(lever, int) or lever < 1:
            raise ValueError(f"lever must be a positive int, got {lever!r}")
        body = _compact({"instId": inst_id, "lever": str(lever), "mgnMode": mgn_mode, "posSide": pos_side})
        return LeverageResult.model_validate(_one(await self._post(path, body, partial_ok=False), path))

    async def get_max_size(
        self,
        inst_id: str,
        td_mode: TdMode,
        *,
        ccy: str | None = None,
        px: Decimal | None = None,
        leverage: int | None = None,
    ) -> MaxSize:
        """GET account/max-size: the largest order OKX would accept now (Risk Engine §10 check 4)."""
        path = "account/max-size"
        params = {
            "instId": inst_id,
            "tdMode": td_mode,
            "ccy": ccy,
            "px": _opt_dec("px", px),
            "leverage": None if leverage is None else str(leverage),
        }
        return MaxSize.model_validate(_one(await self._get(path, params), path))


__all__ = [
    "MARKET_PX",
    "AlgoOrderRequest",
    "AttachedTpSl",
    "OkxTradeClient",
    "OkxTradeError",
    "OrderRequest",
    "ack_error",
    "new_cl_ord_id",
]
