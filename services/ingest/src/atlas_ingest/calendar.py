"""Structured rows for the calendar, earnings and sentiment collectors (ATL-61).

These sources do not produce news_items: their collectors parse the provider's response into the
dataclasses below and write ``economic_events``, ``earnings_events`` and ``market_sentiment``
directly with ``INSERT ... ON CONFLICT``. Every batch is de-duplicated on its conflict key first
(a batch that touches one row twice raises SQLSTATE 21000) and sorted by that key (concurrent
upserts in one order cannot deadlock each other).

Updates use ``ON CONFLICT DO UPDATE ... WHERE <something changed>``, so ``RETURNING`` only counts
rows that were inserted or really changed. That count is what the runner records as "items".
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from atlas_shared.db import EarningsEvent, EconomicEvent, MarketSentiment, NewsSource
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, Table, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert

CHUNK = 1000

_SUFFIX = {"K": Decimal(1000), "M": Decimal(1000) ** 2, "B": Decimal(1000) ** 3, "T": Decimal(1000) ** 4}
_NUMBER = re.compile(r"^[<>~≈]?\s*([+-]?\d+(?:\.\d+)?)\s*([KMBT])?\s*%?$", re.IGNORECASE)


def json_decimal(content: bytes) -> Any:
    """Parse a provider's JSON with every non-integer number as Decimal (never float)."""
    return json.loads(content, parse_float=Decimal)


def parse_number(text: str | None) -> Decimal | None:
    """``"3.2%"`` → 3.2, ``"245K"`` → 245000, ``"-1.5B"`` → -1500000000, ``"1,234"`` → 1234;
    anything that is not one plain number (``"n/a"``, ``"1.2|1.3"``, ``""``) → None."""
    if text is None:
        return None
    m = _NUMBER.match(text.strip().replace(",", ""))
    if m is None:
        return None
    try:
        value = Decimal(m.group(1))
    except InvalidOperation:
        return None
    suffix = m.group(2)
    return value * _SUFFIX[suffix.upper()] if suffix else value


def surprise(actual: str | None, forecast: str | None) -> Decimal | None:
    """actual - forecast in the published units, when both are numeric."""
    a, f = parse_number(actual), parse_number(forecast)
    return a - f if a is not None and f is not None else None


def external_id(country: str, name: str, scheduled_at: datetime) -> str:
    """Stable id for one scheduled release: sha256 of country | name | UTC time."""
    stamp = scheduled_at.astimezone(UTC).isoformat()
    return hashlib.sha256(f"{country}|{name}|{stamp}".encode()).hexdigest()


def importance_for(impact: str | None) -> int:
    """Map a provider's impact label through ``news.calendar_importance`` (case-insensitive).
    0 means the event is not stored."""
    mapping: dict[str, Any] = DEFAULTS["news"]["calendar_importance"]
    lowered = {str(k).lower(): int(v) for k, v in mapping.items()}
    if impact is not None and impact.strip().lower() in lowered:
        return lowered[impact.strip().lower()]
    return int(DEFAULTS["news"]["calendar"]["unmapped_importance"])


@dataclass(frozen=True)
class EconomicRow:
    country: str
    name: str
    importance: int
    scheduled_at: datetime  # UTC
    actual: str | None
    forecast: str | None
    previous: str | None
    surprise: Decimal | None

    @property
    def external_id(self) -> str:
        return external_id(self.country, self.name, self.scheduled_at)


@dataclass(frozen=True)
class EarningsRow:
    symbol: str
    date: date
    time: str | None
    eps_est: Decimal | None
    eps_act: Decimal | None
    rev_est: Decimal | None
    rev_act: Decimal | None


@dataclass(frozen=True)
class SentimentRow:
    index_key: str
    value: Decimal
    classification: str | None
    ts: datetime


def _chunks(rows: Sequence[dict[str, Any]]) -> Iterable[Sequence[dict[str, Any]]]:
    for i in range(0, len(rows), CHUNK):
        yield rows[i : i + CHUNK]


def _upsert(
    engine: Engine,
    table: Table,
    values: list[dict[str, Any]],
    keys: Sequence[str],
    *,
    keep_if_null: Sequence[str] = (),
    overwrite: Sequence[str] = (),
) -> int:
    """INSERT ... ON CONFLICT (keys) DO UPDATE, only where a value really changes. Columns in
    ``keep_if_null`` keep the stored value when the new one is NULL. ``values`` must already be
    de-duplicated on ``keys``. Returns rows inserted or changed."""
    changed = 0
    with engine.begin() as conn:
        for chunk in _chunks(values):
            ins = insert(table).values([{**v, "id": uuid.uuid4()} for v in chunk])
            ex = ins.excluded
            new: dict[str, Any] = {c: func.coalesce(ex[c], table.c[c]) for c in keep_if_null}
            new.update({c: ex[c] for c in overwrite})
            stmt = ins.on_conflict_do_update(
                index_elements=[table.c[k] for k in keys],
                set_={**new, "updated_at": ex.updated_at},
                where=or_(*(table.c[c].is_distinct_from(expr) for c, expr in new.items())),
            ).returning(table.c.id)
            changed += len(conn.execute(stmt).all())
    return changed


