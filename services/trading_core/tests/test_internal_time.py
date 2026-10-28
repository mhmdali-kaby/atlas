"""ATL-13: signed GET /internal/time reports OKX public/time and this machine's clock drift."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import httpx
import respx
from fastapi.testclient import TestClient
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.okx.client import OkxClient
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers

BASE = "https://www.okx.com"
SECRET = "atlas-test-only-internal-secret"  # noqa: S105
PATH = "/internal/time"


def signed(method: str, path: str, body: bytes = b"") -> dict[str, str]:
    return signed_headers(SECRET, method, path, body)


def client() -> TestClient:
    settings = Settings(
        host="127.0.0.1", port=8100, env="demo", internal_shared_secret=SECRET, run_scheduler=False
    )
    return TestClient(create_app(settings, scheduler=Scheduler(), engine=MagicMock()))


@respx.mock(base_url=BASE, assert_all_called=False)
def test_reports_drift_against_okx_server_time(respx_mock: respx.MockRouter) -> None:
    respx_mock.route(host="testserver").pass_through()
    ahead_ms = 1500
    route = respx_mock.get("/api/v5/public/time").mock(
        side_effect=lambda _: httpx.Response(
            200, json={"code": "0", "msg": "", "data": [{"ts": str(int(time.time() * 1000) + ahead_ms)}]}
        )
    )

    response = client().get(PATH, headers=signed("GET", PATH))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["env"] == "demo"
    assert abs(body["offset_ms"] - ahead_ms) < 500
    assert body["drift_ms"] == abs(body["offset_ms"])
    assert body["server_time"].endswith("Z") and body["local_time"].endswith("Z")
    assert route.calls.last.request.headers["x-simulated-trading"] == "1"


@respx.mock(base_url=BASE, assert_all_called=False)
def test_okx_unreachable_is_a_502(respx_mock: respx.MockRouter) -> None:
    respx_mock.route(host="testserver").pass_through()
    respx_mock.get("/api/v5/public/time").mock(side_effect=httpx.ConnectError("down"))
    app = create_app(
        Settings(host="127.0.0.1", port=8100, env="demo", internal_shared_secret=SECRET, run_scheduler=False),
        scheduler=Scheduler(),
        engine=MagicMock(),
    )

    async def no_sleep(_: float) -> None:
        return None

    app.state.okx_public_client_factory = lambda env: OkxClient(env, sleep=no_sleep)

    response = TestClient(app).get(PATH, headers=signed("GET", PATH))

    assert response.status_code == 502
    assert response.json() == {"ok": False, "error": "okx_unreachable"}


def test_requires_a_signature() -> None:
    assert client().get(PATH).status_code == 401
