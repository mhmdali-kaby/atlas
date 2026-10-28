"""Redis Streams publisher for the Atlas event bus (brief §3.2).

Same layout as Laravel's ``App\\Support\\Events\\EventBus``: each stream entry has one field,
``envelope``, holding the :class:`~atlas_shared.events.EventEnvelope` as JSON. Every XADD trims the
stream to about ``event_bus.stream_maxlen`` entries (``MAXLEN ~``); Postgres is the record.
"""

import os
from collections.abc import Mapping
from typing import Final

import redis

from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import STREAMS, EventEnvelope

FIELD: Final = "envelope"


class UnknownStreamError(ValueError):
    """Raised when publishing to a stream that is not one of the seven in the brief."""


def stream_maxlen() -> int:
    value = DEFAULTS["event_bus"]["stream_maxlen"]
    if not isinstance(value, int) or value <= 0:
        raise ValueError("event_bus.stream_maxlen must be a positive integer")
    return value


def make_client(env: Mapping[str, str] | None = None) -> "redis.Redis":
    """A client for the bus, from the same REDIS_* variables as Laravel (no key prefix)."""
    source = os.environ if env is None else env
    password = source.get("REDIS_PASSWORD") or None
    return redis.Redis(
        host=source.get("REDIS_HOST", "127.0.0.1"),
        port=int(source.get("REDIS_PORT", "6380")),
        db=int(source.get("REDIS_DB", "0")),
        username=source.get("REDIS_USERNAME") or None,
        # Laravel .env writes REDIS_PASSWORD=null for "no password".
        password=None if password == "null" else password,  # noqa: S105
        decode_responses=True,
    )


def check_stream(stream: str) -> str:
    if stream not in STREAMS:
        raise UnknownStreamError(f"Unknown event stream [{stream}].")
    return stream


def publish(stream: str, envelope: EventEnvelope, *, client: "redis.Redis | None" = None) -> str:
    """XADD ``envelope`` to ``stream`` and return the stream entry id."""
    check_stream(stream)
    conn = client if client is not None else make_client()
    entry_id = conn.xadd(
        stream,
        {FIELD: envelope.model_dump_json()},
        maxlen=stream_maxlen(),
        approximate=True,
    )
    return entry_id if isinstance(entry_id, str) else bytes(entry_id).decode()
