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
    CHAR,
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
    func,
    text,
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
    never log it. Ingest writes only the health columns (failures, disabled_until, last_*).
    provider (ATL-71) is the vendor that owns the key; every keyed source of one provider holds the
    same ciphertext."""

    __tablename__ = "news_sources"
    __table_args__ = (UniqueConstraint("key"),)

    key: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    poll_seconds: Mapped[int] = mapped_column(Integer)
    requires_key: Mapped[bool] = mapped_column(Boolean, default=False)
    reputable: Mapped[bool] = mapped_column(Boolean, default=False)
    """ATL-106: a newsroom with editorial standards; 2+ distinct reputable sources make a story CONFIRMED."""
    official: Mapped[bool] = mapped_column(Boolean, default=False)
    """ATL-106: the venue's own channel (OKX announcements); one official source makes a story CONFIRMED."""
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


# --- Agent runtime (ATL-78, decision #245). `agent` is a warroom.agents slug from defaults.yaml.


def _jsonb_obj() -> Any:
    """jsonb NOT NULL DEFAULT '{}': omit it on insert and Postgres fills ``{}``."""
    return mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class PromptVersion(_Stamped, Base):
    """One stored prompt body per (agent, version); sha256 is the hex digest of body."""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("agent", "version"), UniqueConstraint("agent", "sha256"))

    agent: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    body: Mapped[str] = mapped_column(Text)


class AgentRun(_Row, Base):
    """One agent session turn. trigger: mention|schedule|eval|manual|alert (ATL-107).
    status: running|ok|error|degraded|rate_limited|over_budget. cost_usd_notional is display-only."""

    __tablename__ = "agent_runs"

    agent: Mapped[str] = mapped_column(String(64))
    trigger: Mapped[str] = mapped_column(String(255))
    correlation_id: Mapped[str | None] = mapped_column(String(191), nullable=True)
    started_at: Mapped[datetime] = mapped_column(_tz())
    ended_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    status: Mapped[str] = mapped_column(String(255), default="running")
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd_notional: Mapped[Decimal] = mapped_column(Numeric(18, 6, asdecimal=True), default=Decimal(0))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentUsageDaily(_Stamped, Base):
    """Per agent, env and Asia/Baghdad day. Write only through atlas_shared.db.agents.add_usage,
    which accumulates with ON CONFLICT (agent, env, day)."""

    __tablename__ = "agent_usage_daily"
    __table_args__ = (UniqueConstraint("agent", "env", "day"),)

    agent: Mapped[str] = mapped_column(String(64))
    env: Mapped[str] = mapped_column(String(255))
    day: Mapped[date] = mapped_column(Date)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    runs: Mapped[int] = mapped_column(Integer, default=0)


class AgentState(_Stamped, Base):
    """Live status per (agent, env). state: idle|thinking|waiting|degraded|rate_limited|paused|disabled."""

    __tablename__ = "agent_states"
    __table_args__ = (UniqueConstraint("agent", "env"),)

    agent: Mapped[str] = mapped_column(String(64))
    env: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(255), default="idle")
    current_task: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_since: Mapped[datetime] = mapped_column(_tz(), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResearchReport(_Row, Base):
    """Brief §5 research_reports. confidence is CHECKed to 0..1."""

    __tablename__ = "research_reports"

    agent: Mapped[str] = mapped_column(String(64))
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    inst_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(64))
    content_md: Mapped[str] = mapped_column(Text)
    data: Mapped[Any] = _jsonb_obj()
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 4, asdecimal=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)


class AgentNotebookEntry(_Row, Base):
    """One note on the shared daily notebook; day is the Asia/Baghdad date."""

    __tablename__ = "agent_notebook"

    day: Mapped[date] = mapped_column(Date)
    agent: Mapped[str] = mapped_column(String(64))
    note_md: Mapped[str] = mapped_column(Text)
    data: Mapped[Any] = _jsonb_obj()


