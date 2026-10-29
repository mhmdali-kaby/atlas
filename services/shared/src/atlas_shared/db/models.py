"""SQLAlchemy 2 models for the tables created by backend/database/migrations (decision #233).

Laravel owns the schema; these models mirror it column for column, and
shared/tests/test_schema_reflection.py fails when they drift. Money, prices and sizes are
Numeric(38, 18) mapped to Decimal. Columns ending in ``_usd`` are USD; every other amount is in
the row's own ``ccy`` (or contracts/coins for ``pos``, as OKX reports it).
"""

import uuid
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    Dialect,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType


def _num() -> Numeric[Decimal]:
    return Numeric(38, 18, asdecimal=True)


def _tz() -> TIMESTAMP:
    """timestamptz(6): Laravel timestampTz(..., 6), always UTC."""
    return TIMESTAMP(timezone=True, precision=6)


class Vector(UserDefinedType[list[float]]):
    """pgvector ``vector(n)``. Binds a sequence of floats as the ``[a,b,…]`` text literal and reads
    it back as ``list[float]``. Embeddings are similarity features, not money, so floats are right."""

    cache_ok = True

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        return "VECTOR" if self.dim is None else f"VECTOR({self.dim})"

    def bind_processor(self, dialect: Dialect) -> Callable[[Sequence[float] | None], str | None]:
        dim = self.dim

        def process(value: Sequence[float] | None) -> str | None:
            if value is None:
                return None
            if dim is not None and len(value) != dim:
                raise ValueError(f"expected {dim} dimensions, got {len(value)}")
            return "[" + ",".join(repr(float(v)) for v in value) + "]"

        return process

    def result_processor(
        self, dialect: Dialect, coltype: object
    ) -> Callable[[str | None], list[float] | None]:
        def process(value: str | None) -> list[float] | None:
            if value is None:
                return None
            inner = value.strip()[1:-1]
            return [float(v) for v in inner.split(",")] if inner else []

        return process


# Lets inspect() reflect `vector(384)` columns as Vector(384) (schema drift test).
ischema_names["vector"] = Vector


def _text_array() -> ARRAY[str]:
    return ARRAY(Text)


class Base(DeclarativeBase):
    """Declarative base; Laravel enum columns are varchar(255) with a CHECK constraint."""


class _Row:
    """Columns every Atlas trading table has: UUID id, env, created_at, updated_at."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    env: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)


class Connection(_Row, Base):
    """Exchange API connection (ATL-4). api_key/api_secret/passphrase are Laravel ``encrypted``-cast
    ciphertext: decrypt with atlas_shared.laravel_crypt, never log them. trading_core writes only the
    verification columns (Test connection, ATL-8); uid_last4 is the only part of the UID ever stored."""

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("exchange", "env"),)

    exchange: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(100))
    api_key: Mapped[str] = mapped_column(Text)
    api_secret: Mapped[str] = mapped_column(Text)
    passphrase: Mapped[str] = mapped_column(Text)
    permissions: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    account_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    uid_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    # mgn_mode is in the key: net mode allows a cross and an isolated position on one instrument.
    __table_args__ = (
        UniqueConstraint(
            "env", "inst_id", "pos_side", "mgn_mode", "taken_at", postgresql_nulls_not_distinct=True
        ),
    )

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


# --- Market history (ATL-44): TimescaleDB hypertables on ts, keyed by their natural key ---------
# No surrogate id: the primary key is the upsert conflict target, e.g.
# insert(Candle).values(rows).on_conflict_do_update(index_elements=Candle.__table__.primary_key.columns, ...)


class Candle(Base):
    """OHLCV bar. bar is 1m|5m|15m|1H|4H|1D; confirmed is OKX's "bar closed" flag."""

    __tablename__ = "candles"

    env: Mapped[str] = mapped_column(String(255), primary_key=True)
    inst_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bar: Mapped[str] = mapped_column(String(255), primary_key=True)
    ts: Mapped[datetime] = mapped_column(_tz(), primary_key=True)
    o: Mapped[Decimal] = mapped_column(_num())
    h: Mapped[Decimal] = mapped_column(_num())
    l: Mapped[Decimal] = mapped_column(_num())  # noqa: E741 - OHLC column name
    c: Mapped[Decimal] = mapped_column(_num())
    vol: Mapped[Decimal] = mapped_column(_num())
    vol_ccy: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    vol_ccy_quote: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class FundingRate(Base):
    __tablename__ = "funding_rates"

    env: Mapped[str] = mapped_column(String(255), primary_key=True)
    inst_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(_tz(), primary_key=True)
    funding_rate: Mapped[Decimal] = mapped_column(_num())
    realized_rate: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)


