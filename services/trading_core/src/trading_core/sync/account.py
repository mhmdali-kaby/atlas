"""Account sync (ATL-10, brief §13, decision #235).

Every ``sync.account_interval_seconds`` for the active env, if that env has a verified OKX connection
(``last_verified_at`` set; live withdraw-enabled keys never are, demo ones are with a warning, ATL-30):

1. Fetch ``account/balance`` (trading account), ``asset/balances`` (funding account) and
   ``account/positions``; and, only when the funding account holds a non-stablecoin, SPOT
   ``market/tickers`` to value it in USD.
2. In one transaction write one ``balances_snapshots`` row per currency per account, one
   ``positions_snapshots`` row per position (all with the same ``taken_at``), upsert the
   ``equity_curve`` point for the current minute, and mark ``sync_status`` (stream ``account``)
   successful and reconciled.

Any failure (OKX error, network, decrypt) writes nothing but ``sync_status.last_error``; the previous
snapshot stays the current one. Trading-account ``eq_usd`` is OKX's own ``eqUsd``, never recomputed.
Funding-account ``eq_usd`` is computed here: USDT/USDC 1:1 with USD, other coins at the SPOT ticker
``<CCY>-USDT`` (then ``<CCY>-USDC``) last price; a coin with no ticker gets ``eq_usd`` NULL and is
left out of the equity total (logged as a warning).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from atlas_shared.db import BalanceSnapshot, Connection, EquityPoint, PositionSnapshot, SyncStatus
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from trading_core.okx import OkxClient, client_for_connection
from trading_core.okx.errors import OkxResponseError
from trading_core.okx.models import AccountBalance, AssetBalance, Position, Ticker

logger = logging.getLogger("trading_core.sync.account")

STREAM = "account"
EXCHANGE = "okx"
TRADING = "trading"
FUNDING = "funding"
NO_CONNECTION = "no_verified_connection"

_CFG: Mapping[str, Any] = DEFAULTS["account_sync"]
ZERO = Decimal(0)

ClientFactory = Callable[[Connection, str], OkxClient]


class AppKeyMissing(RuntimeError):
    """APP_KEY is not configured, so the stored connection cannot be decrypted."""


@dataclass(frozen=True)
class AccountSnapshot:
    """Everything one run fetched from OKX, before anything is written."""

    balance: AccountBalance
    funding: list[AssetBalance]
    positions: list[Position]
    tickers: dict[str, Decimal] = field(default_factory=dict)  # instId -> last


@dataclass(frozen=True)
class AccountSyncResult:
    env: str
    taken_at: datetime
    equity_ts: datetime
    trading_balances: int
    funding_balances: int
    positions: int
    total_eq_usd: Decimal
    trading_eq_usd: Decimal
    funding_eq_usd: Decimal
    upl_usd: Decimal
    unpriced: list[str]

    def as_dict(self) -> dict[str, Any]:
        def iso(dt: datetime) -> str:
            return dt.isoformat().replace("+00:00", "Z")

        return {
            "env": self.env,
            "taken_at": iso(self.taken_at),
            "equity_ts": iso(self.equity_ts),
            "trading_balances": self.trading_balances,
            "funding_balances": self.funding_balances,
            "positions": self.positions,
            "total_eq_usd": str(self.total_eq_usd),
            "trading_eq_usd": str(self.trading_eq_usd),
            "funding_eq_usd": str(self.funding_eq_usd),
            "upl_usd": str(self.upl_usd),
            "unpriced": self.unpriced,
        }


def _stablecoins() -> frozenset[str]:
    return frozenset(str(c).upper() for c in _CFG["usd_stablecoins"])


def _quotes() -> list[str]:
    return [str(q).upper() for q in _CFG["price_quotes"]]


def needs_prices(funding: Sequence[AssetBalance]) -> bool:
    stable = _stablecoins()
    return any(b.ccy.upper() not in stable and (b.bal or ZERO) != ZERO for b in funding)


def funding_usd(bal: AssetBalance, tickers: Mapping[str, Decimal]) -> Decimal | None:
    """USD value of one funding-account coin, or None when no ticker prices it."""
    amount = bal.bal or ZERO
    ccy = bal.ccy.upper()
    if ccy in _stablecoins():
        return amount
    if amount == ZERO:
        return ZERO
    for quote in _quotes():
        last = tickers.get(f"{ccy}-{quote}")
        if last is not None:
            # The quote is itself a stablecoin, valued 1:1 in Phase 0.
            return amount * last
    return None


def settle_ccy(position: Position) -> str:
    if position.ccy:
        return position.ccy.upper()
    parts = position.inst_id.split("-")
    return parts[1].upper() if len(parts) > 1 else ""


def upl_usd(balance: AccountBalance, positions: Sequence[Position]) -> Decimal:
    """OKX's account-level ``upl`` (USD) when it reports one; otherwise Σ ``upl`` of USD-settled
    positions (OKX leaves the account-level field empty in futures mode); 0 with no positions."""
    if balance.upl is not None:
        return balance.upl
    usd = _stablecoins() | {"USD"}
    return sum((p.upl or ZERO for p in positions if settle_ccy(p) in usd), ZERO)


def minute(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(second=0, microsecond=0)


async def fetch_account(okx: OkxClient) -> AccountSnapshot:
    """All reads for one run. Raises on the first failure, so nothing partial is ever written."""
    balance = await okx.get_balance()
    if balance.total_eq is None:
        raise OkxResponseError("account/balance returned no totalEq")
    funding = await okx.get_asset_balances()
    positions = await okx.get_positions()
    tickers: dict[str, Decimal] = {}
    if needs_prices(funding):
        rows: list[Ticker] = await okx.get_tickers("SPOT")
        tickers = {t.inst_id.upper(): t.last for t in rows if t.last is not None}
    return AccountSnapshot(balance=balance, funding=funding, positions=positions, tickers=tickers)


def write_snapshot(engine: Engine, env: str, snap: AccountSnapshot, now: datetime) -> AccountSyncResult:
    """Write one tick (balances, positions, equity point, sync_status) in a single transaction."""
    stamp = {"env": env, "taken_at": now, "created_at": now, "updated_at": now}

    trading_rows = [
        {
            **stamp,
            "ccy": d.ccy,
            "account": TRADING,
            "eq": d.eq if d.eq is not None else ZERO,
            "avail_bal": d.avail_bal,
            "frozen_bal": d.frozen_bal,
            "eq_usd": d.eq_usd,  # OKX's own figure, never recomputed
            "upl": d.upl,
        }
        for d in snap.balance.details
    ]

    funding_rows: list[dict[str, Any]] = []
    funding_total = ZERO
    unpriced: list[str] = []
    for b in snap.funding:
        value = funding_usd(b, snap.tickers)
        if value is None:
            unpriced.append(b.ccy)
        else:
            funding_total += value
        funding_rows.append(
            {
                **stamp,
                "ccy": b.ccy,
                "account": FUNDING,
                "eq": b.bal if b.bal is not None else ZERO,
                "avail_bal": b.avail_bal,
                "frozen_bal": b.frozen_bal,
                "eq_usd": value,
                "upl": None,
            }
        )
    if unpriced:
        logger.warning("funding coins without a USD ticker left out of equity", extra={"ccys": unpriced})

    position_rows = [
        {
            **stamp,
            "inst_id": p.inst_id,
            "pos_side": p.pos_side or "net",
            "pos": p.pos if p.pos is not None else ZERO,
            "avg_px": p.avg_px,
            "mark_px": p.mark_px,
            "liq_px": p.liq_px,
            "lever": p.lever,
            "upl": p.upl,
            "upl_ratio": p.upl_ratio,
            "margin": p.margin,
            "mgn_mode": p.mgn_mode,
            "c_time": p.c_time,
        }
        for p in snap.positions
    ]

    trading_total = snap.balance.total_eq if snap.balance.total_eq is not None else ZERO
    total = trading_total + funding_total
    upl = upl_usd(snap.balance, snap.positions)
    equity_ts = minute(now)

    with engine.begin() as conn:
        balance_rows = trading_rows + funding_rows
        if balance_rows:
            conn.execute(insert(BalanceSnapshot).values(balance_rows))
        if position_rows:
            conn.execute(insert(PositionSnapshot).values(position_rows))

        point = insert(EquityPoint).values(
            env=env, ts=equity_ts, total_eq_usd=total, upl_usd=upl, created_at=now, updated_at=now
        )
        conn.execute(
            point.on_conflict_do_update(
                index_elements=[EquityPoint.env, EquityPoint.ts],
                set_={"total_eq_usd": total, "upl_usd": upl, "updated_at": now},
            )
        )
        _write_status(conn, env, now, error=None)

    return AccountSyncResult(
        env=env,
        taken_at=now,
        equity_ts=equity_ts,
        trading_balances=len(trading_rows),
        funding_balances=len(funding_rows),
        positions=len(position_rows),
        total_eq_usd=total,
        trading_eq_usd=trading_total,
        funding_eq_usd=funding_total,
        upl_usd=upl,
        unpriced=unpriced,
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


async def sync_account(
    env: str, *, okx: OkxClient, engine: Engine, now: Callable[[], datetime] | None = None
) -> AccountSyncResult:
    """Fetch and store one account tick for ``env``. On failure, record it and re-raise."""
    clock = now or (lambda: datetime.now(UTC))
    try:
        snap = await fetch_account(okx)
        result = await asyncio.to_thread(write_snapshot, engine, env, snap, clock())
    except Exception as exc:
        await _record(engine, env, exc)
        raise
    logger.info("account synced", extra=result.as_dict())
    return result


async def _record(engine: Engine, env: str, exc: BaseException) -> None:
    try:
        await asyncio.to_thread(record_failure, engine, env, f"{type(exc).__name__}: {exc}")
    except Exception:
        logger.exception("could not record account sync failure")


def verified_connection(engine: Engine, env: str) -> Connection | None:
    with Session(engine, expire_on_commit=False) as session:
        return session.scalars(
            select(Connection).where(
                Connection.exchange == EXCHANGE,
                Connection.env == env,
                Connection.last_verified_at.is_not(None),
            )
        ).first()


def _default_client(row: Connection, app_key: str) -> OkxClient:
    return client_for_connection(row, app_key)


def account_job(
    env: str,
    engine: Engine,
    app_key: str,
    client_factory: ClientFactory | None = None,
    now: Callable[[], datetime] | None = None,
) -> Callable[[], Any]:
    """The scheduler job. Looks the connection up every run, so a newly verified key starts syncing
    on the next tick and a cleared one stops."""
    factory = client_factory or _default_client

    async def run() -> dict[str, Any]:
        row = await asyncio.to_thread(verified_connection, engine, env)
        if row is None:
            logger.info("account sync skipped: no verified connection", extra={"env": env})
            return {"env": env, "skipped": True, "reason": NO_CONNECTION}
        try:
            if not app_key:
                raise AppKeyMissing("APP_KEY is not set; cannot decrypt the connection")
            client = factory(row, app_key)
        except Exception as exc:
            await _record(engine, env, exc)
            raise
        async with client as okx:
            result = await sync_account(env, okx=okx, engine=engine, now=now)
        return {**result.as_dict(), "skipped": False}

    return run
