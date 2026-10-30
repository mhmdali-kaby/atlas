"""Pydantic models for OKX v5 trade and account-write responses (ATL-141).

Same rules as ``trading_core.okx.models``: every number parses to ``Decimal`` (never ``float``), ``""``
parses to ``None``, millisecond timestamps parse to aware UTC ``datetime``, unknown fields are ignored.
"""

from __future__ import annotations

from pydantic import Field

from trading_core.okx.models import Dec, Ms, MsRequired, OkxModel, Str


class OrderAck(OkxModel):
    """One row of trade/order, trade/batch-orders or trade/cancel-order. ``s_code`` "0" = accepted."""

    cl_ord_id: Str = None
    ord_id: Str = None
    tag: Str = None
    s_code: str = "0"
    s_msg: str = ""
    ts: Ms = None

    @property
    def ok(self) -> bool:
        return self.s_code == "0"


class AmendAck(OkxModel):
    """One row of trade/amend-order."""

    cl_ord_id: Str = None
    ord_id: Str = None
    req_id: Str = None
    s_code: str = "0"
    s_msg: str = ""
    ts: Ms = None

    @property
    def ok(self) -> bool:
        return self.s_code == "0"


class AlgoAck(OkxModel):
    """One row of trade/order-algo or trade/cancel-algos."""

    algo_id: Str = None
    algo_cl_ord_id: Str = None
    cl_ord_id: Str = None
    tag: Str = None
    s_code: str = "0"
    s_msg: str = ""

    @property
    def ok(self) -> bool:
        return self.s_code == "0"


class ClosePositionAck(OkxModel):
    """trade/close-position: OKX answers with the instrument and side it closed."""

    inst_id: str
    pos_side: Str = None
    cl_ord_id: Str = None
    tag: Str = None


class AttachedAlgo(OkxModel):
    """A TP/SL attached to an order (``attachAlgoOrds`` in order details)."""

    attach_algo_id: Str = None
    attach_algo_cl_ord_id: Str = None
    tp_trigger_px: Dec = None
    tp_ord_px: Dec = None
    sl_trigger_px: Dec = None
    sl_ord_px: Dec = None
    sz: Dec = None
    state: Str = None
    fail_code: Str = None
    fail_reason: Str = None


class OrderDetail(OkxModel):
    """GET trade/order, trade/orders-pending and trade/orders-history rows."""

    inst_type: str
    inst_id: str
    ord_id: str
    cl_ord_id: Str = None
    tag: Str = None
    px: Dec = None
    sz: Dec = None
    ord_type: str
    side: str
    pos_side: Str = None
    td_mode: Str = None
    acc_fill_sz: Dec = None
    fill_px: Dec = None
    fill_sz: Dec = None
    avg_px: Dec = None
    state: str
    lever: Dec = None
    fee: Dec = None
    fee_ccy: Str = None
    pnl: Dec = None
    reduce_only: Str = None
    cancel_source: Str = None
    attach_algo_ords: list[AttachedAlgo] = Field(default_factory=list)
    c_time: Ms = None
    u_time: Ms = None


class AlgoOrderDetail(OkxModel):
    """trade/orders-algo-pending rows. ``ord_type`` conditional (one of TP/SL), oco, trigger, …"""

    algo_id: str
    algo_cl_ord_id: Str = None
    inst_type: Str = None
    inst_id: str
    ord_type: str
    side: Str = None
    pos_side: Str = None
    td_mode: Str = None
    sz: Dec = None
    close_fraction: Dec = None
    state: str
    sl_trigger_px: Dec = None
    sl_ord_px: Dec = None
    tp_trigger_px: Dec = None
    tp_ord_px: Dec = None
    trigger_px: Dec = None
    ord_px: Dec = None
    reduce_only: Str = None
    actual_px: Dec = None
    actual_sz: Dec = None
    ord_id: Str = None
    c_time: Ms = None
    u_time: Ms = None


class FillDetail(OkxModel):
    """trade/fills and trade/fills-history rows. ``fee`` negative = paid, positive = rebate."""

    inst_type: Str = None
    inst_id: str
    trade_id: str
    ord_id: str
    cl_ord_id: Str = None
    bill_id: Str = None
    tag: Str = None
    side: str
    pos_side: Str = None
    fill_px: Dec = None
    fill_sz: Dec = None
    fee: Dec = None
    fee_ccy: Str = None
    exec_type: Str = None
    fill_pnl: Dec = None
    ts: MsRequired


class LeverageResult(OkxModel):
    """account/set-leverage."""

    lever: Dec = None
    mgn_mode: str
    inst_id: Str = None
    pos_side: Str = None


class MaxSize(OkxModel):
    """account/max-size: the largest buy and sell sizes (contracts) at the current leverage."""

    inst_id: str
    ccy: Str = None
    max_buy: Dec = None
    max_sell: Dec = None


TRADE_MODELS: tuple[type[OkxModel], ...] = (
    OrderAck,
    AmendAck,
    AlgoAck,
    ClosePositionAck,
    AttachedAlgo,
    OrderDetail,
    AlgoOrderDetail,
    FillDetail,
    LeverageResult,
    MaxSize,
)

__all__ = [
    "TRADE_MODELS",
    "AlgoAck",
    "AlgoOrderDetail",
    "AmendAck",
    "AttachedAlgo",
    "ClosePositionAck",
    "FillDetail",
    "LeverageResult",
    "MaxSize",
    "OrderAck",
    "OrderDetail",
]