class OpenInterest(Base):
    """inst_id is an OKX instId, or a bare ccy (no '-') for currency-level rubik series."""

    __tablename__ = "open_interest"

    env: Mapped[str] = mapped_column(String(255), primary_key=True)
    inst_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(_tz(), primary_key=True)
    oi: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    oi_ccy: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    oi_usd: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)


class LongShortRatio(Base):
    """period is OKX rubik's 5m|1H|1D."""

    __tablename__ = "long_short_ratio"

    env: Mapped[str] = mapped_column(String(255), primary_key=True)
    ccy: Mapped[str] = mapped_column(String(32), primary_key=True)
    period: Mapped[str] = mapped_column(String(255), primary_key=True)
    ts: Mapped[datetime] = mapped_column(_tz(), primary_key=True)
    ratio: Mapped[Decimal] = mapped_column(_num())


class TakerVolume(Base):
    __tablename__ = "taker_volume"

    env: Mapped[str] = mapped_column(String(255), primary_key=True)
    ccy: Mapped[str] = mapped_column(String(32), primary_key=True)
    period: Mapped[str] = mapped_column(String(255), primary_key=True)
    ts: Mapped[datetime] = mapped_column(_tz(), primary_key=True)
    buy_vol: Mapped[Decimal] = mapped_column(_num())
    sell_vol: Mapped[Decimal] = mapped_column(_num())


class BackfillJob(_Row, Base):
    """One resumable backfill per (env, inst_id, dataset). dataset is candles:<bar> | funding |
    open_interest | long_short_ratio | taker_volume; status is pending|running|done|failed|paused."""

    __tablename__ = "backfill_jobs"
    __table_args__ = (UniqueConstraint("env", "inst_id", "dataset"),)

    inst_id: Mapped[str] = mapped_column(String(64))
    dataset: Mapped[str] = mapped_column(String(255))
    target_from: Mapped[datetime] = mapped_column(_tz())
    cursor_ts: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    rows_written: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(255), default="pending")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DataQualityIssue(_Row, Base):
    """kind is gap|duplicate|zero_volume|outlier; repaired_at stays null until repaired from REST."""

    __tablename__ = "data_quality_issues"
    __table_args__ = (UniqueConstraint("env", "inst_id", "dataset", "kind", "from_ts"),)

    inst_id: Mapped[str] = mapped_column(String(64))
    dataset: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(255))
    from_ts: Mapped[datetime] = mapped_column(_tz())
    to_ts: Mapped[datetime] = mapped_column(_tz())
    detail: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(_tz())
    repaired_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)


