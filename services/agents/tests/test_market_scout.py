"""ATL-108: Market Scout publishes the 08:00 watchlist after consulting News and Historian.

The supervisor runs the real ``scout_watchlist`` hook with a fake Claude client. The feature table
and shortlist (ATL-104/105, tested there) are stubbed in memory; a publisher stands in for
atlas:bridge, storing every warroom.message in warroom_messages and, for the teammates the test
names, storing a reply whose reply_to is the mention's row (what News/Historian do).

DB tests need this checkout's test DB migrated (`cd backend && php artisan test`).
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from atlas_agents import news_intelligence
from atlas_agents import testing as fake
from atlas_agents.agents import market_scout
from atlas_agents.registry import REGISTRY, AgentHooks, discover_hooks
from atlas_agents.schemas import WatchlistPick
from atlas_agents.scout import rerun
from atlas_agents.scout.features import FeatureTable, InstrumentFeatures
from atlas_agents.scout.prescore import CorrelatedPair, build_shortlist
from atlas_agents.scout.publish import (
    Pair,
    correlated_groups,
    enforce,
    trim_correlated,
)
from atlas_agents.scout.universe import Rejected
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools import ToolContext
from atlas_agents.watchlists import save_watchlist
from atlas_shared.db import AgentRun, AgentState, AgentUsageDaily, Candle, Watchlist, WatchlistItem
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

ENV = "demo"
SLUG = "market_scout"


def _now() -> datetime:
    # 08:00 Asia/Baghdad on a far-future day unique to this test, so no other test's rows collide.
    offset = uuid.uuid4().int % 3000
    return datetime(2034, 1, 1, 5, 0, tzinfo=UTC) + timedelta(days=offset)


def _row(inst_id: str, as_of: datetime, **kw: Any) -> InstrumentFeatures:
    base: dict[str, Any] = {
        "inst_id": inst_id,
        "asset_class": "crypto",
        "market": "crypto_perp",
        "volume_24h_usd": Decimal("50000000"),
        "returns_pct": {"1d": 1.0, "3d": 2.0, "7d": 3.0, "30d": 4.0},
        "atr_pct_1h": 0.5,
        "atr_pct_1d": 2.0,
        "vol_ratio_24h_30d": 1.2,
        "volume_z_30d": 0.5,
        "ma_distance_pct": {"20d": 1.0, "50d": 2.0, "200d": 3.0},
        "breakout_high_20d": False,
        "breakout_low_20d": False,
        "funding_rate": 0.0001,
        "funding_z_7d": 0.3,
        "oi_change_24h_pct": 1.0,
        "oi_series": inst_id,
        "long_short_ratio": 1.1,
        "corr_btc_30d": 0.5,
        "corr_spy_30d": 0.1,
        "news_count_24h": 0,
        "news_max_importance_24h": None,
        "catalysts": [],
        "catalyst_count_48h": 0,
        "catalyst_max_importance_48h": None,
        "regime_label": "trend",
        "regime_probability": 0.7,
        "regime_method": "hmm",
        "data_as_of": as_of,
        "missing": {},
        "quality_notes": [],
    }
    base.update(kw)
    return InstrumentFeatures(**base)


CLASSES = {"XAU-USDT-SWAP": "commodity", "TSLA-USDT-SWAP": "stock"}

INSTS = [
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "XAU-USDT-SWAP",
    "TSLA-USDT-SWAP",
    "DOGE-USDT-SWAP",
]


def _prepared(
    now: datetime,
    *,
    rows: list[str] | None = None,
    pairs: list[tuple[str, str, float]] = (),  # type: ignore[assignment]
    blocklist: list[str] | None = None,
    table_age: timedelta = timedelta(minutes=1),
    rejected: list[Rejected] | None = None,
    degraded: list[str] | None = None,
) -> market_scout.Prepared:
    as_of = now - table_age
    ids = INSTS if rows is None else rows
    table = FeatureTable(
        env=ENV,
        as_of=as_of,
        valid_until=as_of + timedelta(minutes=15),
        settings_version=1,
        btc_reference="BTC-USDT-SWAP",
        spy_reference=None,
        shared_catalysts=[],
        rows=[
            _row(
                i,
                as_of,
                asset_class=CLASSES.get(i, "crypto"),
                news_count_24h=2 if i == "XAU-USDT-SWAP" else 0,
                returns_pct={"1d": 1.0, "3d": 2.0, "7d": 3.0 + n, "30d": 4.0},
            )
            for n, i in enumerate(ids)
        ],
        rejected=rejected or [],
        notes=[],
    )
    shortlist = build_shortlist(table)
    if shortlist.rows:
        shortlist = shortlist.model_copy(
            update={"correlated_pairs": [CorrelatedPair(a=a, b=b, corr=c, n=29) for a, b, c in pairs]}
        )
    return market_scout.Prepared(
        table=table,
        shortlist=shortlist,
        report_id=None,
        blocklist=blocklist or [],
        max_picks=5,
        degraded_asset_classes=degraded or [],
    )


def _pick(inst_id: str, rank: int, conviction: float, as_of: datetime) -> dict[str, Any]:
    return {
        "inst_id": inst_id,
        "rank": rank,
        "asset_class": CLASSES.get(inst_id, "crypto"),
        "bias": "long",
        "conviction": conviction,
        "thesis": f"{inst_id} broke out on rising volume with a catalyst ahead.",
        "catalysts": [],
        "key_levels": {"support": 100.0, "resistance": 120.0},
        "invalidation": "Daily close back below support",
        "data_as_of": as_of.isoformat(),
    }


def _answer(now: datetime, picks: list[tuple[str, float]], all_ids: list[str] | None = None) -> list[Any]:
    ids = all_ids or INSTS
    chosen = [p for p, _ in picks]
    rejected = [i for i in ids if i not in chosen][:3]
    card = {
        "type": "watchlist",
        "backdrop": ["BTC trending up.", "Gold flat ahead of CPI.", "No big US event in 48h."],
        "picks": [_pick(i, n, c, now) for n, (i, c) in enumerate(picks, start=1)],
        "rejected": [{"inst_id": r, "reason": "weaker evidence"} for r in rejected],
        "no_pick_reason": None if picks else "nothing tradeable",
    }
    return [
        fake.assistant_text(f"Picks below.\n\n```json\n{json.dumps(card)}\n```"),
        fake.result(tokens_in=100, tokens_out=50),
    ]


class Bridge:
    """Stands in for atlas:bridge: stores each warroom.message, and answers chosen mentions."""

    def __init__(self, engine: Engine, now: datetime, answer: set[str]) -> None:
        self.engine = engine
        self.now = now
        self.answer = answer
        self.events: list[tuple[str, EventEnvelope]] = []
        self.row_ids: list[str] = []
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                    "VALUES (:id, 'demo', 'main', 'main', 'Main', :ts, :ts) "
                    "ON CONFLICT (env, kind, key) DO NOTHING"
                ),
                {"id": str(uuid.uuid4()), "ts": now},
            )
            self.thread_id = str(
                conn.execute(
                    text("SELECT id FROM warroom_threads WHERE env='demo' AND kind='main' AND key='main'")
                ).scalar_one()
            )

    def of(self, type_: str) -> list[EventEnvelope]:
        return [e for _s, e in self.events if e.type == type_]

    def _store(
        self, *, event_id: str | None, author: str, kind: str, content: str, card: Any, reply_to: str | None
    ) -> str:
        row_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                    "content_md, card, reply_to, created_at, updated_at) "
                    "VALUES (:id, 'demo', :thread, :event, "
                    "'agent', :author, :kind, :content, CAST(:card AS jsonb), :reply_to, :ts, :ts)"
                ),
                {
                    "id": row_id,
                    "thread": self.thread_id,
                    "event": event_id,
                    "author": author,
                    "kind": kind,
                    "content": content,
                    "card": None if card is None else json.dumps(card),
                    "reply_to": reply_to,
                    "ts": self.now,
                },
            )
        self.row_ids.append(row_id)
        return row_id

    def __call__(self, stream: str, envelope: EventEnvelope) -> None:
        self.events.append((stream, envelope))
        if envelope.type != "warroom.message":
            return
        p = envelope.payload
        row = self._store(
            event_id=str(envelope.id),
            author=str(p["author"]),
            kind=str(p["kind"]),
            content=str(p["content_md"]),
            card=p.get("card"),
            reply_to=p.get("reply_to"),
        )
        for target in p.get("mentions") or []:
            if target in self.answer:
                self._store(
                    event_id=None,
                    author=target,
                    kind="research_report",
                    content=f"{target.upper()}-REPLY-MARKER: evidence for the shortlist.",
                    card=None,
                    reply_to=row,
                )

    def cleanup(self) -> None:
        with self.engine.begin() as conn:
            for row in reversed(self.row_ids):
                conn.execute(text("DELETE FROM warroom_messages WHERE id = :id"), {"id": row})


@pytest.fixture
def now() -> datetime:
    return _now()


@pytest.fixture
def cleanup(engine: Engine, now: datetime) -> Iterator[list[Bridge]]:
    bridges: list[Bridge] = []
    yield bridges
    for b in bridges:
        b.cleanup()
    day = (now + timedelta(hours=3)).date()
    with engine.begin() as conn:
        conn.execute(delete(Watchlist).where(Watchlist.env == ENV, Watchlist.date == day))
        conn.execute(
            delete(AgentRun).where(
                AgentRun.agent == SLUG,
                AgentRun.started_at >= now - timedelta(days=1),
                AgentRun.started_at <= now + timedelta(days=1),
            )
        )
        conn.execute(delete(AgentUsageDaily).where(AgentUsageDaily.agent == SLUG, AgentUsageDaily.day == day))
        conn.execute(delete(AgentState).where(AgentState.agent == SLUG, AgentState.env == ENV))


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_scout, "wait_seconds", lambda: 1.0)
    monkeypatch.setattr(market_scout, "poll_seconds", lambda: 0.05)
    monkeypatch.setattr(market_scout, "link_seconds", lambda: 1.0)

    async def no_positions(ctx: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(market_scout, "_positions", no_positions)


def _supervisor(
    engine: Engine, bridge: Bridge, client: fake.FakeClaudeClient, now: datetime, tmp_path: Path
) -> Supervisor:
    spec = dataclasses.replace(REGISTRY[SLUG], enabled=True)
    return Supervisor(
        env=ENV,
        engine=engine,
        registry={SLUG: spec},
        client_factory=fake.factory(client),
        publisher=bridge,
        prompts_dir=tmp_path,
        clock=lambda: now,
        hooks={SLUG: discover_hooks()[SLUG]},
    )


def _saved(engine: Engine, now: datetime) -> tuple[Watchlist, list[WatchlistItem]]:
    day = (now + timedelta(hours=3)).date()
    with Session(engine) as db:
        wl = db.scalars(
            select(Watchlist)
            .where(Watchlist.env == ENV, Watchlist.date == day)
            .order_by(Watchlist.version.desc())
        ).first()
        assert wl is not None
        items = list(
            db.scalars(
                select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id).order_by(WatchlistItem.rank)
            ).all()
        )
        db.expunge_all()
    return wl, items


def _cards(bridge: Bridge) -> list[EventEnvelope]:
    return [e for e in bridge.of("warroom.message") if e.payload["kind"] == "watchlist"]


def test_market_scout_is_enabled_and_scheduled_at_0800() -> None:
    assert REGISTRY[SLUG].enabled
    hooks = {h.name: h for h in discover_hooks()[SLUG].schedule}
    assert set(hooks) == {"scout_watchlist", "scout_spike_check"}  # ATL-109 added the spike check
    hook = hooks["scout_watchlist"]
    assert hook.daily and hook.time_of_day().isoformat() == "08:00:00"
    assert discover_hooks()[SLUG].on_mention is not None


# -- AC1 ----------------------------------------------------------------------------------------------


async def test_the_0800_hook_asks_news_and_historian_waits_and_publishes_the_watchlist(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(market_scout, "prepare", lambda ctx, day: _prepared(now))
    bridge = Bridge(engine, now, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    client = fake.FakeClaudeClient([_answer(now, [("SOL-USDT-SWAP", 0.72), ("XAU-USDT-SWAP", 0.6)])])
    sup = _supervisor(engine, bridge, client, now, tmp_path)

    (result,) = await sup.tick(now)

    assert result.status == "ok"
    mentions = [e for e in bridge.of("warroom.message") if e.payload.get("mentions")]
    assert [e.payload["mentions"] for e in mentions] == [["news_intelligence"], ["historian"]]
    day = (now + timedelta(hours=3)).date()
    for m in mentions:
        assert m.payload["mention_depth"] == 1 and m.correlation_id == f"scout_watchlist:{day.isoformat()}"
    assert "XAU-USDT-SWAP" in mentions[0].payload["content_md"]  # the instrument with news
    # Both replies reached the model.
    (prompt,) = client.queries
    assert "NEWS_INTELLIGENCE-REPLY-MARKER" in prompt and "HISTORIAN-REPLY-MARKER" in prompt

    wl, items = _saved(engine, now)
    (card_env,) = _cards(bridge)
    card = card_env.payload["card"]
    assert wl.status == "published" == card["status"] and wl.created_by == SLUG
    assert card["watchlist_id"] == str(wl.id) and card["version"] == wl.version
    assert (
        [(i.inst_id, i.rank, float(i.conviction), i.bias) for i in items]
        == [(p["inst_id"], p["rank"], p["conviction"], p["bias"]) for p in card["picks"]]
        == [("SOL-USDT-SWAP", 1, 0.72, "long"), ("XAU-USDT-SWAP", 2, 0.6, "long")]
    )
    assert wl.edit_window_ends_at == wl.created_at + timedelta(minutes=10)
    assert len(card["backdrop"]) == 3 and len(card["rejected"]) == 3
    assert {t["agent"]: t["status"] for t in card["evidence"]["teammates"]} == {
        "news_intelligence": "replied",
        "historian": "replied",
    }
    assert wl.message_id is not None  # linked to the card the bridge stored
    assert card["degraded_asset_classes"] == []
    assert not any("cap_reason" in p or "conviction_before_cap" in p for p in card["picks"])

    # A second tick the same day starts nothing.
    assert await sup.tick(now + timedelta(minutes=1)) == []


# -- AC2 ----------------------------------------------------------------------------------------------


async def test_a_silent_historian_makes_the_watchlist_partial_with_convictions_capped(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(market_scout, "prepare", lambda ctx, day: _prepared(now))
    bridge = Bridge(engine, now, answer={"news_intelligence"})
    cleanup.append(bridge)
    client = fake.FakeClaudeClient(
        [_answer(now, [("SOL-USDT-SWAP", 0.9), ("XAU-USDT-SWAP", 0.7), ("TSLA-USDT-SWAP", 0.4)])]
    )
    sup = _supervisor(engine, bridge, client, now, tmp_path)

    (result,) = await sup.tick(now)

    assert result.status == "ok"
    wl, items = _saved(engine, now)
    (card_env,) = _cards(bridge)
    card = card_env.payload["card"]
    assert wl.status == "partial_evidence" == card["status"]
    assert items and all(i.conviction <= Decimal("0.5") for i in items)
    assert all(p["conviction"] <= 0.5 for p in card["picks"])
    assert [float(i.conviction) for i in items] == [0.5, 0.5, 0.4]
    teammates = {t["agent"]: t["status"] for t in card["evidence"]["teammates"]}
    assert teammates == {"news_intelligence": "replied", "historian": "timed_out"}
    assert "historian" in (wl.reason or "")
    assert {c["inst_id"] for c in card["corrections"] if c["rule"] == "conviction_cap"} == {
        "SOL-USDT-SWAP",
        "XAU-USDT-SWAP",
    }
    assert "Partial evidence" in card_env.payload["content_md"]
    caps = {p["inst_id"]: (p.get("conviction_before_cap"), p.get("cap_reason")) for p in card["picks"]}
    assert caps == {
        "SOL-USDT-SWAP": (0.9, "partial_evidence"),
        "XAU-USDT-SWAP": (0.7, "partial_evidence"),
        "TSLA-USDT-SWAP": (None, None),  # 0.4 was under the cap
    }


async def test_every_crypto_news_source_down_caps_only_crypto_picks(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(market_scout, "prepare", lambda ctx, day: _prepared(now, degraded=["crypto"]))
    bridge = Bridge(engine, now, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    client = fake.FakeClaudeClient([_answer(now, [("SOL-USDT-SWAP", 0.8), ("XAU-USDT-SWAP", 0.7)])])

    (result,) = await _supervisor(engine, bridge, client, now, tmp_path).tick(now)

    assert result.status == "ok"
    (prompt,) = client.queries
    assert "EVERY NEWS SOURCE IS DOWN for: crypto" in prompt
    wl, items = _saved(engine, now)
    (card_env,) = _cards(bridge)
    card = card_env.payload["card"]
    assert wl.status == "published" == card["status"]  # both teammates replied, table fresh
    assert card["degraded_asset_classes"] == ["crypto"]
    assert [(i.inst_id, float(i.conviction)) for i in items] == [
        ("SOL-USDT-SWAP", 0.5),
        ("XAU-USDT-SWAP", 0.7),
    ]
    sol, xau = card["picks"]
    assert (sol["conviction"], sol["conviction_before_cap"], sol["cap_reason"]) == (
        0.5,
        0.8,
        "news_class_down",
    )
    assert xau["conviction"] == 0.7 and "cap_reason" not in xau and "conviction_before_cap" not in xau
    assert "News down" in card_env.payload["content_md"]


async def test_a_stale_feature_table_is_partial_evidence_too(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        market_scout, "prepare", lambda ctx, day: _prepared(now, table_age=timedelta(minutes=16))
    )
    bridge = Bridge(engine, now, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    client = fake.FakeClaudeClient([_answer(now, [("SOL-USDT-SWAP", 0.8)])])
    await _supervisor(engine, bridge, client, now, tmp_path).tick(now)
    wl, items = _saved(engine, now)
    assert wl.status == "partial_evidence" and [float(i.conviction) for i in items] == [0.5]
    assert "feature table" in (wl.reason or "")


# -- AC3 ----------------------------------------------------------------------------------------------


async def test_an_empty_shortlist_posts_no_watchlist_today_without_a_model_call(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rejected = [
        Rejected(
            inst_id="PEPE-USDT-SWAP", market="crypto_perp", reasons=["volume"], detail=["24h volume too low"]
        ),
        Rejected(
            inst_id="XAU-USDT-SWAP",
            market="commodity_perp",
            reasons=["volume", "state"],
            detail=["low", "suspend"],
        ),
    ]
    monkeypatch.setattr(market_scout, "prepare", lambda ctx, day: _prepared(now, rows=[], rejected=rejected))
    bridge = Bridge(engine, now, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    client = fake.FakeClaudeClient([])
    sup = _supervisor(engine, bridge, client, now, tmp_path)

    assert await sup.tick(now) == []

    assert client.queries == []
    assert not [e for e in bridge.of("warroom.message") if e.payload.get("mentions")]
    (card_env,) = _cards(bridge)
    card = card_env.payload["card"]
    assert card["status"] == "empty" and card["picks"] == []
    assert "No watchlist today" in card_env.payload["content_md"]
    assert "2 rejected" in card["reason"] and "volume 2" in card["reason"] and "state 1" in card["reason"]
    wl, items = _saved(engine, now)
    assert wl.status == "empty" and items == [] and wl.reason == card["reason"]
    # Not posted again at the next tick.
    assert await sup.tick(now + timedelta(seconds=30)) == []
    assert len(_cards(bridge)) == 1


# -- AC4 ----------------------------------------------------------------------------------------------


async def test_three_correlated_picks_are_retried_once_and_a_blocked_pick_is_dropped(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pairs = [
        ("BTC-USDT-SWAP", "ETH-USDT-SWAP", 0.92),
        ("ETH-USDT-SWAP", "SOL-USDT-SWAP", 0.86),
        ("BTC-USDT-SWAP", "SOL-USDT-SWAP", 0.88),
    ]
    monkeypatch.setattr(
        market_scout, "prepare", lambda ctx, day: _prepared(now, pairs=pairs, blocklist=["DOGE-USDT-SWAP"])
    )
    bridge = Bridge(engine, now, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    client = fake.FakeClaudeClient(
        [
            _answer(now, [("BTC-USDT-SWAP", 0.7), ("ETH-USDT-SWAP", 0.65), ("SOL-USDT-SWAP", 0.6)]),
            _answer(now, [("BTC-USDT-SWAP", 0.7), ("ETH-USDT-SWAP", 0.65), ("DOGE-USDT-SWAP", 0.6)]),
        ]
    )
    sup = _supervisor(engine, bridge, client, now, tmp_path)

    (result,) = await sup.tick(now)

    assert result.status == "ok" and result.attempts == 2
    assert len(client.queries) == 2 and "correlation rule" in client.queries[1]
    _wl, items = _saved(engine, now)
    assert [i.inst_id for i in items] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    (card_env,) = _cards(bridge)
    card = card_env.payload["card"]
    assert [p["inst_id"] for p in card["picks"]] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert {"rule": "blocked", "inst_id": "DOGE-USDT-SWAP", "detail": "on the blocklist"} in card[
        "corrections"
    ]
    assert card["correlation_refused"] == [["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]]


async def test_correlated_picks_repeated_after_the_retry_are_trimmed_from_the_lowest_rank(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pairs = [("BTC-USDT-SWAP", "ETH-USDT-SWAP", 0.92), ("ETH-USDT-SWAP", "SOL-USDT-SWAP", 0.86)]
    monkeypatch.setattr(market_scout, "prepare", lambda ctx, day: _prepared(now, pairs=pairs))
    bridge = Bridge(engine, now, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    same = [("BTC-USDT-SWAP", 0.7), ("ETH-USDT-SWAP", 0.65), ("SOL-USDT-SWAP", 0.6)]
    client = fake.FakeClaudeClient([_answer(now, same), _answer(now, same)])
    await _supervisor(engine, bridge, client, now, tmp_path).tick(now)
    _wl, items = _saved(engine, now)
    assert [i.inst_id for i in items] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]


async def test_an_invalid_answer_twice_degrades_the_run_and_saves_nothing(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(market_scout, "prepare", lambda ctx, day: _prepared(now))
    bridge = Bridge(engine, now, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    bad = [fake.assistant_text("no card"), fake.result(tokens_in=10, tokens_out=5)]
    client = fake.FakeClaudeClient([bad, bad])
    (result,) = await _supervisor(engine, bridge, client, now, tmp_path).tick(now)
    assert result.status == "degraded" and len(client.queries) == 2
    assert _cards(bridge) == []
    day = (now + timedelta(hours=3)).date()
    with Session(engine) as db:
        assert (
            db.scalars(select(Watchlist).where(Watchlist.env == ENV, Watchlist.date == day)).first() is None
        )


# -- pure rules ---------------------------------------------------------------------------------------


def test_correlated_groups_join_picks_and_open_positions() -> None:
    pairs = [Pair("A", "B", 0.9), Pair("B", "H", 0.85), Pair("C", "D", 0.5)]
    assert correlated_groups(["A", "B", "C", "D", "H"], pairs, 0.8) == [["A", "B", "H"]]
    # 0.8 exactly is not above the threshold.
    assert correlated_groups(["A", "B"], [Pair("A", "B", 0.8)], 0.8) == []


def _wp(inst_id: str, rank: int, conviction: float = 0.6) -> WatchlistPick:
    return WatchlistPick.model_validate(_pick(inst_id, rank, conviction, datetime(2034, 1, 1, tzinfo=UTC)))


def test_trimming_never_drops_an_owner_pin_and_respects_open_positions() -> None:
    picks = [_wp("A", 1), _wp("B", 2)]
    pairs = [Pair("A", "B", 0.9), Pair("A", "H", 0.9)]
    kept, dropped = trim_correlated(picks, ["H"], pairs, 0.8, 2, keep=["B"])
    assert [p.inst_id for p in kept] == ["B"] and dropped == ["A"]


def test_enforce_drops_blocked_and_removed_carries_pins_and_caps() -> None:
    pin = _wp("PIN", 9, 0.0)
    out = enforce(
        [_wp("A", 1, 0.9), _wp("BAD", 2), _wp("GONE", 3), _wp("ELSEWHERE", 4)],
        blocklist=["bad"],
        removed=["GONE"],
        shortlist_ids=["A", "BAD", "GONE", "PIN"],
        pinned_items=[pin],
        held_ids=[],
        pairs=[],
        threshold=0.8,
        max_members=2,
        partial=True,
        cap=0.5,
    )
    assert [(p.inst_id, p.rank, p.conviction) for p in out.picks] == [("A", 1, 0.5), ("PIN", 2, 0.0)]
    assert out.pinned == ["PIN"]
    assert {(c.rule, c.inst_id) for c in out.corrections} == {
        ("blocked", "BAD"),
        ("removed_by_owner", "GONE"),
        ("not_in_shortlist", "ELSEWHERE"),
        ("pinned", "PIN"),
        ("conviction_cap", "A"),
    }


def test_empty_reason_counts_the_filter_checks() -> None:
    now = datetime(2034, 1, 1, 5, tzinfo=UTC)
    rej = [Rejected(inst_id="X", market="spot", reasons=["age"], detail=["new"])]
    reason = market_scout.empty_reason(_prepared(now, rows=[], rejected=rej).table)
    assert reason == "No instrument passed the universe filter: 1 rejected (by check: age 1)."


def test_partial_evidence_wins_over_news_class_down_and_other_classes_keep_their_conviction() -> None:
    def common(**kw: Any) -> Any:
        return enforce(
            [_wp("A", 1, 0.9), _wp("X", 2, 0.8)],
            blocklist=[],
            removed=[],
            shortlist_ids=["A", "X"],
            pinned_items=[],
            held_ids=[],
            pairs=[],
            threshold=0.8,
            max_members=2,
            cap=0.5,
            degraded_classes=["crypto"],
            asset_class_of={"A": "crypto", "X": "commodity"},
            **kw,
        )

    news_only = common(partial=False)
    assert [(p.inst_id, p.conviction) for p in news_only.picks] == [("A", 0.5), ("X", 0.8)]
    assert {k: (c.conviction_before_cap, c.cap_reason) for k, c in news_only.caps.items()} == {
        "A": (0.9, "news_class_down")
    }
    both = common(partial=True)
    assert {k: (c.conviction_before_cap, c.cap_reason) for k, c in both.caps.items()} == {
        "A": (0.9, "partial_evidence"),
        "X": (0.8, "partial_evidence"),
    }


def test_prepare_takes_degraded_classes_from_news_intelligence(
    engine: Engine, now: datetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prepare() asks news_intelligence.degraded_asset_classes (one rule for News and Scout)."""
    table = _prepared(now).table
    asked: list[ToolContext] = []

    def degraded(ctx: ToolContext) -> list[str]:
        asked.append(ctx)
        return ["crypto"]

    monkeypatch.setattr(news_intelligence, "degraded_asset_classes", degraded)
    monkeypatch.setattr(market_scout, "_reusable_table", lambda session, ctx: (table, "report"))
    monkeypatch.setattr(market_scout, "shortlist_correlations", lambda ctx, shortlist: shortlist)
    ctx = ToolContext(
        env=ENV,
        session_factory=lambda: Session(engine),
        agent=SLUG,
        correlation_id="test",
        clock=lambda: now,
        publish=lambda stream, envelope: None,
    )
    prepared = market_scout.prepare(ctx, (now + timedelta(hours=3)).date())
    assert prepared.degraded_asset_classes == ["crypto"] and asked == [ctx]
    assert {r.inst_id for r in prepared.shortlist.rows} == set(INSTS)


