"""Shared plumbing for the execution modules: the stored OKX connection, the executor settings, the
OKX client factory and the bus publishers.

The Executor's plan, the entry lifecycle, the kill switch and the single close/cancel all read the same
connection row and settings, build the same single-attempt client and publish the same envelopes; this
is the one copy of each.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import redis
from atlas_shared import bus
from atlas_shared.db import Connection
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from trading_core.execution.config import WARROOM_STREAM, ExecutorConfig
from trading_core.okx.client import OkxClient, RetryPolicy
from trading_core.okx.connection import client_for_connection

log = logging.getLogger("trading_core.execution.okx_access")

OkxFactory = Callable[..., OkxClient]


@dataclass(frozen=True)
class CredSnapshot:
    """The stored (still encrypted) OKX keys of one connection, read inside one session."""

    env: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    passphrase: str = field(repr=False)

    @classmethod
    def of(cls, conn: Connection) -> CredSnapshot:
        return cls(conn.env, conn.api_key, conn.api_secret, conn.passphrase)


def load_creds(session: Session, env: str, *, verified_only: bool) -> CredSnapshot | None:
    """The env's OKX connection. ``verified_only`` skips one that never passed Test connection;
    otherwise the most recently verified one wins."""
    query = select(Connection).where(Connection.exchange == "okx", Connection.env == env)
    if verified_only:
        query = query.where(Connection.last_verified_at.is_not(None))
    conn = session.scalar(query.order_by(Connection.last_verified_at.desc().nulls_last()).limit(1))
    return CredSnapshot.of(conn) if conn is not None else None


def executor_settings(engine: Engine, env: str) -> dict[str, Any]:
    """The ``executor`` block of the env's settings (or the flat data when there is no block)."""
    with engine.connect() as c:
        row = c.execute(text("SELECT data FROM settings WHERE env = :e"), {"e": env}).first()
    data = row[0] if row is not None and isinstance(row[0], dict) else {}
    nested = data.get("executor")
    return nested if isinstance(nested, dict) else data


def default_factory(app_key: str) -> OkxFactory:
    def build(creds: Any, **kwargs: Any) -> OkxClient:
        return client_for_connection(creds, app_key, **kwargs)

    return build


def single_attempt() -> RetryPolicy:
    """One attempt per HTTP call: the callers retry (and re-read OKX) themselves."""
    return dataclasses.replace(RetryPolicy.from_defaults(), max_attempts=1)


def open_client(
    engine: Engine, env: str, client_factory: OkxFactory | None, app_key: str, *, verified_only: bool = False
) -> tuple[OkxClient | None, ExecutorConfig]:
    """The env's single-attempt OKX client (None without a stored connection) and executor config."""
    cfg = ExecutorConfig.from_settings(executor_settings(engine, env))
    with Session(engine) as s:
        creds = load_creds(s, env, verified_only=verified_only)
    if creds is None:
        return None, cfg
    return (client_factory or default_factory(app_key))(creds, retry=single_attempt()), cfg


def publish(
    rds: redis.Redis,
    stream: str,
    type_: str,
    env: str,
    payload: dict[str, Any],
    *,
    source: str,
    correlation_id: str | None = None,
) -> None:
    """Publish one envelope; a Redis failure is logged, never raised (the DB row is the record)."""
    body: dict[str, Any] = {"type": type_, "env": env, "source": source, "payload": payload}
    if correlation_id is not None:
        body["correlation_id"] = correlation_id
    try:
        bus.publish(stream, EventEnvelope.model_validate(body), client=rds)
    except redis.RedisError:
        log.warning("event publish failed", extra={"type": type_})


def warroom_payload(author: str, kind: str, content_md: str, card: dict[str, Any] | None) -> dict[str, Any]:
    """A system message in the War Room's main thread."""
    return {
        "thread": {"kind": "main", "key": "main"},
        "author_type": "system",
        "author": author,
        "kind": kind,
        "content_md": content_md,
        "card": card,
        "reply_to": None,
        "mentions": [],
        "reasoning_md": None,
        "tool_calls": None,
        "tokens_in": None,
        "tokens_out": None,
        "cost_usd": None,
    }


def publish_warroom(
    rds: redis.Redis,
    env: str,
    *,
    author: str,
    kind: str,
    content_md: str,
    card: dict[str, Any] | None,
    source: str,
    correlation_id: str | None = None,
) -> None:
    publish(
        rds,
        WARROOM_STREAM,
        "warroom.message",
        env,
        warroom_payload(author, kind, content_md, card),
        source=source,
        correlation_id=correlation_id,
    )


__all__ = [
    "CredSnapshot",
    "OkxFactory",
    "default_factory",
    "executor_settings",
    "load_creds",
    "open_client",
    "publish",
    "publish_warroom",
    "single_attempt",
    "warroom_payload",
]
