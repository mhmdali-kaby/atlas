"""ATL-8: Test connection reads account/config, records the outcome and refuses withdraw keys.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. OKX is mocked with
respx; the connection row holds Laravel-format ciphertext made with a throwaway test-only APP_KEY.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import respx
from atlas_shared import testing
from atlas_shared.db import Connection, make_engine
from atlas_shared.laravel_crypt import parse_app_key
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, insert, select
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.connection_test import account_mode_name, run_connection_test, uid_last4
from trading_core.scheduler import Scheduler

FIXTURES = Path(__file__).parent / "fixtures" / "okx"
BASE = "https://www.okx.com"
SECRET = "atlas-test-only-internal-secret"  # noqa: S105
APP_KEY = "base64:" + base64.b64encode(b"atlas-test-only-app-key-32-bytes").decode()
FULL_UID = "4471029384756123"  # 16 digits, made up
LAST4 = "6123"

TEST_DB = testing.db_settings()


def laravel_encrypt(plaintext: str, app_key: str = APP_KEY) -> str:
    """Crypt::encryptString's payload: base64(json{iv, value, mac, tag}), AES-256-CBC, no serialize."""
    key = parse_app_key(app_key)
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    value = base64.b64encode(enc.update(data) + enc.finalize()).decode()
    iv_b64 = base64.b64encode(iv).decode()
    mac = hmac.new(key, (iv_b64 + value).encode(), hashlib.sha256).hexdigest()
    payload = {"iv": iv_b64, "value": value, "mac": mac, "tag": ""}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def config_body(**changes: Any) -> dict[str, Any]:
    body = json.loads((FIXTURES / "account_config.json").read_text(encoding="utf-8"))
    row = copy.deepcopy(body["data"][0])
    row.update(changes)
    return {"code": "0", "msg": "", "data": [row]}


def mock_okx(router: respx.MockRouter, config: dict[str, Any]) -> respx.Route:
    router.get("/api/v5/public/time").respond(
        json={"code": "0", "msg": "", "data": [{"ts": str(int(time.time() * 1000))}]}
    )
    return router.get("/api/v5/account/config").respond(json=config)


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(
            f"{TEST_DB['DB_DATABASE']} is not reachable on {TEST_DB['DB_HOST']}:{TEST_DB['DB_PORT']}: {exc}"
        )
    return eng


@pytest.fixture
def connection_id(engine: Engine) -> Iterator[uuid.UUID]:
    """A demo connection row with encrypted secrets and a stale verified_at to prove it is cleared."""
    cid = uuid.uuid4()
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(delete(Connection).where(Connection.exchange == "okx", Connection.env == "demo"))
        conn.execute(
            insert(Connection).values(
                id=cid,
                exchange="okx",
                env="demo",
                label="OKX Demo",
                api_key=laravel_encrypt("test-api-key-0000"),
                api_secret=laravel_encrypt("test-secret"),
                passphrase=laravel_encrypt("test-passphrase"),
                last_verified_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    yield cid
    with engine.begin() as conn:
        conn.execute(delete(Connection).where(Connection.id == cid))


def load(engine: Engine, cid: uuid.UUID) -> Connection:
    with engine.connect() as conn:
        row: Connection = conn.execute(select(Connection).where(Connection.id == cid)).one()  # type: ignore[assignment]
    return row


def signed(method: str, path: str, body: bytes = b"") -> dict[str, str]:
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), (ts + method + path).encode() + body, hashlib.sha256).hexdigest()
    return {"X-Atlas-Timestamp": ts, "X-Atlas-Signature": sig}


def client(engine: Engine, app_key: str = APP_KEY) -> TestClient:
    settings = Settings(
        host="127.0.0.1",
        port=8100,
        env="demo",
        internal_shared_secret=SECRET,
        run_scheduler=False,
        app_key=app_key,
    )
    return TestClient(create_app(settings, scheduler=Scheduler(), engine=engine))


def post_test(engine: Engine, cid: uuid.UUID, app_key: str = APP_KEY) -> Any:
    path = f"/internal/connections/{cid}/test"
    return client(engine, app_key).post(path, headers=signed("POST", path))


def row_text(row: Connection) -> str:
    return json.dumps({k: str(v) for k, v in row._asdict().items()})  # type: ignore[attr-defined]


# ---- pure helpers -------------------------------------------------------------------------------


def test_account_mode_names() -> None:
    assert [account_mode_name(v) for v in ("1", "2", "3", "4", "9", "")] == [
        "spot",
        "futures",
        "multi_currency_margin",
        "portfolio_margin",
        "unknown",
        "unknown",
    ]
    assert uid_last4(FULL_UID) == LAST4 and uid_last4("") is None


# ---- through the signed endpoint ----------------------------------------------------------------


