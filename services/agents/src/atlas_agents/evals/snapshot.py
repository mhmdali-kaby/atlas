"""Load a case's ``data/`` snapshot into the checkout's test DB, inside the case's transaction.

Each ``<table>.json`` names a Laravel-owned table known to ``atlas_shared.db`` models. Before the
rows go in, rows of that table for the same ``(env, inst_id)`` pairs are deleted (tables with both
columns), so leftovers in the test DB cannot leak into a replayed day. The runner rolls the whole
transaction back after the case, so a run leaves nothing behind.

Values are JSON: timestamps as ISO strings with a zone, decimals as strings (or numbers), UUIDs as
strings. A missing ``id`` on a UUID primary key is generated; missing ``created_at``/``updated_at``
default to ``replay_as_of``.

A table that is not an ``atlas_shared.db`` model (``settings``, ``settings_history``) is reflected
from the database. ``settings`` holds one row per env, so its rows for the snapshot's envs are
deleted first (``settings_history`` goes with them, ON DELETE CASCADE).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from atlas_shared.db import Base
from sqlalchemy import Connection, MetaData, Table, and_, delete, insert, or_
from sqlalchemy.exc import NoSuchTableError

# Tables keyed by env alone: a snapshot replaces the env's row instead of adding a second one.
_ONE_ROW_PER_ENV = frozenset({"settings"})


class SnapshotError(ValueError):
    """The snapshot names an unknown table or column, or holds a value of the wrong type."""


def _table(name: str, conn: Connection | None = None, reflected: MetaData | None = None) -> Table:
    table = Base.metadata.tables.get(name)
    if table is not None:
        return table
    if conn is not None and reflected is not None:
        try:
            return Table(name, reflected, autoload_with=conn)
        except NoSuchTableError:
            pass
    raise SnapshotError(f"snapshot table [{name}] is not an atlas_shared.db model or database table")


def _python_type(column: Any) -> type | None:
    try:
        kind: type = column.type.python_type
    except NotImplementedError:
        return None
    return kind


def _coerce(table: Table, row: Mapping[str, Any], replay_as_of: datetime) -> dict[str, Any]:
    unknown = set(row) - set(table.c.keys())
    if unknown:
        raise SnapshotError(f"{table.name}: unknown columns {sorted(unknown)}")
    out: dict[str, Any] = {}
    for column in table.c:
        if column.name not in row:
            if column.name in ("created_at", "updated_at"):
                out[column.name] = replay_as_of
            elif column.name == "id" and column.primary_key and _python_type(column) is uuid.UUID:
                out[column.name] = uuid.uuid4()
            continue
        value = row[column.name]
        kind = _python_type(column)
        if value is None or kind is None:
            out[column.name] = value
        elif kind is datetime and isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.utcoffset() is None:
                raise SnapshotError(f"{table.name}.{column.name}: timestamp {value!r} has no zone")
            out[column.name] = parsed
        elif kind is Decimal and isinstance(value, (str, int, float)):
            out[column.name] = Decimal(str(value))
        elif kind is uuid.UUID and isinstance(value, str):
            out[column.name] = uuid.UUID(value)
        else:
            out[column.name] = value
    return out


def load_snapshot(
    conn: Connection, snapshot: Mapping[str, Sequence[Mapping[str, Any]]], *, replay_as_of: datetime
) -> dict[str, int]:
    """Insert every snapshot table; return rows loaded per table."""
    loaded: dict[str, int] = {}
    reflected = MetaData()
    # Parents before children (watchlists before watchlist_items); tables that are not models first,
    # in the snapshot's own order (settings before settings_history).
    rank = {t.name: i for i, t in enumerate(Base.metadata.sorted_tables)}
    for name, rows in sorted(snapshot.items(), key=lambda kv: rank.get(kv[0], -1)):
        table = _table(name, conn, reflected)
        values = [_coerce(table, row, replay_as_of) for row in rows]
        if name in _ONE_ROW_PER_ENV and "env" in table.c:
            envs = {v.get("env") for v in values}
            if envs:
                conn.execute(delete(table).where(table.c.env.in_(envs)))
        if "env" in table.c and "inst_id" in table.c:
            pairs = {(v.get("env"), v.get("inst_id")) for v in values}
            if pairs:
                conn.execute(
                    delete(table).where(
                        or_(*(and_(table.c.env == env, table.c.inst_id == inst) for env, inst in pairs))
                    )
                )
        if values:
            conn.execute(insert(table), values)
        loaded[name] = len(values)
    return loaded
