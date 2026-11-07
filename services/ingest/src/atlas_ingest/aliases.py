"""Build and sync ``instrument_aliases`` for the active env (ATL-62, decision #243).

``uv run --package atlas-ingest python -m atlas_ingest.aliases sync`` rebuilds the table from the env's
``instruments`` plus the curated ``config/news_aliases.yaml``. The table has no env column: it always
holds the active env's aliases and is replaced wholesale, so running ``sync`` twice leaves the same
rows. ``watch`` re-syncs whenever the env's instrument count changes (an instruments sync landed);
:class:`AliasWatcher` is the same check for the ingest process to call from its own loop.

Targets are instrument families. Each base symbol has one canonical family in an env: ``BASE-USDT``
when listed, else ``BASE-USDC``, else the first family in sort order. A curated alias whose base is
not listed in the env is dropped.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from atlas_shared.db import Instrument, InstrumentAlias, make_engine
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, delete, func, insert, select

from atlas_ingest.linking import (
    ASSET_CLASS,
    CURATED_PRIORITY,
    FAMILY_PRIORITY,
    INSTRUMENT,
    TICKER_PRIORITY,
    AliasRow,
    CuratedAliases,
    Linker,
    TargetKind,
    load_curated,
)

FAMILY_QUOTES = ("USDT", "USDC", "USD")
_PREFERRED_QUOTES = ("USDT", "USDC")


def active_env() -> str:
    env = (
        os.environ.get("ATLAS_ENV") or os.environ.get("ATLAS_DEFAULT_ENV") or str(DEFAULTS["env"]["default"])
    )
    allowed: list[str] = DEFAULTS["env"]["allowed"]
    if env not in allowed:
        raise ValueError(f"ATLAS_ENV must be one of {allowed}")
    return env


@dataclass(frozen=True)
class InstrumentInfo:
    inst_id: str
    inst_type: str
    inst_family: str | None
    base_ccy: str | None
    quote_ccy: str | None
    asset_class: str


def base_symbol(inst: InstrumentInfo) -> str:
    """OKX leaves baseCcy empty on swaps/futures, so fall back to the family, then the instId."""
    if inst.base_ccy:
        return inst.base_ccy.upper()
    source = inst.inst_family or inst.inst_id
    return source.split("-", 1)[0].upper()


def _family(inst: InstrumentInfo) -> str:
    if inst.inst_family:
        return inst.inst_family.upper()
    if inst.quote_ccy:
        return f"{base_symbol(inst)}-{inst.quote_ccy.upper()}"
    parts = inst.inst_id.upper().split("-")
    return "-".join(parts[:2])


def universe(instruments: Iterable[InstrumentInfo]) -> dict[str, tuple[str, str]]:
    """base symbol → (canonical family, asset class)."""
    families: dict[str, dict[str, str]] = {}
    for inst in instruments:
        base = base_symbol(inst)
        if not base:
            continue
        fam = _family(inst)
        known = families.setdefault(base, {})
        # A derivative's class wins over a spot pair's (spot crypto rows may be "other").
        if fam not in known or (known[fam] == "other" and inst.asset_class != "other"):
            known[fam] = inst.asset_class
    out: dict[str, tuple[str, str]] = {}
    for base, fams in families.items():
        preferred = [f"{base}-{q}" for q in _PREFERRED_QUOTES if f"{base}-{q}" in fams]
        family = preferred[0] if preferred else sorted(fams)[0]
        out[base] = (family, fams[family])
    return out


def build_alias_rows(instruments: Iterable[InstrumentInfo], curated: CuratedAliases) -> list[AliasRow]:
    """Curated aliases first (highest priority), then generated family and ticker aliases.
    Deduplicated on (alias_normalized, target), the table's unique key; the first row wins."""
    uni = universe(instruments)
    rows: list[AliasRow] = []
    for base, aliases in curated.instruments.items():
        hit = uni.get(base.upper())
        if hit is None:
            continue
        rows.extend(AliasRow(a, INSTRUMENT, hit[0], CURATED_PRIORITY) for a in aliases)
    for group in curated.asset_classes:
        rows.extend(
            AliasRow(a, ASSET_CLASS, c, CURATED_PRIORITY) for a in group.aliases for c in group.classes
        )

    stop = {s.upper() for s in curated.ticker_stoplist}
    curated_bases = {b.upper() for b in curated.instruments}
    for base in sorted(uni):
        family = uni[base][0]
        rows.extend(AliasRow(f"{base}-{q}", INSTRUMENT, family, FAMILY_PRIORITY) for q in FAMILY_QUOTES)
        if not base.isalnum() or not base[:1].isalpha():
            continue
        cashtag_only = base in stop or (len(base) <= 2 and base not in curated_bases)
        rows.append(AliasRow(f"${base}" if cashtag_only else base, INSTRUMENT, family, TICKER_PRIORITY))

    seen: set[tuple[str, str]] = set()
    unique: list[AliasRow] = []
    for row in rows:
        key = (row.alias_normalized, row.target)
        if key in seen or not row.alias_normalized or len(row.alias) > 191:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _instruments(engine: Engine, env: str) -> list[InstrumentInfo]:
    with engine.connect() as conn:
        result = conn.execute(
            select(
                Instrument.inst_id,
                Instrument.inst_type,
                Instrument.inst_family,
                Instrument.base_ccy,
                Instrument.quote_ccy,
                Instrument.asset_class,
            ).where(Instrument.env == env)
        )
        return [InstrumentInfo(*r) for r in result]