class JournalEntry(_Row, Base):
    """Post-mortem / lesson (brief §5, §7.8). proposal_id gets its FK when proposals exist (Phase 2)."""

    __tablename__ = "journal_entries"

    agent: Mapped[str] = mapped_column(String(64))
    inst_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    expected_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[Any] = _jsonb_obj()
    tags: Mapped[list[str]] = mapped_column(
        _text_array(), nullable=False, server_default=text("'{}'::text[]")
    )


class AgentScore(_Row, Base):
    """Per agent, env and Asia/Baghdad day. hit_rate and brier_score are CHECKed to 0..1."""

    __tablename__ = "agent_scores"
    __table_args__ = (UniqueConstraint("agent", "env", "day"),)

    agent: Mapped[str] = mapped_column(String(64))
    day: Mapped[date] = mapped_column(Date)
    n: Mapped[int] = mapped_column(Integer, default=0)
    hit_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4, asdecimal=True), nullable=True)
    brier_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 6, asdecimal=True), nullable=True)
    calibration: Mapped[Any] = _jsonb_obj()
    pnl_attributed_usd: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    data: Mapped[Any] = _jsonb_obj()


class Watchlist(_Row, Base):
    """Market Scout's watchlist for one Asia/Baghdad business day (ATL-102, decision #247 (3)).

    ``date`` is a DATE, never a timestamp. Re-runs add a row with the next ``version``; the current
    watchlist for (env, date) is the highest version. status: draft|published|partial_evidence|empty.
    Write it with ``atlas_agents.watchlists.save_watchlist``, which validates the picks."""

    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("env", "date", "version"),)

    date: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(255))
    edit_window_ends_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    data: Mapped[Any] = _jsonb_obj()


class WatchlistItem(_Stamped, Base):
    """One pick on a watchlist. conviction is CHECKed to 0..1 and rank to >= 1; rank and inst_id
    are unique within a watchlist. removed_by_owner_at is set when the owner removes the pick."""

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "rank"),
        UniqueConstraint("watchlist_id", "inst_id"),
    )

    watchlist_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("watchlists.id", ondelete="CASCADE"))
    inst_id: Mapped[str] = mapped_column(String(64))
    rank: Mapped[int] = mapped_column(Integer)
    asset_class: Mapped[str] = mapped_column(String(32))
    bias: Mapped[str] = mapped_column(String(255))
    conviction: Mapped[Decimal] = mapped_column(Numeric(6, 4, asdecimal=True))
    thesis: Mapped[str] = mapped_column(Text)
    catalysts: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    key_levels: Mapped[Any] = _jsonb_obj()
    invalidation: Mapped[str] = mapped_column(Text)
    pinned_by_owner: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    removed_by_owner_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    data_as_of: Mapped[datetime] = mapped_column(_tz())


def _jsonb_list() -> Any:
    """jsonb NOT NULL DEFAULT '[]'."""
    return mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))


def _text_blank() -> Any:
    """text NOT NULL DEFAULT ''."""
    return mapped_column(Text, nullable=False, server_default=text("''"))


class Cycle(_Row, Base):
    """One Orchestrator cycle (ATL-124, brief §7.9). state is CHECKed to the §7.9 machine:
    cycle_start, research_requests_sent, research_collected, proposals_open, reviews_done, voting,
    vote_closed, risk_check, awaiting_owner, executing, cycle_closed. deadlines maps a step name to
    an ISO-8601 UTC deadline."""

    __tablename__ = "cycles"

    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(255), server_default=text("'cycle_start'"))
    started_at: Mapped[datetime] = mapped_column(_tz())
    deadlines: Mapped[Any] = _jsonb_obj()
    closed_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)


