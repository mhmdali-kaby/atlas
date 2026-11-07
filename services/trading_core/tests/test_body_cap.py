"""ATL-35: InternalSignatureMiddleware must not buffer an unbounded /internal body.

Header-only checks (timestamp, nonce, signature presence) run before a single byte of the body
is read, so an unsigned caller gets 401 without the body ever being consumed. A body whose
Content-Length already exceeds the cap is rejected with 413 before it is read. A body with no
(or an understated) Content-Length — a chunked upload — is still capped by a running total kept
while streaming it in.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.testclient import TestClient
from trading_core.app import create_app
from trading_core.config import Settings
from trading_core.security import MAX_INTERNAL_BODY_BYTES, InternalSignatureMiddleware, new_nonce, sign

SECRET = "pytest-only-secret"  # noqa: S105 - test-only


async def _ok_app(scope: dict[str, Any], receive: Callable[[], Awaitable[dict[str, Any]]], send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _scope(headers: dict[str, str], method: str = "POST", path: str = "/internal/echo") -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
    }


def _signed_headers(method: str, path: str, body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    nonce = new_nonce()
    return {
        "x-atlas-timestamp": ts,
        "x-atlas-nonce": nonce,
        "x-atlas-signature": sign(SECRET, ts, nonce, method, path, body),
    }


async def test_unsigned_2mib_post_is_rejected_without_reading_the_body() -> None:
    calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("the body must never be read for a request with no signature headers")

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = InternalSignatureMiddleware(_ok_app, secret=SECRET)
    scope = _scope({"content-length": str(2 * 1024 * 1024)})

    await middleware(scope, receive, send)

    assert calls == 0
    assert sent[0]["status"] == 401


async def test_signed_body_over_the_cap_is_rejected_with_413_without_reading_it() -> None:
    calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("a body already over the declared Content-Length cap must never be read")

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    path = "/internal/echo"
    over_cap = MAX_INTERNAL_BODY_BYTES + 1
    headers = _signed_headers("POST", path, b"x" * over_cap)
    headers["content-length"] = str(over_cap)

    middleware = InternalSignatureMiddleware(_ok_app, secret=SECRET)
    scope = _scope(headers, path=path)

    await middleware(scope, receive, send)

    assert calls == 0
    assert sent[0]["status"] == 413


async def test_chunked_body_without_content_length_is_capped_mid_stream() -> None:
    path = "/internal/echo"
    # The MAC is never checked here (413 fires first), so the signature just needs the right shape.
    headers = _signed_headers("POST", path, b"")
    headers["transfer-encoding"] = "chunked"
    headers.pop("content-length", None)

    chunk = b"x" * 100_000
    chunk_count = (MAX_INTERNAL_BODY_BYTES // len(chunk)) + 5  # comfortably over the cap, in many pieces
    delivered = 0

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        delivered += 1
        more = delivered < chunk_count
        return {"type": "http.request", "body": chunk, "more_body": more}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = InternalSignatureMiddleware(_ok_app, secret=SECRET)
    scope = _scope(headers, path=path)

    await middleware(scope, receive, send)

    assert sent[0]["status"] == 413
    # Caught partway through the stream, not only once every chunk had already arrived.
    assert delivered < chunk_count


def test_signed_body_over_the_cap_returns_413_through_the_full_app() -> None:
    settings = Settings(host="127.0.0.1", port=8100, env="demo", internal_shared_secret=SECRET)
    client = TestClient(create_app(settings))
    path = "/internal/does-not-exist"
    body = b"x" * (MAX_INTERNAL_BODY_BYTES + 1)
    headers = _signed_headers("POST", path, body)
    response = client.post(path, content=body, headers={k: v for k, v in headers.items()})
    assert response.status_code == 413


def test_body_at_exactly_the_cap_is_not_rejected_for_size() -> None:
    """The boundary: a body of exactly MAX_INTERNAL_BODY_BYTES is allowed through to the MAC check."""
    settings = Settings(host="127.0.0.1", port=8100, env="demo", internal_shared_secret=SECRET)
    client = TestClient(create_app(settings))
    path = "/internal/does-not-exist"
    body = b"x" * MAX_INTERNAL_BODY_BYTES
    headers = _signed_headers("POST", path, body)
    response = client.post(path, content=body, headers={k: v for k, v in headers.items()})
    # Not 413: the signature checks out (the route genuinely doesn't exist, so 404).
    assert response.status_code == 404