def instrument_count(engine: Engine, env: str) -> int:
    with engine.connect() as conn:
        n = conn.execute(
            select(func.count()).select_from(Instrument).where(Instrument.env == env)
        ).scalar_one()
    return int(n)


def sync(engine: Engine, env: str, curated: CuratedAliases | None = None, now: datetime | None = None) -> int:
    """Replace ``instrument_aliases`` with the env's aliases in one transaction; returns the row count."""
    rows = build_alias_rows(_instruments(engine, env), curated or load_curated())
    stamp = now or datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(delete(InstrumentAlias))
        if rows:
            conn.execute(
                insert(InstrumentAlias),
                [
                    {
                        "id": uuid.uuid4(),
                        "alias": r.alias,
                        "alias_normalized": r.alias_normalized,
                        "target_kind": r.target_kind,
                        "target": r.target,
                        "priority": r.priority,
                        "created_at": stamp,
                        "updated_at": stamp,
                    }
                    for r in rows
                ],
            )
    return len(rows)


def load_rows(engine: Engine) -> list[AliasRow]:
    with engine.connect() as conn:
        result = conn.execute(
            select(
                InstrumentAlias.alias,
                InstrumentAlias.target_kind,
                InstrumentAlias.target,
                InstrumentAlias.priority,
            )
        )
        return [
            AliasRow(alias, cast(TargetKind, kind), target, int(prio)) for alias, kind, target, prio in result
        ]


def families_for(instruments: Iterable[InstrumentInfo]) -> dict[str, str]:
    """canonical family → asset class, the set a :class:`Linker` may link to."""
    return {family: cls for family, cls in universe(instruments).values()}


def make_linker(
    rows: Iterable[AliasRow], instruments: Iterable[InstrumentInfo], curated: CuratedAliases | None = None
) -> Linker:
    return Linker(rows, families_for(instruments), (curated or load_curated()).context_rules)


def load_linker(engine: Engine, env: str, curated: CuratedAliases | None = None) -> Linker:
    """A linker over the stored aliases, limited to families the env lists today."""
    return make_linker(load_rows(engine), _instruments(engine, env), curated)


class AliasWatcher:
    """Re-syncs aliases when the env's instrument count changes. The first poll always syncs."""

    def __init__(self, engine: Engine, env: str, curated: CuratedAliases | None = None) -> None:
        self.engine = engine
        self.env = env
        self.curated = curated
        self.last_count: int | None = None

    def poll(self) -> int | None:
        """Row count written when a sync ran, else None."""
        count = instrument_count(self.engine, self.env)
        if count == self.last_count:
            return None
        written = sync(self.engine, self.env, self.curated)
        self.last_count = count
        return written


def main(argv: Sequence[str] | None = None, env_vars: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m atlas_ingest.aliases")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sync", help="rebuild instrument_aliases for the active env")
    s.add_argument("--env", help="defaults to ATLAS_ENV / ATLAS_DEFAULT_ENV / config default")
    w = sub.add_parser("watch", help="re-sync whenever the env's instrument count changes")
    w.add_argument("--env")
    w.add_argument("--interval", type=float, default=60.0, help="seconds between checks")
    args = parser.parse_args(argv)

    env = args.env or active_env()
    engine = make_engine(env_vars)
    if args.command == "sync":
        print(f"instrument_aliases: {sync(engine, env)} rows for env {env}")
        return 0
    watcher = AliasWatcher(engine, env)
    while True:  # pragma: no cover - long-running loop
        written = watcher.poll()
        if written is not None:
            print(
                f"instrument_aliases: {written} rows for env {env} ({watcher.last_count} instruments)",
                flush=True,
            )
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
