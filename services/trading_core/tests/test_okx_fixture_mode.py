"""ATL-25: ATLAS_OKX_FIXTURES=1 makes every OkxClient answer from the recorded fixtures, never from OKX."""

from __future__ import annotations

import time

import httpx
import pytest
import respx
from trading_core.okx import OkxClient, OkxCredentials
from trading_core.okx.fixtures import fixture_body, fixtures_dir

CREDS = OkxCredentials(api_key="test-key-0000", secret="atlas-test-only-okx-secret", passphrase="test-pass")  # noqa: S106


@pytest.mark.asyncio
async def test_fixture_mode_serves_recorded_json_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_OKX_FIXTURES", "1")
    with respx.mock(assert_all_called=False) as router:
        real = router.route(host="www.okx.com").mock(return_value=httpx.Response(500))
        async with OkxClient("demo", CREDS) as okx:
            config = await okx.get_account_config()
            balance = await okx.get_balance()
            instruments = await okx.get_instruments("SWAP")
            spot = await okx.get_instruments("SPOT")
            bills_next_page = await okx.get_bills(after="1")
            server = await okx.get_server_time()
        assert not real.called
    assert config.acct_lv == "2"
    assert balance.total_eq > 0
    assert len(instruments) == 6
    assert spot == []  # no recorded SPOT list: empty, not an error
    assert bills_next_page == []  # a paging cursor gets an empty page, so paging loops end
    assert abs(server.ts.timestamp() - time.time()) < 60  # live clock, offset stays ~0


@pytest.mark.asyncio
async def test_without_the_switch_the_client_calls_okx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_OKX_FIXTURES", raising=False)
    with respx.mock() as router:
        route = router.get("https://www.okx.com/api/v5/public/time").mock(
            return_value=httpx.Response(200, json={"code": "0", "data": [{"ts": "1793207736680"}], "msg": ""})
        )
        async with OkxClient("demo") as okx:
            await okx.get_server_time()
        assert route.called


def test_fixture_body_prefers_the_inst_type_file() -> None:
    body = fixture_body("/api/v5/account/trade-fee", httpx.QueryParams({"instType": "SWAP"}), fixtures_dir())
    assert body["code"] == "0"
    assert body["data"]
