"""SQLAlchemy 2 models for the tables created by backend/database/migrations (decision #233).

Laravel owns the schema; these models mirror it column for column, and
shared/tests/test_schema_reflection.py fails when they drift. Money, prices and sizes are
Numeric(38, 18) mapped to Decimal. Columns ending in ``_usd`` are USD; every other amount is in
the row's own ``ccy`` (or contracts/coins for ``pos``, as OKX reports it).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _num() -> Numeric[Decimal]:
    return Numeric(38, 18, asdecimal=True)


def _tz() -> TIMESTAMP:
    """timestamptz(6): Laravel timestampTz(..., 6), always UTC."""
    return TIMESTAMP(timezone=True, precision=6)


class Base(DeclarativeBase):
    """Declarative base; Laravel enum columns are varchar(255) with a CHECK constraint."""


class _Row:
    """Columns every Atlas trading table has: UUID id, env, created_at, updated_at."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    env: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)


class Instrument(_Row, Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("env", "inst_id"),)

    inst_id: Mapped[str] = mapped_column(String(64))
    inst_type: Mapped[str] = mapped_column(String(16))
    inst_family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inst_category: Mapped[str | None] = mapped_column(String(16), nullable=True)
    base_ccy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quote_ccy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    settle_ccy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ct_val: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    ct_val_ccy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ct_mult: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    lot_sz: Mapped[Decimal] = mapped_column(_num())
    min_sz: Mapped[Decimal] = mapped_column(_num())
    tick_sz: Mapped[Decimal] = mapped_column(_num())
    max_lever: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    state: Mapped[str] = mapped_column(String(16))
    list_time: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    exp_time: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    asset_class: Mapped[str] = mapped_column(String(255), default="other")
    last_seen_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)


class BalanceSnapshot(_Row, Base):
    __tablename__ = "balances_snapshots"
    __table_args__ = (UniqueConstraint("env", "account", "ccy", "taken_at"),)

    ccy: Mapped[str] = mapped_column(String(32))
    account: Mapped[str] = mapped_column(String(255))  # trading | funding
    eq: Mapped[Decimal] = mapped_column(_num())
    avail_bal: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    frozen_bal: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    eq_usd: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    upl: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    taken_at: Mapped[datetime] = mapped_column(_tz())


class PositionSnapshot(_Row, Base):
    __tablename__ = "positions_snapshots"
    __table_args__ = (UniqueConstraint("env", "inst_id", "pos_side", "taken_at"),)

    inst_id: Mapped[str] = mapped_column(String(64))
    pos_side: Mapped[str] = mapped_column(String(8))
    pos: Mapped[Decimal] = mapped_column(_num())
    avg_px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    mark_px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    liq_px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    lever: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    upl: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    upl_ratio: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    margin: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    mgn_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    c_time: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    taken_at: Mapped[datetime] = mapped_column(_tz())


class Bill(_Row, Base):
    __tablename__ = "bills"
    __table_args__ = (UniqueConstraint("env", "bill_id"),)

    bill_id: Mapped[str] = mapped_column(String(64))
    inst_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ccy: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(16))
    sub_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bal_chg: Mapped[Decimal] = mapped_column(_num())
    pnl: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    fee: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    ts: Mapped[datetime] = mapped_column(_tz())


class DepositWithdrawal(_Row, Base):
    __tablename__ = "deposits_withdrawals"
    __table_args__ = (UniqueConstraint("env", "kind", "external_id"),)

    kind: Mapped[str] = mapped_column(String(255))  # deposit | withdrawal
    external_id: Mapped[str] = mapped_column(String(64))  # OKX depId / wdId
    ccy: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(_num())
    state: Mapped[str] = mapped_column(String(16))
    tx_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ts: Mapped[datetime] = mapped_column(_tz())


class SyncStatus(_Row, Base):
    __tablename__ = "sync_status"
    __table_args__ = (UniqueConstraint("env", "stream"),)

    stream: Mapped[str] = mapped_column(String(32))
    last_success_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reconciled_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)


class EquityPoint(_Row, Base):
    """TimescaleDB hypertable on ts; primary key is (id, ts), upsert key is (env, ts)."""

    __tablename__ = "equity_curve"
    __table_args__ = (UniqueConstraint("env", "ts"),)

    ts: Mapped[datetime] = mapped_column(_tz(), primary_key=True)
    total_eq_usd: Mapped[Decimal] = mapped_column(_num())
    upl_usd: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
