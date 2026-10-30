"""okx_market and atlas_data read tools (ATL-85) against this checkout's test DB.

Run `cd backend && php artisan test` first so the DB is migrated. Every test runs inside one
transaction that is rolled back, so it can clear and seed env-wide tables (settings, equity) freely.
"""

from __future__ import annotations

import ast
import json
import math
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from atlas_agents.tools import account, market
from atlas_agents.tools import framework as fw
from atlas_agents.tools.catalogue import CATALOGUE
from atlas_agents.tools.framework import ToolContext, fq_name
from atlas_shared import market_data as md
from atlas_shared.db import (
    BalanceSnapshot,
    Candle,
    Connection,
    EquityPoint,
    FundingRate,
    Instrument,
    PositionSnapshot,
)
from sqlalchemy import Engine, delete, text
from sqlalchemy.orm import Session, sessionmaker

ENV = "demo"
CONFIG = {"max_result_tokens": 1_000_000, "chars_per_token": 4, "trim_rows": 10_000}
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

SECRETS = {
    "label": "Owner-Demo-Label-7731",
    "api_key": "eyJpdiI6ImFwaWtleS1jaXBoZXJ0ZXh0LTk5MjEifQ==",
    "api_secret": "eyJpdiI6InNlY3JldC1jaXBoZXJ0ZXh0LTQ0MTAifQ==",
    "passphrase": "eyJpdiI6InBhc3NwaHJhc2UtY2lwaGVydGV4dC0zMyJ9",
    "uid_last4": "Q9Z7",
}


@pytest.fixture
def db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    conn = engine.connect()
    outer = conn.begin()
    factory = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield factory
    finally:
        outer.rollback()
        conn.close()


