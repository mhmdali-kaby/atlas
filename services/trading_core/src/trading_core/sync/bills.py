"""Bills, deposits and withdrawals sync (ATL-11, brief §13, decision #235).

Every ``sync.bills_interval_seconds`` for the active env, if it has a verified OKX connection
(same guard as the account sync, decision #237):

1. Page ``account/bills`` (last 7 days) with ``after`` = the last bill id of the previous page until
   OKX returns a short or empty page. On the first run (no successful ``bills`` sync yet) also page
   ``account/bills-archive`` (last 3 months) the same way.
2. Page ``asset/deposit-history`` and ``asset/withdrawal-history`` with ``after`` = the oldest ``ts``
   of the previous page. These are read-only history endpoints; Atlas has no withdrawal code.
3. In one transaction: insert new bills (upsert key ``(env, bill_id)``; bills never change, so known
   ones are skipped), upsert deposits/withdrawals on ``(env, kind, external_id)`` (their ``state``
   moves from pending to success, stored raw: "2" = success), and mark ``sync_status`` (stream
   ``bills``) successful and reconciled.

Any failure writes nothing but ``sync_status.last_error``. Which bill fields mean fee, funding and
realised PnL is documented in ``trading_core.sync.bill_types``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from atlas_shared.db import Bill as BillRow
from atlas_shared.db import DepositWithdrawal, SyncStatus
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert

from trading_core.okx import OkxClient
from trading_core.okx.models import Bill, DepositRecord, WithdrawalRecord
from trading_core.sync.account import (
    NO_CONNECTION,
    AppKeyMissing,
    ClientFactory,
    _default_client,
    verified_connection,
)

logger = logging.getLogger("trading_core.sync.bills")

STREAM = "bills"
DEPOSIT = "deposit"
WITHDRAWAL = "withdrawal"
PAGE_LIMIT = 100  # OKX's maximum page size for bills and deposit/withdrawal history
INSERT_CHUNK = 1000  # rows per INSERT, well under Postgres's 65535 bind-parameter limit
ZERO = Decimal(0)

type Page[T] = Callable[[str | None], Awaitable[list[T]]]


@dataclass(frozen=True)
class BillsSnapshot:
    bills: list[Bill]
    deposits: list[DepositRecord]
    withdrawals: list[WithdrawalRecord]
    archive: bool


@dataclass(frozen=True)
class BillsSyncResult:
    env: str
    synced_at: datetime
    archive: bool
    bills_fetched: int
    bills_new: int
    deposits: int
    withdrawals: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "synced_at": self.synced_at.isoformat().replace("+00:00", "Z"),
            "archive": self.archive,
            "bills_fetched": self.bills_fetched,
            "bills_new": self.bills_new,
            "deposits": self.deposits,
            "withdrawals": self.withdrawals,
        }


async def paginate[T](fetch: Page[T], cursor_of: Callable[[T], str], limit: int = PAGE_LIMIT) -> list[T]:
    """Call ``fetch(after)`` until a page is short or empty, or the cursor stops moving."""
    rows: list[T] = []
    after: str | None = None
    while True:
        page = await fetch(after)
        rows.extend(page)
        if len(page) < limit:
            return rows
        nxt = cursor_of(page[-1])
        if nxt == after:
            logger.warning("pagination cursor did not advance; stopping", extra={"after": after})
            return rows
        after = nxt


def _ms(dt: datetime) -> str:
    return str(int(dt.timestamp() * 1000))


async def fetch_bills(okx: OkxClient, *, archive: bool, limit: int = PAGE_LIMIT) -> BillsSnapshot:
    """All reads for one run. Raises on the first failure, so nothing partial is ever written."""
    bills = await paginate(lambda a: okx.get_bills(after=a, limit=limit), lambda b: b.bill_id, limit)
    if archive:
        bills += await paginate(
            lambda a: okx.get_bills_archive(after=a, limit=limit), lambda b: b.bill_id, limit
        )

    async def deposits(after: str | None) -> list[DepositRecord]:
        return await okx.get_deposit_history(after=after, limit=limit)

    async def withdrawals(after: str | None) -> list[WithdrawalRecord]:
        return await okx.get_withdrawal_history(after=after, limit=limit)

    # OKX's ``after`` returns records strictly earlier than the given ts, so on a full page a second
    # record sharing the oldest record's exact millisecond would be skipped. Cash flows are rare
    # (a few per day at most), so Phase 0 accepts that; the next 5-minute run cannot recover it either.
    dep = await paginate(deposits, lambda r: _ms(r.ts), limit)
    wd = await paginate(withdrawals, lambda r: _ms(r.ts), limit)
    return BillsSnapshot(bills=bills, deposits=dep, withdrawals=wd, archive=archive)


def _chunks(rows: Sequence[dict[str, Any]]) -> Iterable[Sequence[dict[str, Any]]]:
    for i in range(0, len(rows), INSERT_CHUNK):
        yield rows[i : i + INSERT_CHUNK]


def bill_row(env: str, b: Bill, now: datetime) -> dict[str, Any]:
    return {
        "env": env,
        "bill_id": b.bill_id,
        "inst_id": b.inst_id,
        "ccy": b.ccy,
        "type": b.type,
        "sub_type": b.sub_type,
        "bal_chg": b.bal_chg if b.bal_chg is not None else ZERO,
        "pnl": b.pnl,
        "fee": b.fee,
        "ts": b.ts,
        "created_at": now,
        "updated_at": now,
    }


def flow_row(env: str, kind: str, r: DepositRecord | WithdrawalRecord, now: datetime) -> dict[str, Any]:
    external_id = r.dep_id if isinstance(r, DepositRecord) else r.wd_id
    return {
        "env": env,
        "kind": kind,
        "external_id": external_id,
        "ccy": r.ccy,
        "amount": r.amt if r.amt is not None else ZERO,
        "state": r.state,  # raw OKX code; "2" = success for both kinds
        "tx_id": r.tx_id,
        "ts": r.ts,
        "created_at": now,
        "updated_at": now,
    }


def write_bills(engine: Engine, env: str, snap: BillsSnapshot, now: datetime) -> BillsSyncResult:
    """Store one run (bills, deposits, withdrawals, sync_status) in a single transaction."""
    unique = {b.bill_id: b for b in snap.bills}  # bills and bills-archive overlap
    bills = [bill_row(env, b, now) for b in unique.values()]
    flows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, records in ((DEPOSIT, snap.deposits), (WITHDRAWAL, snap.withdrawals)):
        for r in records:
            row = flow_row(env, kind, r, now)
            flows_by_key[(kind, row["external_id"])] = row
    flows = list(flows_by_key.values())

    new = 0
    with engine.begin() as conn:
        for chunk in _chunks(bills):
            bill_stmt = (
                insert(BillRow)
                .values(list(chunk))
                .on_conflict_do_nothing(index_elements=[BillRow.env, BillRow.bill_id])
                .returning(BillRow.bill_id)
            )
            new += len(conn.execute(bill_stmt).all())  # only rows actually inserted are returned
        for chunk in _chunks(flows):
            flow_stmt = insert(DepositWithdrawal).values(list(chunk))
            conn.execute(
                flow_stmt.on_conflict_do_update(
                    index_elements=[
                        DepositWithdrawal.env,
                        DepositWithdrawal.kind,
                        DepositWithdrawal.external_id,
                    ],
                    set_={
                        "ccy": flow_stmt.excluded.ccy,
                        "amount": flow_stmt.excluded.amount,
                        "state": flow_stmt.excluded.state,
                        "tx_id": flow_stmt.excluded.tx_id,
                        "ts": flow_stmt.excluded.ts,
                        "updated_at": now,
                    },
                )
            )
        _write_status(conn, env, now, error=None)

    return BillsSyncResult(
        env=env,
        synced_at=now,
        archive=snap.archive,
        bills_fetched=len(bills),
        bills_new=new,
        deposits=len(snap.deposits),
        withdrawals=len(snap.withdrawals),
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
        values |= {"last_success_at": now, "reconciled_at": now}
        update_set |= {"last_success_at": now, "reconciled_at": now}
    stmt = insert(SyncStatus).values(values)
    conn.execute(
        stmt.on_conflict_do_update(index_elements=[SyncStatus.env, SyncStatus.stream], set_=update_set)
    )


def record_failure(engine: Engine, env: str, error: str, now: datetime | None = None) -> None:
    """Store the error; last_success_at and reconciled_at keep the previous good run."""
    with engine.begin() as conn:
        _write_status(conn, env, now or datetime.now(UTC), error=error[:2000])


def has_synced(engine: Engine, env: str) -> bool:
    """True once a bills sync has succeeded for ``env`` (so the 3-month archive is not paged again)."""
    with engine.connect() as conn:
        last = conn.scalar(
            select(SyncStatus.last_success_at).where(SyncStatus.env == env, SyncStatus.stream == STREAM)
        )
    return last is not None


async def sync_bills(
    env: str, *, okx: OkxClient, engine: Engine, now: Callable[[], datetime] | None = None
) -> BillsSyncResult:
    """Fetch and store bills, deposits and withdrawals for ``env``. On failure, record it and re-raise."""
    clock = now or (lambda: datetime.now(UTC))
    try:
        archive = not await asyncio.to_thread(has_synced, engine, env)
        snap = await fetch_bills(okx, archive=archive)
        result = await asyncio.to_thread(write_bills, engine, env, snap, clock())
    except Exception as exc:
        await _record(engine, env, exc)
        raise
    logger.info("bills synced", extra=result.as_dict())
    return result


async def _record(engine: Engine, env: str, exc: BaseException) -> None:
    try:
        await asyncio.to_thread(record_failure, engine, env, f"{type(exc).__name__}: {exc}")
    except Exception:
        logger.exception("could not record bills sync failure")


def bills_job(
    env: str,
    engine: Engine,
    app_key: str,
    client_factory: ClientFactory | None = None,
    now: Callable[[], datetime] | None = None,
) -> Callable[[], Any]:
    """The scheduler job. Looks the verified connection up every run, like the account sync."""
    factory = client_factory or _default_client

    async def run() -> dict[str, Any]:
        row = await asyncio.to_thread(verified_connection, engine, env)
        if row is None:
            logger.info("bills sync skipped: no verified connection", extra={"env": env})
            return {"env": env, "skipped": True, "reason": NO_CONNECTION}
        try:
            if not app_key:
                raise AppKeyMissing("APP_KEY is not set; cannot decrypt the connection")
            client = factory(row, app_key)
        except Exception as exc:
            await _record(engine, env, exc)
            raise
        async with client as okx:
            result = await sync_bills(env, okx=okx, engine=engine, now=now)
        return {**result.as_dict(), "skipped": False}

    return run
