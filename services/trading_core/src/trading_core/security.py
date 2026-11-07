"""HMAC signing for Laravel -> trading_core calls (decision #235).

Signature = hex HMAC-SHA256(INTERNAL_SHARED_SECRET, timestamp + nonce + METHOD + path + raw body),
where `timestamp` is unix seconds as sent in X-Atlas-Timestamp, `nonce` is the 32 lower-case hex
chars sent in X-Atlas-Nonce, METHOD is upper case, and `path` is the request path plus `?query` when
there is a query string. A nonce is accepted once: a repeat within NONCE_TTL_SECONDS is a replay and
is rejected (ATL-34). The PHP twin is App\\Support\\TradingCore.
"""

from __future__ import annotations

import hmac
import re
import threading
import time
from collections.abc import Callable

from atlas_shared.defaults import DEFAULTS
from atlas_shared.internal_api import new_nonce, sign, signed_headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TIMESTAMP_HEADER = "x-atlas-timestamp"
SIGNATURE_HEADER = "x-atlas-signature"
NONCE_HEADER = "x-atlas-nonce"
CONTENT_LENGTH_HEADER = "content-length"
PROTECTED_PREFIX = "/internal"
# Longer than the accepted clock window (±30 s → a 60 s span), so a nonce cannot be forgotten while
# its timestamp would still pass the skew check.
NONCE_TTL_SECONDS = 60
# ATL-35: no /internal body may exceed this, whether declared by Content-Length or discovered
# while streaming a chunked body. config/defaults.yaml is the one place this number lives.
MAX_INTERNAL_BODY_BYTES: int = DEFAULTS["internal_api"]["max_body_bytes"]
_NONCE_RE = re.compile(r"[0-9a-f]{32}")

__all__ = ["InternalSignatureMiddleware", "NonceStore", "is_protected", "new_nonce", "sign", "signed_headers"]


class NonceStore:
    """In-process set of nonces seen in the last `ttl_seconds` (trading_core is one process)."""

    def __init__(
        self, ttl_seconds: float = NONCE_TTL_SECONDS, clock: Callable[[], float] = time.time
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, nonce: str) -> bool:
        """Record `nonce`; False if it was already seen and has not expired."""
        now = self.clock()
        with self._lock:
            expired = [n for n, until in self._seen.items() if until <= now]
            for n in expired:
                del self._seen[n]
            if nonce in self._seen:
                return False
            self._seen[nonce] = now + self.ttl_seconds
            return True

    def __len__(self) -> int:
        return len(self._seen)


def is_protected(path: str) -> bool:
    return path == PROTECTED_PREFIX or path.startswith(PROTECTED_PREFIX + "/")


class InternalSignatureMiddleware:
    """Rejects with 401 every /internal request whose signature, timestamp or nonce does not check
    out, and every replay of a nonce already accepted.

    Pure ASGI so the raw body can be read for the MAC and then replayed to the handler unchanged.
    Fails closed: with no secret configured, every /internal call is rejected.
    """

    def __init__(
        self,
        app: ASGIApp,
        secret: str,
        max_skew_seconds: int = 30,
        clock: Callable[[], float] = time.time,
        nonces: NonceStore | None = None,
    ) -> None:
        self.app = app
        self.secret = secret
        self.max_skew_seconds = max_skew_seconds
        self.clock = clock
        self.nonces = nonces if nonces is not None else NonceStore(clock=clock)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_protected(scope["path"]):
            await self.app(scope, receive, send)
            return

        headers = _header_map(scope)

        # Header-only checks first (ATL-35): an unsigned or malformed request is rejected before
        # a single byte of its body is read, so it cannot be used to make trading_core buffer an
        # unbounded body in memory.
        if not self._verify_headers(headers):
            await _unauthorised(send)
            return

        declared_length = _content_length(headers)
        if declared_length is not None and declared_length > MAX_INTERNAL_BODY_BYTES:
            await _payload_too_large(send)
            return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > MAX_INTERNAL_BODY_BYTES:
                # Covers a chunked body with no (or an understated) Content-Length: the cap is
                # enforced on what is actually read, not on what the client claims to send.
                await _payload_too_large(send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        if not self._verify_body(headers, scope, body):
            await _unauthorised(send)
            return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    def _verify_headers(self, headers: dict[str, str]) -> bool:
        """Checks that need only the request headers: present, well-formed, in-skew."""
        if not self.secret:
            return False
        timestamp = headers.get(TIMESTAMP_HEADER, "")
        signature = headers.get(SIGNATURE_HEADER, "")
        nonce = headers.get(NONCE_HEADER, "")
        if not timestamp or not signature or not timestamp.isdigit():
            return False
        if not _NONCE_RE.fullmatch(nonce):
            return False
        if abs(self.clock() - int(timestamp)) > self.max_skew_seconds:
            return False
        return True

    def _verify_body(self, headers: dict[str, str], scope: Scope, body: bytes) -> bool:
        """The MAC check, which needs the body; only reached once the header checks passed."""
        timestamp = headers[TIMESTAMP_HEADER]
        signature = headers[SIGNATURE_HEADER]
        nonce = headers[NONCE_HEADER]
        path = scope["path"]
        query = scope.get("query_string", b"")
        if query:
            path = f"{path}?{query.decode('latin-1')}"
        expected = sign(self.secret, timestamp, nonce, scope["method"], path, body)
        if not hmac.compare_digest(expected, signature.lower()):
            return False
        # Claimed only after the MAC checks out, so unsigned junk cannot fill the store or burn nonces.
        return self.nonces.claim(nonce)


def _header_map(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}


def _content_length(headers: dict[str, str]) -> int | None:
    raw = headers.get(CONTENT_LENGTH_HEADER)
    if raw is None or not raw.isdigit():
        return None
    return int(raw)


async def _unauthorised(send: Send) -> None:
    body = b'{"detail":"invalid or missing signature"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _payload_too_large(send: Send) -> None:
    body = b'{"detail":"request body too large"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
