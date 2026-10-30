"""ATL-141: the typed OKX trade client signs POSTs, sends decimal strings, maps sCodes to plain
English, and keeps every recorded response in Decimal."""

from __future__ import annotations

import base64
import hmac
import json
import types
import typing
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from trading_core.okx import OkxClient, OkxCredentials, OkxError
from trading_core.okx.error_messages import MESSAGES, human_message
from trading_core.okx.signing import iso_timestamp
from trading_core.okx.trade import (
    AlgoOrderRequest,
    AttachedTpSl,
    OkxTradeClient,
    OkxTradeError,
    OrderRequest,
    ack_error,
    new_cl_ord_id,
)
from trading_core.okx.trade_models import TRADE_MODELS

FIXTURES = Path(__file__).parent / "fixtures" / "okx_trade"
BASE = "https://www.okx.com"
SECRET = "atlas-test-only-okx-secret"  # noqa: S105
CREDS = OkxCredentials(api_key="test-key-0000", secret=SECRET, passphrase="test-pass")  # noqa: S106
NOW_MS = 1_793_207_736_680
CL = "atlb1946ac9b2c64f2c8d0b6a1f3e9c7"
TIME = {"code": "0", "msg": "", "data": [{"ts": str(NOW_MS)}]}


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


async def _no_sleep(_: float) -> None:
    return None


def trade_client(env: str = "demo") -> OkxTradeClient:
    client = OkxClient(
        env,
        CREDS,
        http=httpx.AsyncClient(base_url=BASE),
        sleep=_no_sleep,
        wall_clock_ms=lambda: NOW_MS,
        rand=lambda: 1.0,
    )
    return OkxTradeClient(client)


def entry(**overrides: Any) -> OrderRequest:
    values: dict[str, Any] = {
        "inst_id": "BTC-USDT-SWAP",
        "td_mode": "isolated",
        "side": "buy",
        "ord_type": "limit",
        "sz": Decimal("0.12"),
        "px": Decimal("68000.5"),
        "cl_ord_id": CL,
        "pos_side": "net",
        "attach_algo_ords": (
            AttachedTpSl(
                sl_trigger_px=Decimal("66900"),
                tp_trigger_px=Decimal("70500.0"),
                attach_algo_cl_ord_id="atlslatt01",
            ),
        ),
    }
    values.update(overrides)
    return OrderRequest(**values)


def routes(router: respx.MockRouter) -> None:
    router.get("/api/v5/public/time").mock(return_value=httpx.Response(200, json=TIME))


# ---- AC2: signed POST trade/order ----------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_place_order_sends_signed_post_with_cl_ord_id_string_sz_and_attached_stop(
    respx_mock: respx.MockRouter,
) -> None:
    routes(respx_mock)
    route = respx_mock.post("/api/v5/trade/order").mock(
        return_value=httpx.Response(200, json=fixture("order_ok"))
    )

    ack = await trade_client("demo").place_order(entry())

    assert ack.ok
    assert ack.ord_id == "312269865356374016"
    request = route.calls.last.request
    raw = request.content.decode()
    body = json.loads(raw)
    assert body["clOrdId"] == CL
    assert body["sz"] == "0.12"
    assert isinstance(body["sz"], str)
    assert body["px"] == "68000.5"
    assert body["tdMode"] == "isolated"
    assert body["attachAlgoOrds"] == [
        {
            "attachAlgoClOrdId": "atlslatt01",
            "slTriggerPx": "66900",
            "slOrdPx": "-1",
            "slTriggerPxType": "last",
            "tpTriggerPx": "70500.0",
            "tpOrdPx": "-1",
            "tpTriggerPxType": "last",
        }
    ]
    assert request.headers["x-simulated-trading"] == "1"
    ts = request.headers["OK-ACCESS-TIMESTAMP"]
    assert ts == iso_timestamp(NOW_MS)
    expected = base64.b64encode(
        hmac.new(SECRET.encode(), f"{ts}POST/api/v5/trade/order{raw}".encode(), sha256).digest()
    ).decode()
    assert request.headers["OK-ACCESS-SIGN"] == expected
    assert request.headers["OK-ACCESS-KEY"] == CREDS.api_key
    assert request.headers["OK-ACCESS-PASSPHRASE"] == CREDS.passphrase


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_live_order_has_no_simulated_trading_header(respx_mock: respx.MockRouter) -> None:
    routes(respx_mock)
    route = respx_mock.post("/api/v5/trade/order").mock(
        return_value=httpx.Response(200, json=fixture("order_ok"))
    )

    await trade_client("live").place_order(entry())

    assert "x-simulated-trading" not in route.calls.last.request.headers


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_retry_after_5xx_resends_the_same_cl_ord_id(respx_mock: respx.MockRouter) -> None:
    routes(respx_mock)
    route = respx_mock.post("/api/v5/trade/order").mock(
        side_effect=[httpx.Response(503, text="busy"), httpx.Response(200, json=fixture("order_ok"))]
    )

    await trade_client().place_order(entry())

    bodies = [json.loads(c.request.content) for c in route.calls]
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    assert bodies[0]["clOrdId"] == CL


