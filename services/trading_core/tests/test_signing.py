import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.logs import JsonFormatter
from trading_core.security import NonceStore, new_nonce, sign

VECTOR: dict[str, str] = json.loads(
    (Path(__file__).resolve().parents[3] / "test-vectors" / "internal_hmac.json").read_text(encoding="utf-8")
)
SECRET = "pytest-only-secret"  # noqa: S105 - test-only

echo_router = APIRouter(prefix="/internal")


@echo_router.post("/echo")
async def echo(request: Request) -> dict[str, Any]:
    return {"reached": True, "body": (await request.body()).decode()}


@pytest.fixture
def client() -> TestClient:
    settings = Settings(host="127.0.0.1", port=8100, env="demo", internal_shared_secret=SECRET)
    return TestClient(create_app(settings, extra_internal=echo_router))


def _headers(
    method: str,
    path: str,
    body: bytes,
    ts: int | None = None,
    secret: str = SECRET,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = str(int(time.time()) if ts is None else ts)
    n = new_nonce() if nonce is None else nonce
    return {
        "X-Atlas-Timestamp": timestamp,
        "X-Atlas-Nonce": n,
        "X-Atlas-Signature": sign(secret, timestamp, n, method, path, body),
        "Content-Type": "application/json",
    }


def test_shared_vector_matches_the_php_signature() -> None:
    got = sign(
        VECTOR["secret"],
        VECTOR["timestamp"],
        VECTOR["nonce"],
        VECTOR["method"],
        VECTOR["path"],
        VECTOR["body"].encode("utf-8"),
    )
    assert got == VECTOR["signature"]


def test_health_is_unsigned_and_reports_demo(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["env"] == "demo"
    assert response.json()["version"]


def test_signed_request_reaches_the_handler_with_its_body(client: TestClient) -> None:
    body = b'{"hello":"world"}'
    response = client.post("/internal/echo", content=body, headers=_headers("POST", "/internal/echo", body))
    assert response.status_code == 200
    assert response.json() == {"reached": True, "body": body.decode()}


def test_signed_get_with_query_reaches_the_handler(client: TestClient) -> None:
    response = client.get("/internal/ping?x=1", headers=_headers("GET", "/internal/ping?x=1", b""))
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_missing_signature_is_rejected(client: TestClient) -> None:
    assert client.post("/internal/echo", content=b"{}").status_code == 401
    assert client.get("/internal/ping").status_code == 401


def test_wrong_signature_is_rejected(client: TestClient) -> None:
    body = b"{}"
    headers = _headers("POST", "/internal/echo", body, secret="not-the-secret")  # noqa: S106
    assert client.post("/internal/echo", content=body, headers=headers).status_code == 401


def test_tampered_body_is_rejected(client: TestClient) -> None:
    headers = _headers("POST", "/internal/echo", b'{"a":1}')
    assert client.post("/internal/echo", content=b'{"a":2}', headers=headers).status_code == 401


def test_tampered_query_is_rejected(client: TestClient) -> None:
    headers = _headers("GET", "/internal/ping?env=demo", b"")
    assert client.get("/internal/ping?env=live", headers=headers).status_code == 401


def test_timestamp_31_seconds_old_is_rejected(client: TestClient) -> None:
    body = b"{}"
    headers = _headers("POST", "/internal/echo", body, ts=int(time.time()) - 31)
    assert client.post("/internal/echo", content=body, headers=headers).status_code == 401


def test_timestamp_31_seconds_ahead_is_rejected(client: TestClient) -> None:
    body = b"{}"
    headers = _headers("POST", "/internal/echo", body, ts=int(time.time()) + 31)
    assert client.post("/internal/echo", content=body, headers=headers).status_code == 401


def test_non_numeric_timestamp_is_rejected(client: TestClient) -> None:
    headers = _headers("GET", "/internal/ping", b"")
    headers["X-Atlas-Timestamp"] = "2026-10-28T08:00:00Z"
    assert client.get("/internal/ping", headers=headers).status_code == 401


def test_openapi_schema_is_not_served(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/health").status_code == 200


def test_missing_nonce_is_rejected(client: TestClient) -> None:
    body = b"{}"
    headers = _headers("POST", "/internal/echo", body)
    del headers["X-Atlas-Nonce"]
    assert client.post("/internal/echo", content=body, headers=headers).status_code == 401


def test_nonce_left_out_of_the_mac_is_rejected(client: TestClient) -> None:
    """The pre-ATL-34 recipe (ts + METHOD + path + body) with a nonce header alongside is refused."""
    body = b"{}"
    timestamp = str(int(time.time()))
    headers = {
        "X-Atlas-Timestamp": timestamp,
        "X-Atlas-Nonce": new_nonce(),
        "X-Atlas-Signature": sign(SECRET, timestamp, "", "POST", "/internal/echo", body),
    }
    assert client.post("/internal/echo", content=body, headers=headers).status_code == 401


def test_nonce_swapped_after_signing_is_rejected(client: TestClient) -> None:
    body = b"{}"
    headers = _headers("POST", "/internal/echo", body)
    headers["X-Atlas-Nonce"] = new_nonce()
    assert client.post("/internal/echo", content=body, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "nonce",
    ["abc", "g" * 32, "A" * 32, "0" * 31, "0" * 33, "0" * 16 + "-" + "0" * 15],
)
def test_malformed_nonce_is_rejected(client: TestClient, nonce: str) -> None:
    body = b"{}"
    headers = _headers("POST", "/internal/echo", body, nonce=nonce)
    assert client.post("/internal/echo", content=body, headers=headers).status_code == 401


def test_replayed_request_is_rejected(client: TestClient) -> None:
    body = b'{"a":1}'
    headers = _headers("POST", "/internal/echo", body)
    assert client.post("/internal/echo", content=body, headers=headers).status_code == 200
    assert client.post("/internal/echo", content=body, headers=headers).status_code == 401


def test_rejected_request_does_not_burn_its_nonce(client: TestClient) -> None:
    body = b"{}"
    nonce = new_nonce()
    bad = _headers("POST", "/internal/echo", body, secret="not-the-secret", nonce=nonce)  # noqa: S106
    assert client.post("/internal/echo", content=body, headers=bad).status_code == 401
    good = _headers("POST", "/internal/echo", body, nonce=nonce)
    assert client.post("/internal/echo", content=body, headers=good).status_code == 200


def test_nonce_store_forgets_after_its_ttl() -> None:
    now = [1000.0]
    store = NonceStore(ttl_seconds=60, clock=lambda: now[0])
    assert store.claim("a" * 32) is True
    now[0] += 59
    assert store.claim("a" * 32) is False
    now[0] += 1.5
    assert store.claim("b" * 32) is True
    assert len(store) == 1  # the expired nonce was pruned
    assert store.claim("a" * 32) is True


def test_unknown_internal_route_is_still_401_without_signature(client: TestClient) -> None:
    assert client.get("/internal/does-not-exist").status_code == 401


def test_empty_secret_fails_closed() -> None:
    settings = Settings(host="127.0.0.1", port=8100, env="demo", internal_shared_secret="")
    closed = TestClient(create_app(settings))
    headers = _headers("GET", "/internal/ping", b"", secret="")
    assert closed.get("/internal/ping", headers=headers).status_code == 401


def test_access_log_never_contains_headers_body_or_internal_query(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    body = b'{"api_secret":"do-not-log-me"}'
    path = "/internal/echo?token=do-not-log-either"
    headers = _headers("POST", path, body)
    with caplog.at_level(logging.INFO, logger="trading_core.access"):
        assert client.post(path, content=body, headers=headers).status_code == 200
        assert client.post(path, content=body).status_code == 401

    lines = [JsonFormatter().format(r) for r in caplog.records if r.name == "trading_core.access"]
    assert len(lines) == 2
    joined = "\n".join(lines)
    for secret_bit in ("do-not-log-me", "do-not-log-either", headers["X-Atlas-Signature"], "X-Atlas"):
        assert secret_bit not in joined
    first = json.loads(lines[0])
    assert first["path"] == "/internal/echo"
    assert first["status"] == 200
    assert json.loads(lines[1])["status"] == 401


def test_settings_default_to_localhost_8100_and_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TRADING_CORE_HOST", "TRADING_CORE_PORT", "ATLAS_ENV", "ATLAS_DEFAULT_ENV"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert (settings.host, settings.port, settings.env) == ("127.0.0.1", 8100, "demo")


def test_settings_reject_an_unknown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ENV", "prod")
    with pytest.raises(ValueError, match="ATLAS_ENV"):
        Settings.from_env()