# == ATL-109: intraday re-runs ========================================================================

NOON = timedelta(hours=4)  # _now() is 08:00 Baghdad; +4h = 12:00 Baghdad


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _rerun_supervisor(
    engine: Engine,
    bridge: Bridge,
    client: fake.FakeClaudeClient,
    clock: Clock,
    tmp_path: Path,
    *,
    only_spike_hook: bool = False,
) -> Supervisor:
    hooks = discover_hooks()[SLUG]
    if only_spike_hook:  # keep the 08:00 job out of tick(): these tests run after 08:00
        spike = tuple(h for h in hooks.schedule if h.name == market_scout.SPIKE_JOB)
        hooks = AgentHooks(slug=SLUG, schedule=spike)
    return Supervisor(
        env=ENV,
        engine=engine,
        registry={SLUG: dataclasses.replace(REGISTRY[SLUG], enabled=True)},
        client_factory=fake.factory(client),
        publisher=bridge,
        prompts_dir=tmp_path,
        clock=clock,
        hooks={SLUG: hooks},
    )


ALERT_HEADLINE = "Fed announces emergency rate cut"


def _alert(bridge: Bridge, importance: int) -> tuple[EventEnvelope, str]:
    """An ingest news.alert and its War Room row id (stored the way atlas:bridge does)."""
    envelope = EventEnvelope(
        type="news.alert",
        env=ENV,
        source="ingest",
        correlation_id=f"news-{uuid.uuid4()}",
        payload={"headline": ALERT_HEADLINE, "importance": importance, "inst_ids": ["BTC-USDT-SWAP"]},
    )
    row = bridge._store(
        event_id=str(envelope.id),
        author="ingest",
        kind="news_alert",
        content=ALERT_HEADLINE,
        card=None,
        reply_to=None,
    )
    return envelope, row