def test_market_order_omits_px_and_floats_are_refused() -> None:
    body = entry(ord_type="market", px=None, attach_algo_ords=()).to_okx()
    assert "px" not in body
    assert "attachAlgoOrds" not in body

    with pytest.raises(TypeError, match="Decimal"):
        entry(sz=0.12).to_okx()
    with pytest.raises(ValueError, match="positive"):
        entry(sz=Decimal("0")).to_okx()
    with pytest.raises(ValueError, match="needs px"):
        entry(px=None).to_okx()
    with pytest.raises(ValueError, match="alphanumerics"):
        entry(cl_ord_id="atl-1").to_okx()
    with pytest.raises(ValueError, match="stop-loss or a take-profit"):
        entry(attach_algo_ords=(AttachedTpSl(),)).to_okx()


def test_decimal_strings_never_use_exponents() -> None:
    body = entry(sz=Decimal("1E+2"), px=Decimal("0.00000001")).to_okx()
    assert body["sz"] == "100"
    assert body["px"] == "0.00000001"


def test_new_cl_ord_id_is_32_alphanumerics_and_unique() -> None:
    a, b = new_cl_ord_id(), new_cl_ord_id()
    assert a != b
    assert len(a) == 32
    assert a.isalnum()
    assert a.startswith("atl")


# ---- AC3: sCode → typed error with a human message ----------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_scode_51008_raises_typed_error_about_insufficient_balance(
    respx_mock: respx.MockRouter,
) -> None:
    routes(respx_mock)
    respx_mock.post("/api/v5/trade/order").mock(return_value=httpx.Response(200, json=fixture("order_51008")))

    with pytest.raises(OkxTradeError) as caught:
        await trade_client().place_order(entry())

    err = caught.value
    assert isinstance(err, OkxError)
    assert err.s_code == "51008"
    assert err.cl_ord_id == CL
    assert "insufficient balance" in err.human_message.lower()
    assert err.s_msg.startswith("Order failed")


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_unknown_scode_reads_okx_error_code_and_message(respx_mock: respx.MockRouter) -> None:
    routes(respx_mock)
    respx_mock.post("/api/v5/trade/order").mock(
        return_value=httpx.Response(200, json=fixture("order_unmapped"))
    )

    with pytest.raises(OkxTradeError) as caught:
        await trade_client().place_order(entry())

    assert caught.value.human_message == (
        "OKX error 51137: Your opening price has triggered the limit price, and the max buy price is 70500.0"
    )


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_duplicate_cl_ord_id_is_flagged(respx_mock: respx.MockRouter) -> None:
    routes(respx_mock)
    respx_mock.post("/api/v5/trade/order").mock(return_value=httpx.Response(200, json=fixture("order_51016")))

    with pytest.raises(OkxTradeError) as caught:
        await trade_client().place_order(entry())

    assert caught.value.duplicate_cl_ord_id


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_top_level_code_without_rows_is_a_trade_error_too(respx_mock: respx.MockRouter) -> None:
    routes(respx_mock)
    respx_mock.post("/api/v5/trade/order").mock(
        return_value=httpx.Response(200, json=fixture("top_level_51000"))
    )

    with pytest.raises(OkxTradeError) as caught:
        await trade_client().place_order(entry())

    assert caught.value.s_code == "51000"
    assert caught.value.human_message == MESSAGES["51000"]


def test_error_map_covers_the_common_trade_codes() -> None:
    assert len(MESSAGES) >= 30
    for code in ("51008", "51020", "51121", "51016", "51400", "51603", "59102"):
        assert code in MESSAGES
    assert "lot size" in human_message("51121")
    assert "minimum" in human_message("51020")
    assert human_message("99999", "") == "OKX error 99999: no message"
    assert all(code.isdigit() and msg.endswith(".") for code, msg in MESSAGES.items())


# ---- batch, amend, cancel, close ----------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_batch_returns_every_row_and_flags_the_refused_one(respx_mock: respx.MockRouter) -> None:
    routes(respx_mock)
    route = respx_mock.post("/api/v5/trade/batch-orders").mock(
        return_value=httpx.Response(200, json=fixture("batch_partial"))
    )

    acks = await trade_client().place_batch_orders(
        [entry(cl_ord_id="atlbatch1"), entry(cl_ord_id="atlbatch2", sz=Decimal("0.123"))]
    )

    assert [a.ok for a in acks] == [True, False]
    assert ack_error(acks[0]) is None
    err = ack_error(acks[1], "trade/batch-orders")
    assert err is not None
    assert "lot size" in err.human_message
    sent = json.loads(route.calls.last.request.content)
    assert [o["clOrdId"] for o in sent] == ["atlbatch1", "atlbatch2"]


