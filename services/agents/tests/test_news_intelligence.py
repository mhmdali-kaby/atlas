"""ATL-107: News Intelligence answers @News, posts the 07:30 morning brief once per Baghdad day and
analyses ingest's news.alert, rate-limited. Every run uses a FakeClaudeClient script.

DB tests need this checkout's test DB migrated (`cd backend && php artisan test` first).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents import mentions
from atlas_agents import news_intelligence as news
from atlas_agents import testing as fake
from atlas_agents.agents import market_scout
from atlas_agents.agents import news_intelligence as hooks_module
from atlas_agents.registry import REGISTRY, discover_hooks
from atlas_agents.schemas import NewsBrief
from atlas_agents.scout.features import FeatureTable
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools import ToolContext
from atlas_shared.db import (
    AgentRun,
    AgentState,
    AgentUsageDaily,
    EconomicEvent,
    NewsCluster,
    NewsItem,
    NewsSource,
)
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

ENV = "demo"
SLUG = "news_intelligence"
RUN = uuid.uuid4().hex[:10]
CRYPTO_CAT = "crypto_news"
MARKETS_CAT = "markets_news"
REPUTABLE = f"test.{RUN}.coindesk"
OTHER_CRYPTO = f"test.{RUN}.decrypt"
MARKETS = f"test.{RUN}.cnbc"
INST = f"N{RUN}-USDT-SWAP"
NOW = datetime(2026, 10, 30, 4, 30, tzinfo=UTC)  # 07:30 Asia/Baghdad
PUBLISHED = NOW - timedelta(hours=2)


@pytest.fixture(autouse=True)
def no_real_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a test constructed the real ClaudeSDKClient")

    monkeypatch.setattr("atlas_agents.session.ClaudeSDKClient", refuse)


def _wipe_runs(engine: Engine) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent.in_([SLUG, "historian"]), model.env == ENV))


@pytest.fixture
def db(engine: Engine) -> Iterator[Engine]:
    """Three sources (two crypto, one markets). Every other news_sources row of those categories is
    disabled for the test and restored after, so only these three decide what is degraded."""
    _wipe_runs(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        others = list(s.scalars(select(NewsSource).where(NewsSource.category.in_([CRYPTO_CAT, MARKETS_CAT]))))
        was_enabled = {row.key: row.enabled for row in others}
        last_success = {row.key: row.last_success_at for row in others}
        for row in others:
            row.enabled = False
            row.last_success_at = None  # only this test's sources decide degraded_since (ATL-120)
        for key, category, reputable in (
            (REPUTABLE, CRYPTO_CAT, True),
            (OTHER_CRYPTO, CRYPTO_CAT, False),
            (MARKETS, MARKETS_CAT, True),
        ):
            s.add(
                NewsSource(
                    key=key,
                    provider=f"test{RUN}",
                    kind="rss",
                    category=category,
                    name=key,
                    poll_seconds=300,
                    reputable=reputable,
                    official=False,
                )
            )
        s.commit()
    yield engine
    with engine.begin() as conn:
        conn.execute(delete(NewsItem).where(NewsItem.url.like(f"%{RUN}%")))
        conn.execute(delete(NewsCluster).where(NewsCluster.headline.like(f"%{RUN}%")))
        conn.execute(delete(NewsSource).where(NewsSource.key.like(f"test.{RUN}.%")))
        conn.execute(delete(EconomicEvent).where(EconomicEvent.source == f"test.{RUN}"))
        conn.execute(text("DELETE FROM warroom_threads WHERE key LIKE :k"), {"k": f"atl107-{RUN}%"})
    with factory() as s:
        for row in s.scalars(select(NewsSource).where(NewsSource.key.in_(list(was_enabled)))):
            row.enabled = was_enabled[row.key]
            row.last_success_at = last_success[row.key]
        s.commit()
    _wipe_runs(engine)


def _cluster(engine: Engine, importance: int = 4) -> str:
    with Session(engine) as s:
        cluster = NewsCluster(
            headline=f"Exchange lists token {RUN}",
            first_seen=PUBLISHED,
            last_seen=PUBLISHED,
            item_count=1,
            max_importance=importance,
            instruments=[INST],
        )
        s.add(cluster)
        s.flush()
        s.add(
            NewsItem(
                source=REPUTABLE,
                url=f"https://news.test/{RUN}/1",
                canonical_url=f"https://news.test/{RUN}/1",
                title=f"Exchange lists token {RUN}",
                summary="A listing.",
                published_at=PUBLISHED,
                fetched_at=PUBLISHED + timedelta(minutes=1),
                importance=importance,
                sentiment=Decimal("0.4"),
                instruments=[INST],
                cluster_id=cluster.id,
                created_at=PUBLISHED,
                updated_at=PUBLISHED,
            )
        )
        s.commit()
        return str(cluster.id)


def _supervisor(
    engine: Engine,
    client: fake.FakeClaudeClient,
    rec: Recorder,
    *,
    now: datetime = NOW,
    agents: tuple[str, ...] = (SLUG,),
) -> Supervisor:
    return Supervisor(
        env=ENV,
        engine=engine,
        registry={slug: REGISTRY[slug] for slug in agents},
        client_factory=fake.factory(client),
        publisher=rec,
        clock=lambda: now,
    )


def _card(cluster_id: str, **changes: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "news_brief",
        "inst_ids": [INST],
        "clusters": [
            {
                "cluster_id": cluster_id,
                "headline": "Exchange lists token",
                # The model claims CONFIRMED; one reputable source makes it REPORTED in code.
                "status": "CONFIRMED",
                "sources": [
                    {"name": "CoinDesk", "url": f"https://news.test/{RUN}/1", "published_at": _iso(PUBLISHED)}
                ],
                "event_type": "listing",
                "importance": 4,
                "sentiment": 0.4,
                "first_seen": _iso(PUBLISHED),
            }
        ],
        "upcoming_events": [],
        "net_sentiment_24h": 0.4,
        "net_sentiment_72h": None,
        "priced_in": "partly",
        "what_to_watch": "Follow-through after the listing.",
        "data_as_of": _iso(NOW),
    }
    card.update(changes)
    return card


def _iso(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")


def _search_output(cluster_id: str) -> dict[str, Any]:
    return {
        "as_of": _iso(NOW),
        "source": "atlas.news_items",
        "truncated": False,
        "data": {
            "items": [
                {
                    "id": "x",
                    "url": f"https://news.test/{RUN}/1",
                    "importance": 4,
                    "sentiment": 0.4,
                    "cluster_id": cluster_id,
                }
            ],
            "clusters": [{"cluster_id": cluster_id, "status": "REPORTED"}],
        },
    }


def _attempt(
    cluster_id: str, text_md: str = "REPORTED: the token lists; sentiment 0.4.", **changes: Any
) -> list[Any]:
    return [
        fake.tool_use("mcp__news__search_news", {"inst_ids": [INST]}),
        fake.tool_result(json.dumps(_search_output(cluster_id))),
        fake.assistant_text(f"{text_md}\n\n```json\n{json.dumps(_card(cluster_id, **changes))}\n```"),
        fake.result(tokens_in=500, tokens_out=120, turns=2),
    ]


def _owner_message() -> EventEnvelope:
    return EventEnvelope(
        type="warroom.message",
        env=ENV,
        source="human:owner",
        payload={
            "thread": {"kind": "main", "key": "main"},
            "thread_id": str(uuid.uuid4()),
            "message_id": str(uuid.uuid4()),
            "author_type": "human",
            "author": "Owner",
            "kind": "text",
            "content_md": f"@News what is the news on {INST}?",
            "reply_to": None,
            "mentions": [SLUG],
        },
    )


def _alert(cluster_id: str) -> EventEnvelope:
    return EventEnvelope(
        id=uuid.uuid4(),
        type="news.alert",
        env=ENV,
        source="system:news",
        correlation_id=f"news:{cluster_id}",
        payload={
            "news_item_id": str(uuid.uuid4()),
            "cluster_id": cluster_id,
            "headline": f"Exchange lists token {RUN}",
            "source": REPUTABLE,
            "source_name": "CoinDesk",
            "category": CRYPTO_CAT,
            "url": f"https://news.test/{RUN}/1",
            "published_at": _iso(PUBLISHED),
            "importance": 5,
            "event_type": "listing",
            "inst_ids": [INST],
            "asset_classes": ["crypto"],
            "sentiment": 0.4,
            "novelty": 1.0,
            "reason": "importance",
            "held_inst_ids": [],
        },
    )


def _store_alert(engine: Engine, envelope: EventEnvelope) -> tuple[str, str]:
    """The alert as atlas:bridge stores it: (thread id, row id)."""
    thread_id, row_id = str(uuid.uuid4()), str(uuid.uuid4())
    created = NOW - timedelta(seconds=5)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, 'demo', 'agent', :key, :key, :ts, :ts)"
            ),
            {"id": thread_id, "key": f"atl107-{RUN}-{row_id[:8]}", "ts": created},
        )
        conn.execute(
            text(
                "INSERT INTO warroom_messages (id, env, thread_id, event_id, author_type, author, kind, "
                "content_md, created_at, updated_at) VALUES (:id, 'demo', :thread, :event, 'system', "
                "'system:news', 'news_alert', :content, :ts, :ts)"
            ),
            {
                "id": row_id,
                "thread": thread_id,
                "event": str(envelope.id),
                "content": "headline",
                "ts": created,
            },
        )
    return thread_id, row_id


def _run_end(rec: Recorder) -> list[EventEnvelope]:
    return [e for e in rec.of("warroom.thinking") if e.payload["delta_type"] == "run_end"]


def _runs(engine: Engine, agent: str) -> list[AgentRun]:
    with Session(engine) as db:
        return list(db.scalars(select(AgentRun).where(AgentRun.agent == agent, AgentRun.env == ENV)))


# -- registration ------------------------------------------------------------------------------------


def test_news_intelligence_is_enabled_and_registers_mention_schedule_and_alert_hooks() -> None:
    assert REGISTRY[SLUG].enabled
    hooks = discover_hooks()[SLUG]
    assert hooks.on_mention is hooks_module.answer_mention
    schedule = {h.name: h for h in hooks.schedule}  # registration order follows import order
    assert set(schedule) == {"news_morning_brief", "scoring"}  # scoring: ATL-110
    assert schedule["news_morning_brief"].time_of_day().isoformat() == "07:30:00"
    assert set(hooks.on_event) == {"news.alert"}
    # News Intelligence analyses every alert; the other listeners are Market Scout's code-only
    # intraday re-run trigger (ATL-109, importance 5) and the Portfolio Manager's check of a held
    # instrument (ATL-169); neither starts a News run.
    others = [slug for slug, h in discover_hooks().items() if slug != SLUG and "news.alert" in h.on_event]
    assert others == ["market_scout", "portfolio_manager"]
    assert DEFAULTS["agents"]["news_intelligence"]["alerts_per_hour"] == 6


# -- AC1: @News ------------------------------------------------------------------------------------


async def test_a_news_mention_gets_one_news_brief_reply_in_its_thread(db: Engine) -> None:
    cluster_id = _cluster(db)
    rec = Recorder()
    client = fake.FakeClaudeClient([_attempt(cluster_id)])
    sup = _supervisor(db, client, rec)
    owner = _owner_message()

    (result,) = await sup.handle_envelope(owner)

    assert result.status == "ok"
    assert len(client.queries) == 1 and INST in client.queries[0]
    assert "UTC" in client.queries[0] and "Baghdad" in client.queries[0]
    (reply,) = rec.of("warroom.message")
    p = reply.payload
    assert reply.source == "agent:news_intelligence"
    assert p["kind"] == "research_report" and p["author"] == SLUG
    assert p["reply_to"] == owner.payload["message_id"]
    assert p["thread_id"] == owner.payload["thread_id"] and "thread" not in p
    assert p["run_id"] == str(result.run_id)
    brief = NewsBrief.model_validate(p["card"])
    assert brief.form == "instrument_brief"
    assert brief.degraded_asset_classes == []
    assert brief.top_instruments == []
    (cluster,) = brief.clusters
    # Code's status, not the model's.
    assert cluster.status == "REPORTED" and cluster.status_sources == [REPUTABLE]
    assert brief.meta is not None
    assert "```" not in p["content_md"] and p["content_md"].startswith("REPORTED")
    assert [c["name"] for c in p["tool_calls"]] == ["mcp__news__search_news"]
    (end,) = _run_end(rec)
    assert end.payload["status"] == "ok" and end.payload["run_id"] == p["run_id"]
    (run,) = _runs(db, SLUG)
    assert run.trigger == "mention" and run.status == "ok"


async def test_a_card_naming_an_unknown_cluster_is_retried_once_then_accepted(db: Engine) -> None:
    cluster_id = _cluster(db)
    rec = Recorder()
    unknown = str(uuid.uuid4())
    client = fake.FakeClaudeClient([_attempt(unknown), _attempt(cluster_id)])
    sup = _supervisor(db, client, rec)

    (result,) = await sup.handle_envelope(_owner_message())

    assert result.status == "ok"
    assert len(client.queries) == 2 and unknown in client.queries[1]
    (reply,) = rec.of("warroom.message")
    assert NewsBrief.model_validate(reply.payload["card"]).clusters[0].cluster_id == cluster_id


# -- AC2: 07:30 morning brief ------------------------------------------------------------------------


async def test_the_morning_brief_is_posted_once_per_baghdad_day_even_after_a_restart(db: Engine) -> None:
    assert DEFAULTS["agents"]["schedule"]["news_morning_brief"] == "07:30"
    cluster_id = _cluster(db)
    cpi_at = NOW + timedelta(hours=6)
    with Session(db) as s:
        s.add(
            EconomicEvent(
                source=f"test.{RUN}",
                external_id=f"cpi-{RUN}",
                country="US",
                name="US CPI",
                importance=3,
                scheduled_at=cpi_at,
                created_at=NOW - timedelta(days=1),
                updated_at=NOW - timedelta(days=1),
            )
        )
        s.commit()
    events = [
        {
            "time": _iso(cpi_at),
            "name": "US CPI",
            "forecast": "0.3%",
            "previous": "0.2%",
            "sensitive_instruments": [],
        },
        {"time": _iso(cpi_at + timedelta(minutes=7)), "name": "Unknown", "sensitive_instruments": []},
    ]
    rec = Recorder()
    client = fake.FakeClaudeClient(
        [_attempt(cluster_id, "Morning brief: one listing story.", upcoming_events=events)]
    )

    early = _supervisor(db, client, rec, now=NOW - timedelta(minutes=1))
    assert await early.tick() == []  # 07:29 Baghdad: not yet

    sup = _supervisor(db, client, rec)
    (result,) = await sup.tick()
    assert result.status == "ok"
    (message,) = rec.of("warroom.message")
    p = message.payload
    assert p["kind"] == "research_report" and p["thread"] == {"kind": "main", "key": "main"}
    assert p["reply_to"] is None and p["run_id"] == str(result.run_id)
    brief = NewsBrief.model_validate(p["card"])
    assert brief.form == "morning_brief"
    flow = {f.inst_id: f for f in brief.top_instruments}
    assert flow[INST].stories == 1 and flow[INST].net_sentiment == 0.4
    # Event importance comes from economic_events; an event Atlas does not have stays null.
    assert [(e.name, e.importance) for e in brief.upcoming_events] == [("US CPI", 3), ("Unknown", None)]
    assert p["card"]["top_instruments"][0].keys() >= {"inst_id", "stories", "net_sentiment"}
    # The code-computed context reached the model.
    assert cluster_id in client.queries[0] and "07:30 Baghdad" in client.queries[0]
    (end,) = _run_end(rec)
    assert end.payload["status"] == "ok"
    (run,) = _runs(db, SLUG)
    assert run.trigger == "schedule" and run.correlation_id == "news_morning_brief:2026-10-30"

    # The same supervisor ticking again, and a restarted one at 07:40 and at 23:59, post nothing.
    assert await sup.tick() == []
    for later in (NOW + timedelta(minutes=10), NOW + timedelta(hours=16, minutes=29)):
        restarted = _supervisor(db, fake.FakeClaudeClient([]), rec, now=later)
        assert await restarted.tick() == []
    assert len(rec.of("warroom.message")) == 1

    # The next Baghdad day gets its own brief.
    tomorrow = _supervisor(
        db, fake.FakeClaudeClient([_attempt(cluster_id)]), rec, now=NOW + timedelta(days=1)
    )
    (next_day,) = await tomorrow.tick()
    assert next_day.status == "ok" and len(rec.of("warroom.message")) == 2


async def test_the_morning_context_lists_stories_flow_and_degraded_classes(db: Engine) -> None:
    cluster_id = _cluster(db, importance=5)
    ctx = ToolContext(
        env=ENV, session_factory=sessionmaker(bind=db), agent=SLUG, replay_as_of=NOW, publish=Recorder()
    )
    context = news.build_morning_context(ctx)
    assert cluster_id in [s.cluster_id for s in context.top_stories]
    story = next(s for s in context.top_stories if s.cluster_id == cluster_id)
    assert story.max_importance == 5 and story.sources == [REPUTABLE] and story.instruments == [INST]
    assert "untrusted_source" in (story.headline or "")
    assert INST in [f.inst_id for f in context.top_instruments]
    assert context.degraded_asset_classes == []


# -- AC3: news.alert ---------------------------------------------------------------------------------


async def test_news_alerts_start_news_intelligence_only_and_the_seventh_in_an_hour_is_skipped(
    db: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    cluster_id = _cluster(db)
    rec = Recorder()
    client = fake.FakeClaudeClient([_attempt(cluster_id, "ALERT REPORTED: listing.") for _ in range(6)])
    clock = {"now": NOW}
    sup = Supervisor(
        env=ENV,
        engine=db,
        registry={SLUG: REGISTRY[SLUG], "historian": REGISTRY["historian"]},
        client_factory=fake.factory(client),
        publisher=rec,
        clock=lambda: clock["now"],
    )
    assert set(sup.sessions) == {SLUG, "historian"}

    first = _alert(cluster_id)
    thread_id, row_id = _store_alert(db, first)
    (result,) = await sup.handle_envelope(first)
    assert result.status == "ok"
    (reply,) = rec.of("warroom.message")
    p = reply.payload
    assert p["reply_to"] == row_id and p["thread_id"] == thread_id
    assert p["kind"] == "research_report" and p["run_id"] == str(result.run_id)
    brief = NewsBrief.model_validate(p["card"])
    assert brief.form == "alert_analysis" and brief.clusters[0].status == "REPORTED"
    assert "untrusted_source" in client.queries[0]

    # Alerts not yet stored by the bridge are answered in Main without waiting in this test.
    monkeypatch.setattr(mentions, "ALERT_PARENT_TRIES", 1)
    for minute in range(1, 6):
        clock["now"] = NOW + timedelta(minutes=minute)
        (r,) = await sup.handle_envelope(_alert(cluster_id))
        assert r.status == "ok"
    assert len(client.queries) == 6

    clock["now"] = NOW + timedelta(minutes=30)
    with caplog.at_level(logging.WARNING, logger="atlas_agents.agents.news_intelligence"):
        assert await sup.handle_envelope(_alert(cluster_id)) == []
    assert "alerts_per_hour" in caplog.text
    assert len(client.queries) == 6 and client.remaining == 0

    runs = _runs(db, SLUG)
    assert len(runs) == 6 and {r.trigger for r in runs} == {"alert"}
    assert _runs(db, "historian") == []
    assert len(_run_end(rec)) == 6

    # An hour after the first alert the window has room again.
    client.add_run(_attempt(cluster_id))
    clock["now"] = NOW + timedelta(minutes=61)
    (again,) = await sup.handle_envelope(_alert(cluster_id))
    assert again.status == "ok"


async def test_an_alert_from_another_env_starts_nothing(db: Engine) -> None:
    rec = Recorder()
    client = fake.FakeClaudeClient([])
    sup = _supervisor(db, client, rec)
    other = _alert(str(uuid.uuid4())).model_copy(update={"env": "live"})
    assert await sup.handle_envelope(other) == []
    assert client.queries == []


# -- AC4: degraded asset classes ---------------------------------------------------------------------


async def test_with_every_crypto_source_degraded_the_brief_lists_crypto(db: Engine) -> None:
    cluster_id = _cluster(db)
    with Session(db) as s:
        reputable = s.scalars(select(NewsSource).where(NewsSource.key == REPUTABLE)).one()
        reputable.disabled_until = NOW + timedelta(minutes=20)  # 3 failures: degraded
        reputable.consecutive_failures = 3
        other = s.scalars(select(NewsSource).where(NewsSource.key == OTHER_CRYPTO)).one()
        other.requires_key = True  # keyed with no key: needs_key
        s.commit()
    rec = Recorder()
    client = fake.FakeClaudeClient([_attempt(cluster_id)])
    sup = _supervisor(db, client, rec)

    (result,) = await sup.handle_envelope(_owner_message())

    assert result.status == "ok"
    (reply,) = rec.of("warroom.message")
    brief = NewsBrief.model_validate(reply.payload["card"])
    assert brief.degraded_asset_classes == ["crypto"]
    assert "crypto" in client.queries[0] and "partial" in client.queries[0]


# -- ATL-120: degraded_since ------------------------------------------------------------------------

CRYPTO_LAST_OK = datetime(2026, 10, 30, 3, 10, tzinfo=UTC)


def _crypto_down(db: Engine, last_ok: dict[str, datetime | None]) -> None:
    """Every crypto source degraded (back-off window), with the given last_success_at per key."""
    with Session(db) as s:
        for key in (REPUTABLE, OTHER_CRYPTO):
            row = s.scalars(select(NewsSource).where(NewsSource.key == key)).one()
            row.disabled_until = NOW + timedelta(minutes=45)
            row.consecutive_failures = 3
            row.last_success_at = last_ok.get(key)
        markets = s.scalars(select(NewsSource).where(NewsSource.key == MARKETS)).one()
        markets.last_success_at = NOW - timedelta(minutes=2)  # up: not listed
        s.commit()


def _empty_table() -> FeatureTable:
    return FeatureTable(
        env=ENV,
        as_of=NOW,
        valid_until=NOW + timedelta(minutes=15),
        settings_version=1,
        btc_reference="BTC-USDT-SWAP",
        spy_reference=None,
        shared_catalysts=[],
        rows=[],
        rejected=[],
        notes=[],
    )


async def test_the_morning_brief_and_the_watchlist_carry_the_same_degraded_since(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    cluster_id = _cluster(db)
    _crypto_down(db, {REPUTABLE: CRYPTO_LAST_OK, OTHER_CRYPTO: CRYPTO_LAST_OK - timedelta(hours=5)})
    rec = Recorder()
    # The model tries to write both code-owned fields: Atlas overwrites them.
    client = fake.FakeClaudeClient(
        [
            _attempt(
                cluster_id,
                "Morning brief.",
                degraded_since={"crypto": "2020-01-01T00:00:00Z", "stock": None},
                degraded_asset_classes=["stock"],
            )
        ]
    )

    (result,) = await _supervisor(db, client, rec).tick()

    assert result.status == "ok"
    (message,) = rec.of("warroom.message")
    card = message.payload["card"]
    assert card["form"] == "morning_brief"
    assert card["degraded_asset_classes"] == ["crypto"]
    assert card["degraded_since"] == {"crypto": "2026-10-30T03:10:00Z"}
    assert NewsBrief.model_validate(card).degraded_since == {"crypto": "2026-10-30T03:10:00Z"}

    # The same morning's watchlist takes its values from the same helper.
    monkeypatch.setattr(market_scout, "_reusable_table", lambda session, ctx: (_empty_table(), "r"))
    ctx = ToolContext(
        env=ENV,
        session_factory=sessionmaker(bind=db),
        agent="market_scout",
        clock=lambda: NOW + timedelta(minutes=30),
        publish=Recorder(),
    )
    prepared = market_scout.prepare(ctx, NOW.date())
    assert prepared.degraded_asset_classes == ["crypto"]
    assert prepared.degraded_since == card["degraded_since"]


async def test_a_degraded_class_that_never_succeeded_has_null_degraded_since(db: Engine) -> None:
    cluster_id = _cluster(db)
    _crypto_down(db, {})
    rec = Recorder()
    client = fake.FakeClaudeClient([_attempt(cluster_id)])

    (result,) = await _supervisor(db, client, rec).handle_envelope(_owner_message())

    assert result.status == "ok"
    (reply,) = rec.of("warroom.message")
    assert reply.payload["card"]["degraded_since"] == {"crypto": None}
    ctx = ToolContext(env=ENV, session_factory=sessionmaker(bind=db), agent=SLUG, clock=lambda: NOW)
    assert news.news_coverage(ctx).degraded_since == {"crypto": None}


def test_degraded_since_ignores_a_success_after_the_replay_cutoff(db: Engine) -> None:
    _crypto_down(db, {REPUTABLE: NOW + timedelta(hours=1), OTHER_CRYPTO: CRYPTO_LAST_OK})
    ctx = ToolContext(env=ENV, session_factory=sessionmaker(bind=db), agent=SLUG, replay_as_of=NOW)
    assert news.news_coverage(ctx).degraded_since == {"crypto": "2026-10-30T03:10:00Z"}


def test_no_degraded_class_means_an_empty_degraded_since(db: Engine) -> None:
    ctx = ToolContext(env=ENV, session_factory=sessionmaker(bind=db), agent=SLUG, clock=lambda: NOW)
    coverage = news.news_coverage(ctx)
    assert coverage.degraded_asset_classes == [] and coverage.degraded_since == {}


def test_a_source_is_unavailable_when_disabled_keyless_or_backing_off() -> None:
    def source(**kw: Any) -> NewsSource:
        base: dict[str, Any] = {
            "enabled": True,
            "requires_key": False,
            "api_key": None,
            "disabled_until": None,
        }
        base.update(kw)
        return NewsSource(**base)

    assert news.source_available(source(), NOW)
    assert not news.source_available(source(enabled=False), NOW)
    assert not news.source_available(source(requires_key=True), NOW)
    assert news.source_available(source(requires_key=True, api_key="cipher"), NOW)
    assert not news.source_available(source(disabled_until=NOW + timedelta(seconds=1)), NOW)
    assert news.source_available(source(disabled_until=NOW), NOW)


def test_both_zones_writes_utc_and_baghdad() -> None:
    assert news.both_zones(NOW) == "2026-10-30 04:30 UTC (07:30 Baghdad)"


def test_warroom_message_cannot_be_registered_as_an_event_hook() -> None:
    from atlas_agents.registry import HookRegistry

    with pytest.raises(ValueError, match="mentions"):
        HookRegistry().event("x", "warroom.message")


async def test_an_invented_number_is_flagged_in_card_meta(db: Engine) -> None:
    """The number guard sees only tool outputs and code-written fields, never the model's own card."""
    cluster_id = _cluster(db)
    rec = Recorder()
    client = fake.FakeClaudeClient([_attempt(cluster_id, "REPORTED: the token lists; up 7.77% already.")])
    sup = _supervisor(db, client, rec)

    (result,) = await sup.handle_envelope(_owner_message())

    assert result.status == "ok"
    (reply,) = rec.of("warroom.message")
    meta = NewsBrief.model_validate(reply.payload["card"]).meta
    assert meta is not None
    assert [(u.raw, u.location) for u in meta.unmatched_numbers] == [("7.77%", "text")]