def _table(model: Any) -> Table:
    table = model.__table__
    assert isinstance(table, Table)
    return table


def upsert_economic(engine: Engine, source: str, rows: Iterable[EconomicRow], now: datetime) -> int:
    """Insert or update economic_events on (source, external_id). A later poll that has no
    actual/forecast/previous keeps the stored value. Returns rows inserted or changed."""
    batch: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.importance <= 0:
            continue
        batch[r.external_id] = {  # last occurrence wins
            "source": source,
            "external_id": r.external_id,
            "country": r.country[:16],
            "name": r.name[:255],
            "importance": min(r.importance, 5),
            "scheduled_at": r.scheduled_at,
            "actual": r.actual,
            "forecast": r.forecast,
            "previous": r.previous,
            "surprise": r.surprise,
            "created_at": now,
            "updated_at": now,
        }
    return _upsert(
        engine,
        _table(EconomicEvent),
        [batch[k] for k in sorted(batch)],
        ("source", "external_id"),
        keep_if_null=("actual", "forecast", "previous", "surprise"),
        overwrite=("country", "name", "importance"),
    )


def upsert_earnings(engine: Engine, rows: Iterable[EarningsRow], now: datetime) -> int:
    """Insert or update earnings_events on (symbol, date). Returns rows inserted or changed."""
    batch: dict[tuple[str, date], dict[str, Any]] = {}
    for r in rows:
        batch[(r.symbol, r.date)] = {  # last occurrence wins
            "symbol": r.symbol[:32],
            "date": r.date,
            "time": r.time,
            "eps_est": r.eps_est,
            "eps_act": r.eps_act,
            "rev_est": r.rev_est,
            "rev_act": r.rev_act,
            "created_at": now,
            "updated_at": now,
        }
    return _upsert(
        engine,
        _table(EarningsEvent),
        [batch[k] for k in sorted(batch)],
        ("symbol", "date"),
        keep_if_null=("time", "eps_est", "eps_act", "rev_est", "rev_act"),
    )


def upsert_sentiment(engine: Engine, rows: Iterable[SentimentRow], now: datetime) -> int:
    """One market_sentiment row per (index_key, ts); a re-poll of the same reading changes
    nothing. Returns rows inserted or changed."""
    batch: dict[tuple[str, datetime], dict[str, Any]] = {}
    for r in rows:
        batch[(r.index_key, r.ts)] = {
            "index_key": r.index_key,
            "value": r.value,
            "classification": r.classification,
            "ts": r.ts,
            "created_at": now,
            "updated_at": now,
        }
    return _upsert(
        engine,
        _table(MarketSentiment),
        [batch[k] for k in sorted(batch)],
        ("index_key", "ts"),
        overwrite=("value", "classification"),
    )


def calendar_poll_seconds(engine: Engine, now: datetime, base_seconds: float) -> float:
    """Seconds until the next calendar poll (brief §7.2): ``fast_poll_seconds`` while any stored
    event (importance ≥ ``fast_min_importance``) is within ±``fast_window_minutes`` of now;
    otherwise ``base_seconds``, shortened so the next poll lands when the next event's window opens."""
    cfg: dict[str, Any] = DEFAULTS["news"]["calendar"]
    fast = float(cfg["fast_poll_seconds"])
    window = timedelta(minutes=int(cfg["fast_window_minutes"]))
    min_importance = int(cfg["fast_min_importance"])
    t = _table(EconomicEvent)
    with engine.connect() as conn:
        upcoming = conn.execute(
            select(func.min(t.c.scheduled_at)).where(
                t.c.importance >= min_importance, t.c.scheduled_at >= now - window
            )
        ).scalar()
    if not isinstance(upcoming, datetime):
        return base_seconds
    opens = upcoming - window
    if opens <= now:
        return fast
    return max(fast, min(base_seconds, (opens - now).total_seconds()))


def finnhub_calendar_active(engine: Engine, now: datetime) -> bool:
    """True when finnhub.calendar is enabled, keyed and not degraded: it is then the calendar
    source and forexfactory.calendar stands down."""
    with engine.connect() as conn:
        found = conn.execute(
            select(NewsSource.id).where(
                NewsSource.key == "finnhub.calendar",
                NewsSource.enabled.is_(True),
                NewsSource.api_key.is_not(None),
                and_(or_(NewsSource.disabled_until.is_(None), NewsSource.disabled_until <= now)),
            )
        ).first()
    return found is not None