class Proposal(_Row, Base):
    """A TradeProposal (entry, from the Strategist) or a position action (amend_stop, amend_targets,
    partial_close, close, from the Portfolio Manager). ATL-124, brief §5/§6.3/§8.

    status is CHECKed to the §8 lifecycle (draft, under_review, voting, passed, rejected,
    risk_checked, awaiting_owner, sent, partially_filled, filled, closed, cancelled, expired).
    An entry must carry side, order_type and stop. A revision is a new row with version + 1 and
    parent_id = the revised row (version 1 has no parent). Prices are in the instrument's quote
    currency; size_suggestion_pct is % of equity; probability is 0..1."""

    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("parent_id"),)

    cycle_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("cycles.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("proposals.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(255), server_default=text("'entry'"))
    author: Mapped[str] = mapped_column(String(64))
    inst_id: Mapped[str] = mapped_column(String(64))
    side: Mapped[str | None] = mapped_column(String(255), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    entry: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    entry_zone: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    stop: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    take_profits: Mapped[Any] = _jsonb_list()
    time_stop_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_suggestion_pct: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    close_pct: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    """partial_close only: % of the position to close, 0 < x < 100 (ATL-153)."""
    leverage_suggestion: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_r: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    probability: Mapped[Decimal | None] = mapped_column(Numeric(6, 4, asdecimal=True), nullable=True)
    rationale_md: Mapped[str] = _text_blank()
    invalidation: Mapped[str] = _text_blank()
    evidence: Mapped[Any] = _jsonb_list()
    status: Mapped[str] = mapped_column(String(255), server_default=text("'draft'"))


class Vote(_Row, Base):
    """One voter's vote on a proposal (take|skip|change); confidence is CHECKed to 0..1."""

    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("proposal_id", "agent"),)

    proposal_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("proposals.id", ondelete="CASCADE"))
    agent: Mapped[str] = mapped_column(String(64))
    vote: Mapped[str] = mapped_column(String(255))
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 4, asdecimal=True))
    reason: Mapped[str] = _text_blank()
    fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    """A "change" vote's requested fix (ATL-153)."""


class RiskCheck(_Row, Base):
    """One Risk Engine run on a proposal (brief §10). result approved|resized|rejected; decision
    send_now|awaiting_owner|advise_only (ATL-127), and null exactly when the result is rejected
    (CHECKed). checks is the ordered list of the nine §10 check results (trading_core.risk.engine).
    final_size is contracts after lotSz rounding;
    final_notional_usd is USD."""

    __tablename__ = "risk_checks"

    proposal_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("proposals.id", ondelete="CASCADE"))
    settings_version: Mapped[int] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String(255))
    checks: Mapped[Any] = _jsonb_list()
    final_size: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    final_notional_usd: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    leverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Order(_Row, Base):
    """An order the Executor placed or reconciliation imported (ATL-141, brief §5, §11).

    state is CHECKed to the Executor machine: new, sending, live, partially_filled, filled,
    canceled, rejected, failed. origin atlas|external; purpose entry|protect|close|kill. cl_ord_id
    is unique (rule 8: retries reuse it) and required when origin is atlas; an atlas entry also
    needs proposal_id and risk_check_id (rule 1, CHECKed). px is null for market orders; sizes are
    contracts (or base currency for spot); filled_sz <= sz."""

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("env", "ord_id"),)

    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposals.id", ondelete="SET NULL"), nullable=True
    )
    risk_check_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("risk_checks.id", ondelete="SET NULL"), nullable=True
    )
    cl_ord_id: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    ord_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inst_id: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(255))
    pos_side: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ord_type: Mapped[str] = mapped_column(String(255))
    td_mode: Mapped[str] = mapped_column(String(255))
    px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    sz: Mapped[Decimal] = mapped_column(_num())
    filled_sz: Mapped[Decimal] = mapped_column(_num(), server_default=text("0"))
    avg_px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    lever: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fee: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    fee_ccy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state: Mapped[str] = mapped_column(String(255), server_default=text("'new'"))
    reduce_only: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    origin: Mapped[str] = mapped_column(String(255), server_default=text("'atlas'"))
    purpose: Mapped[str] = mapped_column(String(255), server_default=text("'entry'"))
    last_error_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[Any] = _jsonb_obj()