def _seed_v1(engine: Engine, now: datetime, picks: list[str], pinned: list[str]) -> Watchlist:
    """Version 1 of the test day's watchlist, saved at 08:01 Baghdad."""
    day = (now + timedelta(hours=3)).date()
    with Session(engine) as db:
        wl = save_watchlist(
            db,
            ENV,
            day,
            [_pick(i, n, 0.6, now) for n, i in enumerate(picks, start=1)],
            "published",
            data={"trigger": "schedule"},
            created_at=now + timedelta(minutes=1),
        )
        for item in db.scalars(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id)).all():
            item.pinned_by_owner = item.inst_id in pinned
        db.commit()
        db.refresh(wl)
        db.expunge(wl)
    return wl


def _cards_of(bridge: Bridge) -> list[dict[str, Any]]:
    return [e.payload for e in _cards(bridge)]


def _closes(end: datetime, n: int, last_return: float, step: float = 0.01) -> pd.Series[float]:
    """n + 1 hourly closes ending at ``end``: returns alternate +-step, the last one is last_return."""
    price = 100.0
    values = [price]
    for k in range(1, n):
        price *= 1 + (step if k % 2 else -step)
        values.append(price)
    values.append(price * (1 + last_return))
    index = pd.DatetimeIndex([end - timedelta(hours=n - k) for k in range(n + 1)])
    return pd.Series(values, index=index)


