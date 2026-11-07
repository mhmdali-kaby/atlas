"""Instrument universe sync (ATL-9, brief §2 and §4.3).

For one environment: fetch public/instruments for each type in ``instruments.inst_types``
(SPOT, SWAP, FUTURES), upsert every row into ``instruments`` keyed (env, inst_id) with a derived
``asset_class``, and mark rows of those types that OKX no longer lists as ``delisted_local``.
Nothing is ever deleted. The outcome is recorded in ``sync_status`` (stream ``instruments``).

Public endpoints only, so this runs before any connection exists.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from atlas_shared.db import Instrument, SyncStatus
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, update
from sqlalchemy.dialects.postgresql import insert

from trading_core.okx import OkxClient
from trading_core.okx.models import Instrument as OkxInstrument

logger = logging.getLogger("trading_core.sync.instruments")

STREAM = "instruments"
OTHER = "other"
_CHUNK = 500  # rows per INSERT; 22 columns x 500 stays far below Postgres' 65535 bind parameters

_CFG: Mapping[str, Any] = DEFAULTS["instruments"]


@dataclass(frozen=True)
class InstrumentSyncResult:
    env: str
    fetched: int
    upserted: int
    delisted: int
    by_type: dict[str, int]
    by_asset_class: dict[str, int]
    synced_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "fetched": self.fetched,
            "upserted": self.upserted,
            "delisted": self.delisted,
            "by_type": self.by_type,
            "by_asset_class": self.by_asset_class,
            "synced_at": self.synced_at.isoformat().replace("+00:00", "Z"),
        }


def base_symbol(inst: OkxInstrument) -> str:
    """The underlying symbol: baseCcy for spot, else the first part of instFamily/instId.

    OKX leaves baseCcy empty for SWAP/FUTURES, so "TSLA-USDT-SWAP" (family "TSLA-USDT") → "TSLA".
    """
    if inst.base_ccy:
        return inst.base_ccy.upper()
    return (inst.inst_family or inst.inst_id).split("-", 1)[0].upper()


def derive_asset_class(
    inst: OkxInstrument,
    *,
    category_map: Mapping[str, str] | None = None,
    stock_overrides: Mapping[str, str] | None = None,
) -> str:
    """Map OKX instCategory to Atlas' asset_class; anything unknown or missing is "other"."""
    categories = _CFG["category_asset_class"] if category_map is None else category_map
    overrides = _CFG["stock_class_overrides"] if stock_overrides is None else stock_overrides
    asset_class = categories.get((inst.inst_category or "").strip(), OTHER)
    if asset_class == "stock":
        asset_class = overrides.get(base_symbol(inst), asset_class)
    return str(asset_class)


async def fetch_universe(okx: OkxClient, inst_types: Sequence[str]) -> dict[str, list[OkxInstrument]]:
    """Every instrument of each type. Raises if any type fails, so a partial universe never delists."""
    universe: dict[str, list[OkxInstrument]] = {}
    for inst_type in inst_types:
        universe[inst_type] = await okx.get_instruments(inst_type)
    return universe


def _row(env: str, inst: OkxInstrument, now: datetime) -> dict[str, Any] | None:
    if inst.lot_sz is None or inst.min_sz is None or inst.tick_sz is None:
        return None
    return {
        "env": env,
        "inst_id": inst.inst_id,
        "inst_type": inst.inst_type,
        "inst_family": inst.inst_family,
        "inst_category": inst.inst_category,
        "base_ccy": inst.base_ccy,
        "quote_ccy": inst.quote_ccy,
        "settle_ccy": inst.settle_ccy,
        "ct_val": inst.ct_val,
        "ct_val_ccy": inst.ct_val_ccy,
        "ct_mult": inst.ct_mult,
        "lot_sz": inst.lot_sz,
        "min_sz": inst.min_sz,
        "tick_sz": inst.tick_sz,
        "max_lever": inst.max_lever,
        "state": inst.state,
        "list_time": inst.list_time,
        "exp_time": inst.exp_time,
        "asset_class": derive_asset_class(inst),
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }


