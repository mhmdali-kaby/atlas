"""HMAC signing for Laravel -> trading_core calls (decision #235).

Signature = hex HMAC-SHA256(INTERNAL_SHARED_SECRET, timestamp + METHOD + path + raw body), where
`timestamp` is unix seconds as sent in X-Atlas-Timestamp, METHOD is upper case, and `path` is the
request path plus `?query` when there is a query string. The PHP twin is App\\Support\\TradingCore.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

TIMESTAMP_HEADER = "x-atlas-timestamp"
SIGNATURE_HEADER = "x-atlas-signature"
PROTECTED_PREFIX = "/internal"


def sign(secret: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    message = f"{timestamp}{method.upper()}{path}".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def is_protected(path: str) -> bool:
    return path == PROTECTED_PREFIX or path.startswith(PROTECTED_PREFIX + "/")


class InternalSignatureMiddleware:
    """Rejects every /internal request whose signature or timestamp does not check out with 401.

    Pure ASGI so the raw body can be read for the MAC and then replayed to the handler unchanged.
    Fails closed: with no secret configured, every /internal call is rejected.
    """

    def __init__(
        self,
        app: ASGIApp,
        secret: str,
        max_skew_seconds: int = 30,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.app = app
        self.secret = secret
        self.max_skew_seconds = max_skew_seconds
        self.clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_protected(scope["path"]):
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        if not self._verify(scope, body):
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

    def _verify(self, scope: Scope, body: bytes) -> bool:
        if not self.secret:
            return False
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        timestamp = headers.get(TIMESTAMP_HEADER, "")
        signature = headers.get(SIGNATURE_HEADER, "")
        if not timestamp or not signature or not timestamp.isdigit():
            return False
        if abs(self.clock() - int(timestamp)) > self.max_skew_seconds:
            return False
        path = scope["path"]
        query = scope.get("query_string", b"")
        if query:
            path = f"{path}?{query.decode('latin-1')}"
        expected = sign(self.secret, timestamp, scope["method"], path, body)
        return hmac.compare_digest(expected, signature.lower())


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