async def test_batch_size_is_bounded() -> None:
    with pytest.raises(ValueError, match=r"1\.\.20"):
        await trade_client().place_batch_orders([])


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_amend_cancel_and_close(respx_mock: respx.MockRouter) -> None:
    routes(respx_mock)
    amend = respx_mock.post("/api/v5/trade/amend-order").mock(
        return_value=httpx.Response(200, json=fixture("amend_ok"))
    )
    respx_mock.post("/api/v5/trade/cancel-order").mock(
        return_value=httpx.Response(200, json=fixture("cancel_51400"))
    )
    close = respx_mock.post("/api/v5/trade/close-position").mock(
        return_value=httpx.Response(200, json=fixture("close_position"))
    )
    client = trade_client()

    ack = await client.amend_order(
        "BTC-USDT-SWAP", ord_id="312269865356374016", new_px=Decimal("67950"), req_id="atlamend1"
    )
    assert ack.req_id == "atlamend1"
    assert json.loads(amend.calls.last.request.content) == {
        "instId": "BTC-USDT-SWAP",
        "ordId": "312269865356374016",
        "newPx": "67950",
        "reqId": "atlamend1",
    }

    with pytest.raises(OkxTradeError) as caught:
        await client.cancel_order("BTC-USDT-SWAP", ord_id="312269865356374016")
    assert caught.value.s_code == "51400"
    assert caught.value.ord_id == "312269865356374016"

    closed = await client.close_position("BTC-USDT-SWAP", "isolated", pos_side="net", auto_cxl=True)
    assert closed.inst_id == "BTC-USDT-SWAP"
    assert json.loads(close.calls.last.request.content) == {
        "instId": "BTC-USDT-SWAP",
        "mgnMode": "isolated",
        "posSide": "net",
        "autoCxl": True,
    }

    with pytest.raises(ValueError, match="exactly one"):
        await client.cancel_order("BTC-USDT-SWAP")


# ---- algo orders ---------------------------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_algo_place_cancel_and_pending(respx_mock: respx.MockRouter) -> None:
    routes(respx_mock)
    place = respx_mock.post("/api/v5/trade/order-algo").mock(
        return_value=httpx.Response(200, json=fixture("order_algo_ok"))
    )
    respx_mock.post("/api/v5/trade/cancel-algos").mock(
        return_value=httpx.Response(200, json=fixture("cancel_algos"))
    )
    pending = respx_mock.get("/api/v5/trade/orders-algo-pending").mock(
        return_value=httpx.Response(200, json=fixture("algo_pending"))
    )
    client = trade_client()

    ack = await client.place_algo_order(
        AlgoOrderRequest(
            inst_id="BTC-USDT-SWAP",
            td_mode="isolated",
            side="sell",
            ord_type="conditional",
            algo_cl_ord_id="atlsl0001",
            sz=Decimal("0.12"),
            pos_side="net",
            sl_trigger_px=Decimal("66900"),
        )
    )
    assert ack.algo_id == "681096944655273984"
    assert json.loads(place.calls.last.request.content) == {
        "instId": "BTC-USDT-SWAP",
        "tdMode": "isolated",
        "side": "sell",
        "posSide": "net",
        "ordType": "conditional",
        "sz": "0.12",
        "reduceOnly": True,
        "algoClOrdId": "atlsl0001",
        "slTriggerPx": "66900",
        "slOrdPx": "-1",
        "slTriggerPxType": "last",
    }

    rows = await client.cancel_algos(
        [("BTC-USDT-SWAP", "681096944655273984"), ("BTC-USDT-SWAP", "681096944655273985")]
    )
    assert [r.ok for r in rows] == [True, False]

    algos = await client.get_algo_orders_pending("conditional,oco", inst_id="BTC-USDT-SWAP")
    assert algos[0].sl_trigger_px == Decimal("66900")
    assert algos[0].state == "live"
    assert pending.calls.last.request.url.params["ordType"] == "conditional,oco"


def test_algo_request_validation() -> None:
    base: dict[str, Any] = {
        "inst_id": "BTC-USDT-SWAP",
        "td_mode": "isolated",
        "side": "sell",
        "algo_cl_ord_id": "atloco01",
    }
    with pytest.raises(ValueError, match="both"):
        AlgoOrderRequest(ord_type="oco", sz=Decimal(1), sl_trigger_px=Decimal(1), **base).to_okx()
    with pytest.raises(ValueError, match="exactly one"):
        AlgoOrderRequest(ord_type="conditional", sl_trigger_px=Decimal(1), **base).to_okx()
    with pytest.raises(ValueError, match="trigger_px"):
        AlgoOrderRequest(ord_type="trigger", sz=Decimal(1), **base).to_okx()
    body = AlgoOrderRequest(
        ord_type="oco",
        close_fraction=Decimal("1"),
        sl_trigger_px=Decimal("66900"),
        tp_trigger_px=Decimal("70500"),
        tp_ord_px=Decimal("70490"),
        **base,
    ).to_okx()
    assert body["closeFraction"] == "1"
    assert "sz" not in body
    assert body["tpOrdPx"] == "70490"
    assert body["slOrdPx"] == "-1"


