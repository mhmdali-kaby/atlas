"""Which backfill jobs exist (ATL-46, brief §7.3).

For every ``state=live`` instrument of the env whose market is enabled
(``backfill_runner.enabled_markets``), one ``backfill_jobs`` row per dataset:

- ``candles:<bar>`` for every bar in ``backfill_runner.dataset_order`` (all instrument types);
- ``funding`` for perpetual swaps;
- ``open_interest`` for crypto swaps/futures, keyed by the bare currency (``BTC``), because OKX's
  history series (rubik ``open-interest-volume``) is per currency. One job serves every BTC contract.

``target_from`` = creation time - ``backfill.depth_days[dataset]``; a null depth ("as far back as OKX
gives") stores ``backfill_runner.full_history_floor``. Creating is idempotent (ON CONFLICT DO NOTHING
on the unique (env, inst_id, dataset)); a re-sync also moves ``paused``/``failed`` jobs back to
``pending``. ``done`` jobs are left alone: the live poller keeps them fresh.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from atlas_shared.db import BackfillJob, Instrument
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, select, update
from sqlalchemy.dialects.postgresql import insert

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
PAUSED = "paused"

FUNDING = "funding"
OPEN_INTEREST = "open_interest"
CANDLES_PREFIX = "candles:"

_PERP_MARKETS: Mapping[str, str] = {
    "crypto": "crypto_perp",
    "stock": "stock_perp",
    "etf": "stock_perp",
    "index": "stock_perp",
    "commodity": "commodity_perp",
}


def runner_cfg() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["backfill_runner"]
    return cfg


def depth_days() -> Mapping[str, int | None]:
    depths: Mapping[str, int | None] = DEFAULTS["backfill"]["depth_days"]
    return depths


def full_history_floor() -> datetime:
    return datetime.fromisoformat(str(runner_cfg()["full_history_floor"]).replace("Z", "+00:00"))


def market_of(inst_type: str, asset_class: str) -> str:
    """The brief §14.5 market toggle an instrument falls under."""
    if inst_type == "SPOT":
        return "spot"
    if inst_type == "SWAP":
        return _PERP_MARKETS.get(asset_class, "other_perp")
    if inst_type == "FUTURES":
        return "futures"
    if inst_type == "OPTION":
        return "options"
    return "other"


def currency_of(inst: Any) -> str:
    """BTC for BTC-USDT-SWAP (OKX leaves baseCcy empty on derivatives)."""
    if inst.base_ccy:
        return str(inst.base_ccy).upper()
    return str(inst.inst_family or inst.inst_id).split("-", 1)[0].upper()


@dataclass(frozen=True)
class JobSpec:
    inst_id: str
    dataset: str


def datasets_for(inst: Any, order: Sequence[str]) -> list[JobSpec]:
    specs: list[JobSpec] = []
    for dataset in order:
        if dataset.startswith(CANDLES_PREFIX):
            specs.append(JobSpec(inst.inst_id, dataset))
        elif dataset == FUNDING and inst.inst_type == "SWAP":
            specs.append(JobSpec(inst.inst_id, dataset))
        elif (
            dataset == OPEN_INTEREST
            and inst.inst_type in ("SWAP", "FUTURES")
            and inst.asset_class == "crypto"
        ):
            specs.append(JobSpec(currency_of(inst), dataset))
    return specs


def target_from(dataset: str, now: datetime) -> datetime:
    days = depth_days().get(dataset)
    return full_history_floor() if days is None else now - timedelta(days=int(days))


@dataclass(frozen=True)
class EnsureResult:
    env: str
    instruments: int
    created: int
    resumed: int
    specs: int

    @property
    def queued(self) -> int:
        return self.created + self.resumed

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "instruments": self.instruments,
            "jobs": self.queued,
            "created": self.created,
            "resumed": self.resumed,
        }


def eligible_instruments(engine: Engine, env: str, inst_id: str | None = None) -> list[Any]:
    enabled = set(runner_cfg()["enabled_markets"])
    stmt = select(Instrument).where(Instrument.env == env, Instrument.state == "live")
    if inst_id is not None:
        stmt = stmt.where(Instrument.inst_id == inst_id)
    with engine.connect() as conn:
        rows = list(conn.execute(stmt.order_by(Instrument.inst_id)).all())
    return [r for r in rows if market_of(r.inst_type, r.asset_class) in enabled]


def ensure_jobs(
    engine: Engine,
    env: str,
    *,
    inst_id: str | None = None,
    resume: bool = True,
    now: datetime | None = None,
) -> EnsureResult:
    """Create the missing jobs for the env (or one instrument) and, with ``resume``, re-queue the
    paused and failed ones. Safe to run any number of times."""
    clock = now or datetime.now(UTC)
    order: list[str] = list(runner_cfg()["dataset_order"])
    instruments = eligible_instruments(engine, env, inst_id)
    specs: dict[tuple[str, str], JobSpec] = {}
    for inst in instruments:
        for spec in datasets_for(inst, order):
            specs[(spec.inst_id, spec.dataset)] = spec
    created = resumed = 0
    if not specs:
        return EnsureResult(env, len(instruments), 0, 0, 0)
    rows = [
        {
            "id": uuid.uuid4(),
            "env": env,
            "inst_id": s.inst_id,
            "dataset": s.dataset,
            "target_from": target_from(s.dataset, clock),
            "cursor_ts": None,
            "rows_written": 0,
            "status": PENDING,
            "last_error": None,
            "created_at": clock,
            "updated_at": clock,
        }
        for s in sorted(specs.values(), key=lambda s: (s.inst_id, s.dataset))
    ]
    with engine.begin() as conn:
        for start in range(0, len(rows), 1000):  # 11 columns x 1000 rows, far below 65535 params
            stmt = (
                insert(BackfillJob)
                .values(rows[start : start + 1000])
                .on_conflict_do_nothing(index_elements=["env", "inst_id", "dataset"])
                .returning(BackfillJob.id)
            )
            created += len(conn.execute(stmt).all())
        if resume:
            keys = {s.inst_id for s in specs.values()}
            result = conn.execute(
                update(BackfillJob)
                .where(
                    BackfillJob.env == env,
                    BackfillJob.inst_id.in_(keys),
                    BackfillJob.status.in_((PAUSED, FAILED)),
                )
                .values(status=PENDING, last_error=None, updated_at=clock)
            )
            resumed = result.rowcount or 0
    return EnsureResult(env, len(instruments), created, resumed, len(specs))


def pause_jobs(engine: Engine, env: str, *, inst_id: str | None = None, now: datetime | None = None) -> int:
    """Mark pending/running jobs ``paused``; a worker stops before writing its next page."""
    clock = now or datetime.now(UTC)
    stmt = update(BackfillJob).where(BackfillJob.env == env, BackfillJob.status.in_((PENDING, RUNNING)))
    if inst_id is not None:
        stmt = stmt.where(BackfillJob.inst_id == inst_id)
    with engine.begin() as conn:
        return conn.execute(stmt.values(status=PAUSED, updated_at=clock)).rowcount or 0