class _Hashes:
    """A dict standing in for the Redis hashes trading_core's feed writes (atlas_shared.market_data)."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, str]] = {}

    def hgetall(self, name: str) -> dict[str, str]:
        return dict(self.data.get(name, {}))


def _ctx(
    factory: sessionmaker[Session], replay_as_of: datetime | None = None, hashes: _Hashes | None = None
) -> ToolContext:
    store = hashes or _Hashes()
    return ToolContext(
        env=ENV,
        session_factory=factory,
        agent="test_atl85",
        replay_as_of=replay_as_of,
        clock=lambda: NOW,
        market_snapshot=lambda env, inst: md.read_snapshot(store, env, inst),
    )


async def _call(ctx: ToolContext, server: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await fw.make_handler(fw.get_tool(fq_name(server, name)), ctx, CONFIG)(args)
    body: dict[str, Any] = json.loads(result["content"][0]["text"])
    assert result.get("is_error") is not True, body
    return body


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _inst(inst_id: str, inst_type: str = "SWAP") -> Instrument:
    return Instrument(
        env=ENV,
        inst_id=inst_id,
        inst_type=inst_type,
        asset_class="crypto",
        base_ccy=inst_id.split("-")[0],
        quote_ccy="USDT",
        settle_ccy="USDT",
        ct_val=Decimal("0.01"),
        lot_sz=Decimal("0.1"),
        min_sz=Decimal("0.1"),
        tick_sz=Decimal("0.1"),
        max_lever=Decimal("50"),
        state="live",
        list_time=NOW - timedelta(days=400),
        created_at=NOW - timedelta(days=400),
        updated_at=NOW - timedelta(days=400),
        last_seen_at=NOW - timedelta(hours=1),
    )


def _candle(inst_id: str, bar: str, ts: datetime, close: float, vol_quote: str = "1000") -> Candle:
    c = Decimal(repr(close))
    return Candle(
        env=ENV,
        inst_id=inst_id,
        bar=bar,
        ts=ts,
        o=c,
        h=c * Decimal("1.01"),
        l=c * Decimal("0.99"),
        c=c,
        vol=Decimal("10"),
        vol_ccy=Decimal("1"),
        vol_ccy_quote=Decimal(vol_quote),
        confirmed=True,
    )


def _uid() -> str:
    return uuid.uuid4().hex[:8].upper()


# -- replay bounds -----------------------------------------------------------------------------------


async def test_replay_returns_no_row_after_the_cutoff(db: sessionmaker[Session]) -> None:
    a, b = f"RA{_uid()}-USDT-SWAP", f"RB{_uid()}-USDT-SWAP"
    cutoff = datetime(2026, 10, 20, 10, 30, tzinfo=UTC)
    with db() as s:
        s.add_all([_inst(a), _inst(b)])
        # 1H bars from 48h before the cutoff to 12h after it: the 10:00 bar is still open at 10:30,
        # and everything from 11:00 on is later than the cutoff.
        start = cutoff.replace(minute=0) - timedelta(hours=48)
        for i in range(61):
            ts = start + timedelta(hours=i)
            s.add(_candle(a, "1H", ts, 100 + i))
            s.add(_candle(b, "1H", ts, 50 + i))
        # 1D bars (16:00 UTC grid) for 40 days before the cutoff and 5 after.
        day0 = datetime(2026, 9, 9, 16, 0, tzinfo=UTC)
        rng = np.random.default_rng(7)
        for i in range(46):
            ts = day0 + timedelta(days=i)
            s.add(_candle(a, "1D", ts, float(100 * math.exp(rng.normal(0, 0.02) * (i + 1)))))
            s.add(_candle(b, "1D", ts, float(80 * math.exp(rng.normal(0, 0.02) * (i + 1)))))
        for i in range(-30, 10):
            s.add(
                FundingRate(
                    env=ENV, inst_id=a, ts=cutoff + timedelta(hours=8 * i), funding_rate=Decimal("0.0001")
                )
            )
        s.commit()

    ctx = _ctx(db, replay_as_of=cutoff)

    candles = await _call(ctx, "okx_market", "get_candles", {"inst_id": a, "bar": "1H", "limit": 1000})
    stamps = [_ts(c["ts"]) for c in candles["data"]["candles"]]
    assert stamps and all(t + timedelta(hours=1) <= cutoff for t in stamps)
    assert max(stamps) == datetime(2026, 10, 20, 9, 0, tzinfo=UTC)  # the open 10:00 bar is held back
    assert _ts(candles["as_of"]) <= cutoff

    tickers = await _call(ctx, "okx_market", "get_tickers", {"inst_type": "SWAP"})
    mine = {t["inst_id"]: t for t in tickers["data"]["tickers"] if t["inst_id"] in (a, b)}
    assert set(mine) == {a, b}
    assert _ts(mine[a]["last_bar_ts"]) == datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    assert Decimal(mine[a]["last"]) == Decimal(100 + 47)
    assert all(_ts(t["last_bar_ts"]) <= cutoff for t in tickers["data"]["tickers"])
    assert _ts(tickers["as_of"]) <= cutoff

    funding = await _call(ctx, "okx_market", "get_funding", {"inst_id": a, "history_days": 30})
    f_stamps = [_ts(r["ts"]) for r in funding["data"]["history"]]
    assert f_stamps and max(f_stamps) == cutoff and all(t <= cutoff for t in f_stamps)
    assert _ts(funding["as_of"]) <= cutoff

    corr = await _call(ctx, "atlas_data", "get_correlations", {"inst_ids": [a, b], "lookback_days": 60})
    assert _ts(corr["as_of"]) <= cutoff
    # Daily bars closed by the cutoff: the 1D bar opening 2026-10-19 16:00 closes 10-20 16:00, after it.
    with db() as s:
        closes = {
            inst: [
                float(c)
                for (c,) in s.execute(
                    text(
                        "select c from candles where env=:e and inst_id=:i and bar='1D' "
                        "and ts + interval '1 day' <= :cut order by ts"
                    ),
                    {"e": ENV, "i": inst, "cut": cutoff},
                )
            ]
            for inst in (a, b)
        }
    ra, rb = (np.diff(np.log(closes[x])) for x in (a, b))
    pair = corr["data"]["pairs"][0]
    assert pair["n"] == len(ra)
    assert abs(pair["corr"] - float(np.corrcoef(ra, rb)[0, 1])) < 1e-6


async def test_known_correlation_to_1e_6(db: sessionmaker[Session]) -> None:
    a, b = f"CA{_uid()}-USDT-SWAP", f"CB{_uid()}-USDT-SWAP"
    rng = np.random.default_rng(42)
    days = 30
    x = rng.normal(0, 0.02, days)
    y = 0.6 * x + 0.8 * rng.normal(0, 0.02, days)
    expected = float(np.corrcoef(x, y)[0, 1])
    last_open = datetime(2026, 10, 28, 16, 0, tzinfo=UTC) - timedelta(days=1)  # closed by NOW
    with db() as s:
        s.add_all([_inst(a), _inst(b)])
        pa, pb = 100.0, 40.0
        for i in range(days + 1):
            ts = last_open - timedelta(days=days - i)
            if i > 0:
                pa *= math.exp(x[i - 1])
                pb *= math.exp(y[i - 1])
            s.add(_candle(a, "1D", ts, pa))
            s.add(_candle(b, "1D", ts, pb))
        s.commit()
    body = await _call(_ctx(db), "atlas_data", "get_correlations", {"inst_ids": [a, b], "lookback_days": 40})
    pair = body["data"]["pairs"][0]
    assert pair["n"] == days
    assert abs(pair["corr"] - expected) < 1e-6
    assert body["source"] == "postgres:candles"


# -- no identifiers or keys --------------------------------------------------------------------------


def _seed_account_and_settings(s: Session) -> Connection:
    s.execute(delete(Connection).where(Connection.env == ENV))
    s.execute(text("delete from settings where env = :e"), {"e": ENV})
    s.execute(delete(EquityPoint).where(EquityPoint.env == ENV))
    s.execute(delete(BalanceSnapshot).where(BalanceSnapshot.env == ENV))
    s.execute(delete(PositionSnapshot).where(PositionSnapshot.env == ENV))
    connection = Connection(
        env=ENV,
        exchange="okx",
        permissions={"read": True, "trade": True},
        account_mode="futures",
        position_mode="net",
        last_verified_at=NOW - timedelta(hours=2),
        **SECRETS,
    )
    s.add(connection)
    data = {
        "risk_profile": "balanced",
        "max_loss_per_trade_pct": "1.0",
        "max_position_pct": "5",
        "max_daily_loss_pct": "4",
        "max_leverage": 5,
        "max_open_positions": 6,
        "autonomy_level": 2,
        "onboarding_completed": 1,
        # Keys that must never pass the allow-list, even though they sit in settings data.
        "api_key": SECRETS["api_key"],
        "connection_label": SECRETS["label"],
    }
    settings_id = uuid.uuid4()
    s.execute(
        text(
            "insert into settings (id, env, version, data, risk_profile, autonomy_level, "
            "max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at) "
            "values (:id, :env, 3, cast(:data as jsonb), 'balanced', 2, 1, 5, 5, :t, :t)"
        ),
        {"id": settings_id, "env": ENV, "data": json.dumps(data), "t": NOW - timedelta(hours=3)},
    )
    for version, hours, pct in ((1, 72, "0.5"), (3, 3, "1.0")):
        s.execute(
            text(
                "insert into settings_history (id, settings_id, version, data, diff, created_at) "
                "values (:id, :sid, :v, cast(:data as jsonb), '{}'::jsonb, :t)"
            ),
            {
                "id": uuid.uuid4(),
                "sid": settings_id,
                "v": version,
                "data": json.dumps({**data, "max_loss_per_trade_pct": pct}),
                "t": NOW - timedelta(hours=hours),
            },
        )
    taken = NOW - timedelta(minutes=1)
    s.add(
        EquityPoint(
            env=ENV,
            ts=taken.replace(second=0, microsecond=0),
            total_eq_usd=Decimal("100000"),
            upl_usd=Decimal("-25.5"),
        )
    )
    s.add_all(
        [
            BalanceSnapshot(
                env=ENV,
                account="trading",
                ccy="USDT",
                eq=Decimal("90000"),
                avail_bal=Decimal("80000"),
                eq_usd=Decimal("90000"),
                taken_at=taken,
            ),
            BalanceSnapshot(
                env=ENV,
                account="funding",
                ccy="USDT",
                eq=Decimal("10000"),
                eq_usd=Decimal("10000"),
                taken_at=taken,
            ),
            PositionSnapshot(
                env=ENV,
                inst_id="BTC-USDT-SWAP",
                pos_side="net",
                pos=Decimal("2"),
                avg_px=Decimal("60000"),
                margin=Decimal("1200"),
                mgn_mode="isolated",
                taken_at=taken,
            ),
            # An older snapshot's position must not count as open now.
            PositionSnapshot(
                env=ENV,
                inst_id="ETH-USDT-SWAP",
                pos_side="net",
                pos=Decimal("5"),
                taken_at=taken - timedelta(minutes=1),
            ),
        ]
    )
    s.flush()
    return connection


async def test_account_and_settings_output_carry_no_identifiers_or_keys(db: sessionmaker[Session]) -> None:
    with db() as s:
        connection = _seed_account_and_settings(s)
        connection_id = str(connection.id)
        s.commit()
    ctx = _ctx(db)
    outputs = {
        name: await _call(ctx, "atlas_data", name, {})
        for name in ("get_settings_snapshot", "get_account_summary", "get_positions", "get_today_budget_left")
    }
    for name, body in outputs.items():
        blob = json.dumps(body)
        for value in (connection_id, *SECRETS.values()):
            assert value not in blob, (name, value)
        for word in ("api_key", "secret", "passphrase", "label", "uid"):
            assert word not in blob.lower(), (name, word)

    snapshot = outputs["get_settings_snapshot"]["data"]
    assert snapshot["version"] == 3
    assert set(snapshot["values"]) <= account.SETTINGS_SNAPSHOT_KEYS
    assert snapshot["values"]["max_loss_per_trade_pct"] == "1.0"
    assert snapshot["values"]["max_leverage"] == 5
    assert "onboarding_completed" not in snapshot["values"]

    summary = outputs["get_account_summary"]["data"]
    assert Decimal(summary["total_equity_usd"]) == Decimal("100000")
    assert Decimal(summary["trading_equity_usd"]) == Decimal("90000")
    assert Decimal(summary["funding_equity_usd"]) == Decimal("10000")
    assert summary["open_positions"] == 1
    assert [p["inst_id"] for p in outputs["get_positions"]["data"]["positions"]] == ["BTC-USDT-SWAP"]

    budget = outputs["get_today_budget_left"]["data"]
    assert Decimal(budget["max_risk_per_trade_usd"]) == Decimal("1000")
    assert Decimal(budget["max_position_usd"]) == Decimal("5000")
    assert Decimal(budget["daily_loss_limit_usd"]) == Decimal("4000")
    assert Decimal(budget["daily_loss_used_usd"]) == 0
    assert budget["positions_left"] == 5
    assert "Executor" in budget["note"]


async def test_settings_snapshot_in_replay_is_the_version_in_force_then(db: sessionmaker[Session]) -> None:
    with db() as s:
        _seed_account_and_settings(s)
        s.commit()
    body = await _call(
        _ctx(db, replay_as_of=NOW - timedelta(hours=24)), "atlas_data", "get_settings_snapshot", {}
    )
    assert body["data"]["version"] == 1
    assert body["data"]["values"]["max_loss_per_trade_pct"] == "0.5"
    assert body["source"] == "postgres:settings_history"
    assert set(account.SETTINGS_SNAPSHOT_KEYS) == {
        "max_loss_per_trade_pct",
        "max_position_pct",
        "max_daily_loss_pct",
        "max_weekly_loss_pct",
        "max_leverage",
        "max_open_positions",
        "max_share_per_instrument_pct",
        "max_correlated_exposure_pct",
        "min_expected_r",
        "max_consecutive_losses",
        "autonomy_level",
        "max_instruments_per_day",
        "max_spread_bps",
        "min_24h_volume_usd_crypto_perp",
        "min_24h_volume_usd_stock_perp",
        "min_24h_volume_usd_commodity_perp",
        "min_24h_volume_usd_spot",
    }


# -- every tool answers with as_of and source --------------------------------------------------------


async def test_every_tool_has_as_of_and_source_and_an_unsubscribed_orderbook_is_unavailable(
    db: sessionmaker[Session],
) -> None:
    inst = f"EV{_uid()}-USDT-SWAP"
    with db() as s:
        _seed_account_and_settings(s)
        s.add(_inst(inst))
        s.add(_candle(inst, "1m", NOW - timedelta(minutes=3), 123.4))
        s.commit()
    ctx = _ctx(db)
    args: dict[str, dict[str, Any]] = {
        "list_instruments": {},
        "get_tickers": {"inst_type": "SWAP"},
        "get_candles": {"inst_id": inst, "bar": "1H"},
        "get_orderbook": {"inst_id": inst},
        "get_funding": {"inst_id": inst},
        "get_open_interest": {"inst_id": inst},
        "get_long_short_ratio": {"ccy": f"NONE{_uid()}"},
        "get_taker_volume": {"ccy": f"NONE{_uid()}"},
        "get_mark_price": {"inst_id": inst},
        "get_instrument_spec": {"inst_id": inst},
        "get_ta_table": {"inst_id": inst},
        "get_correlations": {"inst_ids": [inst, "NOPE-USDT-SWAP"]},
        "get_watchlist": {},
    }
    for server in ("okx_market", "atlas_data"):
        for name in CATALOGUE[server]:
            body = await _call(ctx, server, name, args.get(name, {}))
            assert _ts(body["as_of"]).utcoffset() is not None, name
            want = "redis:" if name == "get_orderbook" else "postgres:"
            assert body["source"].startswith(want), name

    book = await _call(ctx, "okx_market", "get_orderbook", {"inst_id": inst})
    assert book["data"]["available"] is False and book["data"]["reason"]
    assert book["data"]["bids"] == [] and book["data"]["asks"] == []

    empty = await _call(ctx, "okx_market", "get_funding", {"inst_id": inst})
    assert empty["data"]["history"] == [] and empty["data"]["reason"]
    assert _ts(empty["as_of"]) == NOW

    mark = await _call(ctx, "okx_market", "get_mark_price", {"inst_id": inst})
    assert mark["data"]["is_proxy"] is True
    assert Decimal(mark["data"]["price"]) == Decimal("123.4")
    assert mark["data"]["age_seconds"] == 120

    spec = await _call(ctx, "okx_market", "get_instrument_spec", {"inst_id": inst})
    assert Decimal(spec["data"]["tick_sz"]) == Decimal("0.1")

    orders = await _call(ctx, "atlas_data", "get_open_orders", {})
    assert orders["data"] == {"available": False, "orders": [], "reason": orders["data"]["reason"]}


async def test_orderbook_serves_the_live_books5_snapshot_while_fresh_and_refuses_it_when_stale(
    db: sessionmaker[Session],
) -> None:
    """ATL-125: get_orderbook reads the books5 hash the WebSocket feed keeps in Redis."""
    inst = f"OB{_uid()}-USDT-SWAP"
    with db() as s:
        s.add(_inst(inst))
        s.commit()
    hashes = _Hashes()
    book_at = NOW - timedelta(seconds=2)
    hashes.data[md.md_key(ENV, inst)] = {
        md.LAST_MSG_MS: str(md.to_ms(book_at)),
        "books5_ms": str(md.to_ms(book_at)),
        md.BOOKS5: json.dumps(
            {
                "asks": [["101.5", "3", "0", "2"], ["101.6", "4", "0", "1"]],
                "bids": [["101.4", "5", "0", "3"], ["101.3", "6", "0", "1"]],
                "ts": str(md.to_ms(book_at)),
                "seqId": 42,
            }
        ),
    }
    assert md.md_key(ENV, inst) == f"atlas:md:demo:{inst}"

    fresh = await _call(_ctx(db, hashes=hashes), "okx_market", "get_orderbook", {"inst_id": inst})
    assert fresh["data"]["available"] is True
    assert [[Decimal(p), Decimal(q)] for p, q in fresh["data"]["bids"]] == [
        [Decimal("101.4"), Decimal("5")],
        [Decimal("101.3"), Decimal("6")],
    ]
    assert [Decimal(p) for p, _ in fresh["data"]["asks"]] == [Decimal("101.5"), Decimal("101.6")]
    assert _ts(fresh["as_of"]) == book_at
    assert fresh["source"].startswith("redis:")

    one = await _call(_ctx(db, hashes=hashes), "okx_market", "get_orderbook", {"inst_id": inst, "depth": 1})
    assert len(one["data"]["bids"]) == 1 and len(one["data"]["asks"]) == 1

    # Older than market_ws.market_data_max_age_seconds (30 s): no depth is served.
    old = NOW - timedelta(seconds=31)
    hashes.data[md.md_key(ENV, inst)]["books5_ms"] = str(md.to_ms(old))
    stale = await _call(_ctx(db, hashes=hashes), "okx_market", "get_orderbook", {"inst_id": inst})
    assert stale["data"]["available"] is False
    assert stale["data"]["bids"] == [] and stale["data"]["asks"] == []
    assert "stale" in stale["data"]["reason"]

    # Replay never sees the live book.
    hashes.data[md.md_key(ENV, inst)]["books5_ms"] = str(md.to_ms(book_at))
    replay = await _call(
        _ctx(db, replay_as_of=NOW, hashes=hashes), "okx_market", "get_orderbook", {"inst_id": inst}
    )
    assert replay["data"]["available"] is False

    def broken(env: str, inst_id: str) -> md.MarketSnapshot:
        raise ConnectionError("redis down")

    down_ctx = ToolContext(env=ENV, session_factory=db, agent="t", clock=lambda: NOW, market_snapshot=broken)
    down = await _call(down_ctx, "okx_market", "get_orderbook", {"inst_id": inst})
    assert down["data"]["available"] is False and "unreachable" in down["data"]["reason"]


async def test_unknown_instrument_spec_is_not_found(db: sessionmaker[Session]) -> None:
    tool = fw.get_tool(fq_name("okx_market", "get_instrument_spec"))
    result = await fw.make_handler(tool, _ctx(db), CONFIG)({"inst_id": "NOPE-USDT-SWAP"})
    assert result["is_error"] is True
    assert json.loads(result["content"][0]["text"])["error"] == "not_found"


# -- read-only by construction -----------------------------------------------------------------------


@pytest.mark.parametrize("module", [account, market])
def test_tool_modules_only_read_postgres(module: Any) -> None:
    source = Path(module.__file__).read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    for name in imported:
        root = name.split(".")[0]
        assert root not in {"trading_core", "okx", "httpx", "requests", "ccxt", "websockets"}, name
        assert not name.endswith((".insert", ".update", ".delete")), name
    for call in ("session.add(", "session.merge(", "session.delete(", ".commit(", ".execute(text("):
        assert call not in source, call
    assert "Connection" not in imported and "atlas_shared.db.Connection" not in imported