# ---- reads and account ---------------------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_reads_parse_to_decimal(respx_mock: respx.MockRouter) -> None:
    routes(respx_mock)
    order = respx_mock.get("/api/v5/trade/order").mock(
        return_value=httpx.Response(200, json=fixture("order_detail"))
    )
    respx_mock.get("/api/v5/trade/orders-pending").mock(
        return_value=httpx.Response(200, json=fixture("orders_pending"))
    )
    history = respx_mock.get("/api/v5/trade/orders-history").mock(
        return_value=httpx.Response(200, json=fixture("orders_pending"))
    )
    respx_mock.get("/api/v5/trade/fills").mock(return_value=httpx.Response(200, json=fixture("fills")))
    fills_history = respx_mock.get("/api/v5/trade/fills-history").mock(
        return_value=httpx.Response(200, json=fixture("fills"))
    )
    max_size = respx_mock.get("/api/v5/account/max-size").mock(
        return_value=httpx.Response(200, json=fixture("max_size"))
    )
    client = trade_client()

    detail = await client.get_order("BTC-USDT-SWAP", cl_ord_id=CL)
    assert order.calls.last.request.url.params["clOrdId"] == CL
    assert order.calls.last.request.headers["OK-ACCESS-SIGN"]
    assert detail.state == "partially_filled"
    assert detail.acc_fill_sz == Decimal("0.05")
    assert detail.attach_algo_ords[0].sl_trigger_px == Decimal("66900")

    pending = await client.get_orders_pending(inst_type="SWAP", limit=100)
    assert pending[0].px == Decimal("4010.25")
    await client.get_orders_history("SWAP", limit=50)
    assert history.calls.last.request.url.params["instType"] == "SWAP"

    fills = await client.get_fills(inst_type="SWAP")
    assert fills[0].fee == Decimal("-0.0017")
    assert fills[0].exec_type == "M"
    await client.get_fills_history("SWAP", inst_id="BTC-USDT-SWAP")
    assert fills_history.calls.last.request.url.params["instId"] == "BTC-USDT-SWAP"

    size = await client.get_max_size("BTC-USDT-SWAP", "isolated", leverage=3)
    assert size.max_buy == Decimal("1250.5")
    assert max_size.calls.last.request.url.params["leverage"] == "3"

    with pytest.raises(ValueError, match=r"1\.\.100"):
        await client.get_fills(limit=101)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_set_leverage_and_query_errors(respx_mock: respx.MockRouter) -> None:
    routes(respx_mock)
    lever = respx_mock.post("/api/v5/account/set-leverage").mock(
        return_value=httpx.Response(200, json=fixture("set_leverage"))
    )
    respx_mock.get("/api/v5/trade/order").mock(
        return_value=httpx.Response(200, json={"code": "51603", "msg": "Order does not exist", "data": []})
    )
    client = trade_client()

    result = await client.set_leverage("BTC-USDT-SWAP", 3, "isolated")
    assert result.lever == Decimal(3)
    assert json.loads(lever.calls.last.request.content) == {
        "instId": "BTC-USDT-SWAP",
        "lever": "3",
        "mgnMode": "isolated",
    }
    with pytest.raises(ValueError, match="positive int"):
        await client.set_leverage("BTC-USDT-SWAP", 0, "isolated")

    with pytest.raises(OkxTradeError) as caught:
        await client.get_order("BTC-USDT-SWAP", ord_id="1")
    assert caught.value.human_message == MESSAGES["51603"]


def _contains_float(annotation: Any) -> bool:
    if annotation is float:
        return True
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _contains_float(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType, list, tuple, dict):
        return any(_contains_float(a) for a in typing.get_args(annotation))
    return False


def test_no_trade_model_field_is_a_float() -> None:
    for model in TRADE_MODELS:
        for name, field in model.model_fields.items():
            assert not _contains_float(field.annotation), f"{model.__name__}.{name} is a float"


def test_fixture_mode_still_refuses_writes() -> None:
    from trading_core.okx.fixtures import fixture_transport

    transport = fixture_transport(FIXTURES)
    response = transport.handle_request(httpx.Request("POST", f"{BASE}/api/v5/trade/order", json={}))
    assert response.status_code == 405
