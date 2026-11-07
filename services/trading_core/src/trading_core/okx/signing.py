"""OKX v5 request signing (brief §4.1).

OK-ACCESS-SIGN = Base64(HMAC-SHA256(secret, timestamp + METHOD + requestPath(+?query) + body)),
where timestamp is ISO 8601 UTC with milliseconds, e.g. ``2020-12-08T09:08:57.715Z``.
"""

from __future__ import annotations

import base64
import hmac
from datetime import UTC, datetime
from hashlib import sha256


def iso_timestamp(epoch_ms: int) -> str:
    """Format epoch milliseconds as OKX's ``YYYY-MM-DDTHH:MM:SS.mmmZ``."""
    dt = datetime.fromtimestamp(epoch_ms // 1000, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{epoch_ms % 1000:03d}Z"


def prehash(timestamp: str, method: str, request_path: str, body: str = "") -> str:
    return timestamp + method.upper() + request_path + body


def sign(secret: str, timestamp: str, method: str, request_path: str, body: str = "") -> str:
    """Return the Base64 OK-ACCESS-SIGN value. ``request_path`` includes ``?query`` when there is one."""
    message = prehash(timestamp, method, request_path, body)
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), sha256).digest()
    return base64.b64encode(digest).decode("ascii")