def _chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def write_universe(
    engine: Engine, env: str, universe: Mapping[str, Sequence[OkxInstrument]], now: datetime
) -> InstrumentSyncResult:
    """Upsert the fetched universe and delist what is gone, in one transaction."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_type: dict[str, int] = {}
    for inst_type, instruments in universe.items():
        by_type[inst_type] = len(instruments)
        for inst in instruments:
            seen.add(inst.inst_id)  # even a malformed row is "still listed": never delist it
            row = _row(env, inst, now)
            if row is None:
                logger.warning(
                    "instrument without lotSz/minSz/tickSz skipped", extra={"inst_id": inst.inst_id}
                )
                continue
            rows.append(row)

    delisted_state = str(_CFG["delisted_state"])
    with engine.begin() as conn:
        for chunk in _chunks(rows, _CHUNK):
            stmt = insert(Instrument).values(chunk)
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=[Instrument.env, Instrument.inst_id],
                set_={
                    col: getattr(excluded, col)
                    for col in chunk[0]
                    if col not in ("env", "inst_id", "created_at")
                },
            )
            conn.execute(stmt)

        delist = (
            update(Instrument)
            .where(
                Instrument.env == env,
                Instrument.inst_type.in_(list(universe.keys())),
                Instrument.state != delisted_state,
            )
            .values(state=delisted_state, updated_at=now)
        )
        if seen:
            delist = delist.where(Instrument.inst_id.not_in(sorted(seen)))
        delisted = conn.execute(delist).rowcount

        _write_status(conn, env, now, error=None)

    by_class: dict[str, int] = {}
    for row in rows:
        by_class[row["asset_class"]] = by_class.get(row["asset_class"], 0) + 1
    return InstrumentSyncResult(
        env=env,
        fetched=sum(by_type.values()),
        upserted=len(rows),
        delisted=delisted,
        by_type=by_type,
        by_asset_class=dict(sorted(by_class.items())),
        synced_at=now,
    )


def _write_status(conn: Any, env: str, now: datetime, *, error: str | None) -> None:
    values: dict[str, Any] = {
        "env": env,
        "stream": STREAM,
        "last_error": error,
        "created_at": now,
        "updated_at": now,
    }
    update_set: dict[str, Any] = {"last_error": error, "updated_at": now}
    if error is None:
        values["last_success_at"] = now
        update_set["last_success_at"] = now
    stmt = insert(SyncStatus).values(values)
    conn.execute(
        stmt.on_conflict_do_update(index_elements=[SyncStatus.env, SyncStatus.stream], set_=update_set)
    )


def record_failure(engine: Engine, env: str, error: str, now: datetime | None = None) -> None:
    """Store the error on sync_status; last_success_at keeps the previous good run."""
    with engine.begin() as conn:
        _write_status(conn, env, now or datetime.now(UTC), error=error[:2000])


async def sync_instruments(
    env: str,
    *,
    okx: OkxClient,
    engine: Engine,
    inst_types: Sequence[str] | None = None,
    now: datetime | None = None,
) -> InstrumentSyncResult:
    """Fetch and store the universe for ``env``. On failure, record it in sync_status and re-raise."""
    types = list(_CFG["inst_types"] if inst_types is None else inst_types)
    try:
        universe = await fetch_universe(okx, types)
        stamp = now or datetime.now(UTC)
        result = await asyncio.to_thread(write_universe, engine, env, universe, stamp)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        try:
            await asyncio.to_thread(record_failure, engine, env, message)
        except Exception:
            logger.exception("could not record instruments sync failure")
        raise
    logger.info("instruments synced", extra=result.as_dict())
    return result


def instruments_job(env: str, engine: Engine) -> Any:
    """The scheduler job: a fresh public client per run, so nothing is held between hours."""

    async def run() -> dict[str, Any]:
        async with OkxClient(env) as okx:
            result = await sync_instruments(env, okx=okx, engine=engine)
        return result.as_dict()

    return run