class AlgoOrder(_Row, Base):
    """A server-side TP/SL or trigger order (ATL-141, brief §1 rule 3, §11). kind sl|tp|oco|trigger;
    sl/tp/trigger carry trigger_px, an oco carries sl_trigger_px and tp_trigger_px (CHECKed). ord_px
    null = market on trigger; sz null = the whole position. state: new, sending, failed (ours) or
    OKX's live, pause, effective, partially_effective, canceled, order_failed, partially_failed.
    algo_cl_ord_id is unique and required when origin is atlas."""

    __tablename__ = "algo_orders"
    __table_args__ = (UniqueConstraint("env", "algo_id"),)

    order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    algo_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    algo_cl_ord_id: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    inst_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(255))
    side: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pos_side: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trigger_px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    sl_trigger_px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    tp_trigger_px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    ord_px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    sz: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    state: Mapped[str] = mapped_column(String(255), server_default=text("'new'"))
    reduce_only: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    origin: Mapped[str] = mapped_column(String(255), server_default=text("'atlas'"))
    last_error_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[Any] = _jsonb_obj()


class Fill(_Row, Base):
    """One OKX fill (ATL-141, brief §5). Unique per (env, inst_id, trade_id). fee follows OKX's
    sign: negative = paid, positive = rebate. exec_type T taker / M maker."""

    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("env", "inst_id", "trade_id"),)

    order_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    trade_id: Mapped[str] = mapped_column(String(64))
    ord_id: Mapped[str] = mapped_column(String(64))
    cl_ord_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bill_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inst_id: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(255))
    pos_side: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fill_px: Mapped[Decimal] = mapped_column(_num())
    fill_sz: Mapped[Decimal] = mapped_column(_num())
    fee: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    fee_ccy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exec_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    ts: Mapped[datetime] = mapped_column(_tz())
    raw: Mapped[Any] = _jsonb_obj()
    # OKX fillPnl (ATL-172): realised PnL of a closing fill, 0 on opening fills.
    fill_pnl: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)


class PositionClosed(_Row, Base):
    """One OKX ``account/positions-history`` record (ATL-172, brief §13 History): a full or partial
    close, liquidation or ADL. Unique per (env, pos_id, closed_at). side is the closed position's
    direction (long|short). proposal_id/agent are set when an Atlas entry order opened it
    (origin atlas, CHECKed); otherwise origin external. R is derived by the API, not stored."""

    __tablename__ = "positions_closed"
    __table_args__ = (UniqueConstraint("env", "pos_id", "closed_at"),)

    pos_id: Mapped[str] = mapped_column(String(64))
    inst_id: Mapped[str] = mapped_column(String(64))
    inst_type: Mapped[str] = mapped_column(String(16))
    mgn_mode: Mapped[str] = mapped_column(String(16))
    pos_side: Mapped[str] = mapped_column(String(255))
    side: Mapped[str] = mapped_column(String(255))
    close_type: Mapped[str] = mapped_column(String(8))
    lever: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    ccy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    size: Mapped[Decimal] = mapped_column(_num())
    entry_px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    exit_px: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    realised_pnl: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    pnl: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    fee: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    funding_fee: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    liq_penalty: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    pnl_ratio: Mapped[Decimal | None] = mapped_column(_num(), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(_tz())
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("proposals.id", ondelete="SET NULL"), nullable=True
    )
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    setup_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str] = mapped_column(String(255), server_default=text("'external'"))
    raw: Mapped[Any] = _jsonb_obj()


class TradingState(_Row, Base):
    """Whether new orders are paused in an env, and why (ATL-141; circuit breakers are ATL-127's).
    One row per env. breakers maps a breaker name to {"active", "since", "detail"}."""

    __tablename__ = "trading_state"
    __table_args__ = (UniqueConstraint("env"),)

    paused: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    breakers: Mapped[Any] = _jsonb_obj()
    paused_at: Mapped[datetime | None] = mapped_column(_tz(), nullable=True)
