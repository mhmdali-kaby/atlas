"""Reads ``news_sources`` and writes its health columns (ATL-59). Ingest never touches the owner's
columns (enabled, url, poll_seconds, api_key)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from atlas_shared.db import Instrument, NewsSource
from sqlalchemy import Engine, case, select, update


@dataclass(frozen=True)
class SourceRow:
    key: str
    kind: str
    category: str
    name: str
    url: str | None
    enabled: bool
    poll_seconds: int
    requires_key: bool
    disabled_until: datetime | None
    api_key_ciphertext: str | None = field(default=None, repr=False)

    @property
    def missing_key(self) -> bool:
        return self.requires_key and not self.api_key_ciphertext


def _row(s: NewsSource) -> SourceRow:
    return SourceRow(
        key=s.key,
        kind=s.kind,
        category=s.category,
        name=s.name,
        url=s.url,
        enabled=s.enabled,
        poll_seconds=s.poll_seconds,
        requires_key=s.requires_key,
        disabled_until=s.disabled_until,
        api_key_ciphertext=s.api_key,
    )


def load_enabled(engine: Engine) -> list[SourceRow]:
    with engine.connect() as conn:
        rows = conn.execute(select(NewsSource).where(NewsSource.enabled.is_(True)).order_by(NewsSource.key))
        return [_row(r) for r in rows]  # type: ignore[arg-type]


def load_one(engine: Engine, key: str) -> SourceRow | None:
    with engine.connect() as conn:
        row = conn.execute(select(NewsSource).where(NewsSource.key == key)).first()
        return _row(row) if row is not None else None  # type: ignore[arg-type]


def record_success(engine: Engine, key: str, now: datetime, *, inserted: int) -> None:
    values: dict[str, object] = {
        "consecutive_failures": 0,
        "disabled_until": None,
        "last_error": None,
        "last_success_at": now,
        "updated_at": now,
    }
    if inserted:
        values["last_item_at"] = now
    with engine.begin() as conn:
        conn.execute(update(NewsSource).where(NewsSource.key == key).values(**values))


def record_failure(
    engine: Engine, key: str, now: datetime, error: str, *, threshold: int, disable_minutes: int
) -> tuple[int, datetime | None]:
    """Increment the failure counter; at ``threshold`` set disabled_until = now + disable_minutes.
    Returns the new counter and disabled_until."""
    count = NewsSource.consecutive_failures + 1
    stmt = (
        update(NewsSource)
        .where(NewsSource.key == key)
        .values(
            consecutive_failures=count,
            last_error=error,
            updated_at=now,
            disabled_until=case(
                (count >= threshold, now + timedelta(minutes=disable_minutes)),
                else_=NewsSource.disabled_until,
            ),
        )
        .returning(NewsSource.consecutive_failures, NewsSource.disabled_until)
    )
    with engine.begin() as conn:
        row = conn.execute(stmt).first()
    return (int(row[0]), row[1]) if row is not None else (0, None)


def stock_symbols(engine: Engine, env: str | None = None) -> list[str]:
    """Underlyings of every live stock/ETF instrument (``TSLA-USDT-SWAP`` → ``TSLA``), limited to
    ``env`` when given (the runner passes its active env)."""
    stmt = select(Instrument.inst_id).where(
        Instrument.asset_class.in_(("stock", "etf")), Instrument.state == "live"
    )
    if env is not None:
        stmt = stmt.where(Instrument.env == env)
    with engine.connect() as conn:
        ids = conn.execute(stmt).scalars()
        return sorted({inst_id.split("-", 1)[0] for inst_id in ids if inst_id})