class _Stamped:
    """UUID id plus Laravel timestampsTz(6), for tables without an env column."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)


class NewsSource(_Stamped, Base):
    """One ingest source (ATL-57, decision #243). kind is the transport (rss|api|calendar|earnings|sentiment);
    category is the topic (exchange|crypto_news|markets_news|calendar|earnings|sentiment).
    api_key is Laravel ``encrypted``-cast ciphertext: decrypt with atlas_shared.laravel_crypt and
    never log it. Ingest writes only the health columns (failures, disabled_until, last_*)."""

    __tablename__ = "news_sources"
    __table_args__ = (UniqueConstraint("key"),)

    key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    poll_seconds: Mapped[int] = mapped_column(Integer)
    requires_key: Mapped[bool] = mapped_column(Boolean, default=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    disabled_until: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    last_item_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NewsCluster(_Stamped, Base):
    """One story told by many sources; first_seen is the earliest item's published_at."""

    __tablename__ = "news_clusters"

    headline: Mapped[str] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(_tz())
    last_seen: Mapped[datetime] = mapped_column(_tz())
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    max_importance: Mapped[int] = mapped_column(SmallInteger, default=1)
    instruments: Mapped[list[str]] = mapped_column(_text_array(), default=list)
    alerted_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    """When ingest published this story's news.alert (ATL-65); NULL = never alerted."""


class NewsItem(_Stamped, Base):
    """One fetched article (brief §5). Deduplicated on canonical_url_hash, a generated
    sha256(canonical_url): write with ``ON CONFLICT (canonical_url_hash)``, never set it.
    event_type is one of the brief §7.2 types; importance 1-5 and sentiment -1..1 are CHECKed."""

    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("canonical_url_hash"),)

    source: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    canonical_url_hash: Mapped[bytes | None] = mapped_column(
        BYTEA, Computed("atlas_sha256_text(canonical_url)", persisted=True), nullable=True
    )
    title: Mapped[str] = mapped_column(Text)
    title_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(_tz())
    fetched_at: Mapped[datetime] = mapped_column(_tz())
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    entities: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    instruments: Mapped[list[str]] = mapped_column(_text_array(), default=list)
    asset_classes: Mapped[list[str]] = mapped_column(_text_array(), default=list)
    event_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sentiment: Mapped[Decimal | None] = mapped_column(Numeric(6, 4, asdecimal=True), nullable=True)
    importance: Mapped[int] = mapped_column(SmallInteger, default=1)
    novelty: Mapped[Decimal | None] = mapped_column(Numeric(6, 4, asdecimal=True), nullable=True)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("news_clusters.id", ondelete="SET NULL"), nullable=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    raw: Mapped[Any | None] = mapped_column(JSONB, nullable=True)


class EconomicEvent(_Stamped, Base):
    """Scheduled macro release. actual/forecast/previous are kept as published ("3.2%", "245K")."""

    __tablename__ = "economic_events"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    source: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(191))
    country: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))
    importance: Mapped[int] = mapped_column(SmallInteger, default=1)
    scheduled_at: Mapped[datetime] = mapped_column(_tz())
    actual: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forecast: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous: Mapped[str | None] = mapped_column(String(64), nullable=True)
    surprise: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)


class EarningsEvent(_Stamped, Base):
    """time is Finnhub's hour field: bmo | amc | dmh | empty."""

    __tablename__ = "earnings_events"
    __table_args__ = (UniqueConstraint("symbol", "date"),)

    symbol: Mapped[str] = mapped_column(String(32))
    date: Mapped[date] = mapped_column(Date)
    time: Mapped[str | None] = mapped_column(String(16), nullable=True)
    eps_est: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    eps_act: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    rev_est: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    rev_act: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)


class MarketSentiment(_Stamped, Base):
    """A market-wide sentiment reading, e.g. index_key crypto_fng (Alternative.me, 0-100)."""

    __tablename__ = "market_sentiment"
    __table_args__ = (UniqueConstraint("index_key", "ts"),)

    index_key: Mapped[str] = mapped_column(String(64))
    value: Mapped[Decimal] = mapped_column(_num())
    classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ts: Mapped[datetime] = mapped_column(_tz())


class InstrumentAlias(_Stamped, Base):
    """Entity-linking alias. target_kind is instrument_family (e.g. BTC-USDT) | asset_class."""

    __tablename__ = "instrument_aliases"
    __table_args__ = (UniqueConstraint("alias_normalized", "target"),)

    alias: Mapped[str] = mapped_column(String(191))
    alias_normalized: Mapped[str] = mapped_column(String(191))
    target_kind: Mapped[str] = mapped_column(String(255))
    target: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(SmallInteger, default=0)