# -- pure rules ---------------------------------------------------------------------------------------


def test_the_rerun_window_is_0810_to_2300_baghdad() -> None:
    day = datetime(2034, 3, 1, tzinfo=UTC)  # Baghdad = UTC+3
    assert not rerun.in_window(day.replace(hour=5, minute=9))  # 08:09
    assert rerun.in_window(day.replace(hour=5, minute=10))  # 08:10
    assert rerun.in_window(day.replace(hour=19, minute=59))  # 22:59
    assert not rerun.in_window(day.replace(hour=20))  # 23:00
    assert not rerun.in_window(day.replace(hour=23))  # 02:00 the next day


def test_only_importance_5_alerts_qualify() -> None:
    assert rerun.alert_qualifies({"importance": 5})
    assert not rerun.alert_qualifies({"importance": 4})
    assert not rerun.alert_qualifies({"importance": None})
    assert not rerun.alert_qualifies({"importance": True})


def test_measure_spike_divides_the_last_return_by_the_previous_stdev() -> None:
    end = datetime(2034, 3, 1, 8, tzinfo=UTC)
    measured = rerun.measure_spike(_closes(end, 720, 0.035), end, lookback=720, minimum=100)
    assert measured is not None
    sigma, ret, stdev, n = measured
    assert ret == pytest.approx(0.035) and stdev == pytest.approx(0.01, rel=1e-3) and n == 719
    assert sigma == pytest.approx(3.5, rel=1e-3)
    # A feed that is behind (the last bar is not the latest closed one), or too short a history: None.
    late = end + timedelta(hours=1)
    assert rerun.measure_spike(_closes(end, 720, 0.035), late, lookback=720, minimum=100) is None
    assert rerun.measure_spike(_closes(end, 50, 0.035), end, lookback=720, minimum=100) is None