@respx.mock(base_url=BASE, assert_all_called=False)
def test_read_trade_key_is_verified_and_only_uid_last4_is_kept(
    respx_mock: respx.MockRouter, engine: Engine, connection_id: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    respx_mock.route(host="testserver").pass_through()
    route = mock_okx(
        respx_mock, config_body(acctLv="2", posMode="net_mode", perm="read_only,trade", uid=FULL_UID)
    )
    caplog.set_level(logging.DEBUG)

    response = post_test(engine, connection_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["error"] is None
    assert body["acct_lv"] == "2" and body["account_mode"] == "futures"
    assert body["position_mode"] == "net_mode"
    assert body["permissions"] == ["read_only", "trade"]
    assert body["uid_last4"] == LAST4
    assert body["verified_at"] is not None
    # The call was signed with the decrypted key, in demo.
    sent = route.calls.last.request.headers
    assert sent["OK-ACCESS-KEY"] == "test-api-key-0000" and sent["x-simulated-trading"] == "1"

    row = load(engine, connection_id)
    assert row.account_mode == "futures" and row.position_mode == "net_mode"
    assert row.permissions == ["read_only", "trade"] and row.uid_last4 == LAST4
    assert row.last_verified_at is not None and row.last_test_error is None

    assert FULL_UID not in response.text
    assert FULL_UID not in row_text(row)
    assert FULL_UID not in caplog.text
    assert "test-secret" not in caplog.text and "test-passphrase" not in caplog.text


@respx.mock(base_url=BASE, assert_all_called=False)
def test_withdraw_permission_is_refused_and_not_verified(
    respx_mock: respx.MockRouter, engine: Engine, connection_id: uuid.UUID
) -> None:
    respx_mock.route(host="testserver").pass_through()
    mock_okx(respx_mock, config_body(perm="read_only,withdraw,trade", uid=FULL_UID))

    body = post_test(engine, connection_id).json()

    assert body["ok"] is False and body["error"] == "withdraw_permission_enabled"
    assert body["permissions"] == ["read_only", "trade", "withdraw"]
    assert body["verified_at"] is None
    row = load(engine, connection_id)
    assert row.last_verified_at is None  # also clears the earlier verification
    assert row.last_test_error == "withdraw_permission_enabled"
    assert row.permissions == ["read_only", "trade", "withdraw"]
    assert row.uid_last4 == LAST4


@respx.mock(base_url=BASE, assert_all_called=False)
def test_wrong_sign_reports_invalid_credentials_and_no_uid(
    respx_mock: respx.MockRouter, engine: Engine, connection_id: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    respx_mock.route(host="testserver").pass_through()
    error = json.loads((FIXTURES / "error_invalid_sign.json").read_text(encoding="utf-8"))
    respx_mock.get("/api/v5/public/time").respond(
        json={"code": "0", "msg": "", "data": [{"ts": str(int(time.time() * 1000))}]}
    )
    respx_mock.get("/api/v5/account/config").respond(401, json=error)
    caplog.set_level(logging.DEBUG)

    response = post_test(engine, connection_id)

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False and body["error"] == "invalid_credentials" and body["okx_code"] == "50113"
    assert body["uid_last4"] is None
    row = load(engine, connection_id)
    assert row.last_verified_at is None and row.last_test_error == "invalid_credentials"
    assert row.uid_last4 is None
    assert FULL_UID not in row_text(row) and FULL_UID not in caplog.text


@pytest.mark.parametrize(("code", "slug"), [("50111", "invalid_credentials"), ("50101", "wrong_environment")])
@respx.mock(base_url=BASE, assert_all_called=False)
def test_okx_codes_map_to_slugs(
    respx_mock: respx.MockRouter, engine: Engine, connection_id: uuid.UUID, code: str, slug: str
) -> None:
    respx_mock.route(host="testserver").pass_through()
    mock_okx(respx_mock, {"code": code, "msg": "x", "data": []})
    body = post_test(engine, connection_id).json()
    assert body["ok"] is False and body["error"] == slug and body["okx_code"] == code


@respx.mock(base_url=BASE, assert_all_called=False)
def test_spot_mode_recorded_fixture_still_passes_with_raw_acct_lv(
    respx_mock: respx.MockRouter, engine: Engine, connection_id: uuid.UUID
) -> None:
    respx_mock.route(host="testserver").pass_through()
    # The recorded demo fixture, switched to Spot mode and a Read + Trade key.
    mock_okx(respx_mock, config_body(acctLv="1", perm="read_only,trade"))

    body = post_test(engine, connection_id).json()

    assert body["ok"] is True
    assert body["acct_lv"] == "1" and body["account_mode"] == "spot"
    assert load(engine, connection_id).account_mode == "spot"


def test_wrong_app_key_reports_decrypt_failed(engine: Engine, connection_id: uuid.UUID) -> None:
    other = "base64:" + base64.b64encode(b"another-test-only-app-key-32byte").decode()
    body = post_test(engine, connection_id, app_key=other).json()
    assert body == {**body, "ok": False, "error": "decrypt_failed"}
    assert load(engine, connection_id).last_verified_at is None


def test_unknown_connection_is_404_and_unsigned_is_401(engine: Engine) -> None:
    path = f"/internal/connections/{uuid.uuid4()}/test"
    c = client(engine)
    assert c.post(path).status_code == 401
    response = c.post(path, headers=signed("POST", path))
    assert response.status_code == 404 and response.json()["error"] == "connection_not_found"


async def test_run_connection_test_raises_for_missing_row(engine: Engine) -> None:
    from trading_core.connection_test import ConnectionNotFound

    with pytest.raises(ConnectionNotFound):
        await run_connection_test(uuid.uuid4(), engine=engine, app_key=APP_KEY)
