"""HMAC signing for calls to trading_core's internal API (decision #235).

Signature = hex HMAC-SHA256(INTERNAL_SHARED_SECRET, timestamp + nonce + METHOD + path + raw body),
where `timestamp` is unix seconds (X-Atlas-Timestamp), `nonce` is 32 lower-case hex chars
(X-Atlas-Nonce, accepted once), METHOD is upper case and `path` includes `?query` when there is one.
trading_core.security verifies it; the PHP twin is App\\Support\\TradingCore. The agents process signs
its own calls with this module (ATL-160: the Orchestrator's risk check).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time


def sign(secret: str, timestamp: str, nonce: str, method: str, path: str, body: bytes) -> str:
    message = f"{timestamp}{nonce}{method.upper()}{path}".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def new_nonce() -> str:
    return secrets.token_hex(16)


def signed_headers(
    secret: str,
    method: str,
    path: str,
    body: bytes = b"",
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """The three X-Atlas-* headers for one call, exactly as Laravel sends them."""
    ts = str(int(time.time()) if timestamp is None else timestamp)
    n = new_nonce() if nonce is None else nonce
    return {
        "X-Atlas-Timestamp": ts,
        "X-Atlas-Nonce": n,
        "X-Atlas-Signature": sign(secret, ts, n, method, path, body),
    }


__all__ = ["new_nonce", "sign", "signed_headers"]
