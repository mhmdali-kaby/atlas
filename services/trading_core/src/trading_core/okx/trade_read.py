"""Read-only OKX v5 trade-account calls (ATL-127 split of ATL-141's client, brief §4.2).

``OkxTradeReadClient`` holds the GET calls on the trade and account endpoints: order status, open and
finished orders, pending algo orders, fills and ``account/max-size``. Sync, reconciliation and the Risk
Engine read through it. It has no POST transport, so nothing built on it can place, amend or cancel an
order. The write calls live in ``trading_core.okx.trade.OkxTradeClient`` (a subclass), which only the
Executor may import; ``tests/test_risk_engine.py`` enforces that.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

from atlas_shared.defaults import DEFAULTS

from trading_core.okx.client import Cursor, OkxClient, _cursor_ms
from trading_core.okx.error_messages import human_message
from trading_core.okx.errors import OkxError
from trading_core.okx.trade_models import AlgoOrderDetail, FillDetail, MaxSize, OrderDetail

TdMode = Literal["isolated", "cross", "cash"]


CL_ORD_ID = re.compile(r"^[A-Za-z0-9]{1,32}$")
"""OKX clOrdId / algoClOrdId: 1-32 case-sensitive alphanumerics."""

DUPLICATE_CL_ORD_ID = "51016"


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


class OkxTradeReadClient:
    """Read-only trade-account GETs over an authenticated ``OkxClient`` (same env, key, limiter)."""

    def __init__(self, client: OkxClient) -> None:
        self._client = client

    @property
    def env(self) -> str:
        return self._client.env

    async def _get(self, endpoint: str, params: Mapping[str, str | None]) -> list[Any]:
        try:
            return await self._client._get(endpoint, params, signed=True)
        except OkxError as err:
            raise OkxTradeError(err.code, err.msg, path=endpoint, http_status=err.http_status) from err

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


__all__ = ["CL_ORD_ID", "DUPLICATE_CL_ORD_ID", "OkxTradeError", "OkxTradeReadClient"]