def test_the_spike_check_is_an_interval_hook() -> None:
    hook = next(h for h in discover_hooks()[SLUG].schedule if h.name == market_scout.SPIKE_JOB)
    assert not hook.daily and hook.every_seconds == rerun.check_seconds() == 300
    assert news_intelligence.ALERT_EVENT in discover_hooks()[SLUG].on_event


# -- AC1: importance-5 alert at 12:00 -----------------------------------------------------------------


async def test_an_importance_5_alert_at_noon_schedules_one_rerun_and_importance_4_none(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    noon = now + NOON
    monkeypatch.setattr(market_scout, "prepare", lambda ctx, day: _prepared(noon))
    bridge = Bridge(engine, noon, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    client = fake.FakeClaudeClient([_answer(noon, [("SOL-USDT-SWAP", 0.7)])])
    sup = _rerun_supervisor(engine, bridge, client, Clock(noon), tmp_path)

    four, _row = _alert(bridge, importance=4)
    assert await sup.handle_envelope(four) == []
    await market_scout.settled()
    assert client.queries == [] and _cards(bridge) == []

    five, _row = _alert(bridge, importance=5)
    await sup.handle_envelope(five)
    await market_scout.settled()

    assert len(client.queries) == 1
    assert "INTRADAY RE-RUN" in client.queries[0]
    assert ALERT_HEADLINE not in client.queries[0]  # the untrusted headline never reaches the model
    (card,) = [p["card"] for p in _cards_of(bridge)]
    assert card["trigger"]["kind"] == "news_alert" and card["trigger"]["importance"] == 5


# -- AC2: a 3.5-sigma 1H move -------------------------------------------------------------------------


@pytest.fixture
def candles(engine: Engine) -> Iterator[list[str]]:
    seeded: list[str] = []
    yield seeded
    with engine.begin() as conn:
        for inst in seeded:
            conn.execute(text("DELETE FROM candles WHERE inst_id = :i"), {"i": inst})


def _seed_spike(engine: Engine, inst_id: str, at: datetime, last_return: float, seeded: list[str]) -> None:
    """31 days of confirmed 1H bars alternating +-1 %, the latest closed bar moving ``last_return``."""
    closes = _closes(rerun.latest_closed_bar(at), 31 * 24, last_return)
    rows = [
        {
            "env": ENV,
            "inst_id": inst_id,
            "bar": "1H",
            "ts": ts.to_pydatetime(),
            "o": Decimal(str(c)),
            "h": Decimal(str(c)),
            "l": Decimal(str(c)),
            "c": Decimal(str(c)),
            "vol": Decimal(1),
            "confirmed": True,
        }
        for ts, c in closes.items()
    ]
    with engine.begin() as conn:
        conn.execute(insert(Candle), rows)
    seeded.append(inst_id)


async def test_a_3_5_sigma_move_off_the_watchlist_reruns_and_on_the_watchlist_does_not(
    engine: Engine,
    now: datetime,
    cleanup: list[Bridge],
    candles: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noon = now + NOON
    tag = uuid.uuid4().hex[:6].upper()
    listed, other = f"WL{tag}-USDT-SWAP", f"SPK{tag}-USDT-SWAP"
    _seed_v1(engine, now, [listed], pinned=[])
    _seed_spike(engine, listed, noon, 0.035, candles)
    universe = [listed]

    def build_universe(ctx: ToolContext) -> SimpleNamespace:
        return SimpleNamespace(kept=[SimpleNamespace(inst_id=i) for i in universe])

    monkeypatch.setattr(market_scout, "build_universe", build_universe)
    monkeypatch.setattr(market_scout, "prepare", lambda ctx, day: _prepared(noon))
    bridge = Bridge(engine, noon, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    client = fake.FakeClaudeClient([_answer(noon, [("SOL-USDT-SWAP", 0.7)])])
    sup = _rerun_supervisor(engine, bridge, client, Clock(noon), tmp_path, only_spike_hook=True)

    # Only the watchlist instrument moved 3.5 sigma: nothing.
    assert await sup.tick(noon) == []
    await market_scout.settled()
    assert client.queries == [] and _cards(bridge) == []

    # The same move on an instrument that is not on the watchlist: one re-run.
    _seed_spike(engine, other, noon, 0.035, candles)
    universe.append(other)
    await _rerun_supervisor(engine, bridge, client, Clock(noon), tmp_path, only_spike_hook=True).tick(noon)
    await market_scout.settled()

    assert len(client.queries) == 1
    (card,) = [p["card"] for p in _cards_of(bridge)]
    trigger = card["trigger"]
    assert set(trigger) == {"kind", "inst_id", "sigma", "bar", "bar_ts", "at"}
    assert trigger["kind"] == "vol_spike" and trigger["inst_id"] == other and trigger["bar"] == "1H"
    assert trigger["sigma"] == pytest.approx(3.5, abs=0.01)
    bar_close = rerun.latest_closed_bar(noon) + timedelta(hours=1)
    assert trigger["at"] == bar_close.isoformat()
    assert card["version"] == 2
    assert set(card["changes"]) == {"since", "version", "added", "dropped"}
    assert card["changes"]["added"] == ["SOL-USDT-SWAP"] and card["changes"]["dropped"] == [listed]


# -- AC3: debounce and the night ----------------------------------------------------------------------


async def test_two_triggers_20_minutes_apart_rerun_once_and_02_00_runs_none(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    noon = now + NOON
    monkeypatch.setattr(market_scout, "prepare", lambda ctx, day: _prepared(noon))
    bridge = Bridge(engine, noon, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    client = fake.FakeClaudeClient([_answer(noon, [("SOL-USDT-SWAP", 0.7)]), _answer(noon, [])])
    clock = Clock(noon - timedelta(hours=10))  # 02:00 Baghdad the same day
    sup = _rerun_supervisor(engine, bridge, client, clock, tmp_path)

    await sup.handle_envelope(_alert(bridge, importance=5)[0])
    await market_scout.settled()
    assert client.queries == [] and _cards(bridge) == []

    clock.at = noon
    await sup.handle_envelope(_alert(bridge, importance=5)[0])
    await market_scout.settled()
    clock.at = noon + timedelta(minutes=20)
    await sup.handle_envelope(_alert(bridge, importance=5)[0])
    await market_scout.settled()
    assert len(client.queries) == 1 and len(_cards(bridge)) == 1

    # A restarted supervisor still sees the saved re-run: the debounce is not only in memory.
    restarted = _rerun_supervisor(engine, bridge, client, clock, tmp_path)
    await restarted.handle_envelope(_alert(bridge, importance=5)[0])
    await market_scout.settled()
    assert len(client.queries) == 1


# -- AC4 + card shapes: version 2 keeps owner pins and names the trigger -------------------------------


async def test_the_rerun_saves_version_2_with_owner_pins_and_a_card_naming_the_trigger(
    engine: Engine, now: datetime, cleanup: list[Bridge], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    noon = now + NOON
    v1 = _seed_v1(engine, now, ["SOL-USDT-SWAP", "TSLA-USDT-SWAP"], pinned=["TSLA-USDT-SWAP"])

    def prepare(ctx: ToolContext, day: Any) -> market_scout.Prepared:
        with ctx.session_factory() as session:
            pinned, removed, version = market_scout._owner_edits(session, ctx, day)
        return dataclasses.replace(
            _prepared(noon), pinned_items=pinned, removed=removed, previous_version=version
        )

    monkeypatch.setattr(market_scout, "prepare", prepare)
    bridge = Bridge(engine, noon, answer={"news_intelligence", "historian"})
    cleanup.append(bridge)
    client = fake.FakeClaudeClient([_answer(noon, [("ETH-USDT-SWAP", 0.6)])])
    sup = _rerun_supervisor(engine, bridge, client, Clock(noon), tmp_path)

    alert, alert_row = _alert(bridge, importance=5)
    await sup.handle_envelope(alert)
    await market_scout.settled()

    wl, items = _saved(engine, now)
    assert wl.version == 2 and v1.version == 1
    assert [(i.inst_id, i.pinned_by_owner) for i in items] == [
        ("ETH-USDT-SWAP", False),
        ("TSLA-USDT-SWAP", True),
    ]
    (payload,) = _cards_of(bridge)
    card = payload["card"]
    assert card["version"] == 2 and card["previous_version"] == 1
    assert card["correlation_id"].startswith("scout_rerun:news_alert:")
    assert card["trigger"] == {
        "kind": "news_alert",
        "message_id": alert_row,
        "event_id": str(alert.id),
        "headline": ALERT_HEADLINE,
        "importance": 5,
        "at": alert.ts.astimezone(UTC).isoformat(),
    }
    assert card["changes"] == {
        "since": v1.created_at.astimezone(UTC).isoformat(),
        "version": 1,
        "added": ["ETH-USDT-SWAP"],
        "dropped": ["SOL-USDT-SWAP"],
    }
    assert wl.data["trigger"] == card["trigger"] and wl.data["changes"] == card["changes"]
    assert payload["content_md"].startswith(
        f"**Intraday re-run** (importance-5 news alert: {ALERT_HEADLINE}). "
        "Added: ETH-USDT-SWAP. Dropped: SOL-USDT-SWAP."
    )
    with Session(engine) as db:
        triggers = db.scalars(
            select(AgentRun.trigger).where(
                AgentRun.agent == SLUG,
                AgentRun.correlation_id == card["correlation_id"],
            )
        ).all()
    assert list(triggers) == ["alert"]
