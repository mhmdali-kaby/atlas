"""Daily agent scores (ATL-110): Market Scout's selection edge and directional accuracy, News
Intelligence's ingest-to-alert latency, idempotent re-runs and the open-window skip.

Run `cd backend && php artisan test` first so the DB is migrated. Every test runs inside one
transaction that is rolled back.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from atlas_agents import scoring
from atlas_agents.agents import scores as hooks
from atlas_agents.prompts import load_context, scorecard_block
from atlas_agents.registry import ScheduleFire, discover_hooks
from atlas_agents.tools.framework import ToolContext
from atlas_agents.watchlists import save_watchlist
from atlas_shared.db import AgentScore, Candle, NewsItem, Watchlist, WatchlistItem
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

ENV = "demo"
RUN = uuid.uuid4().hex[:6].upper()
DAY = date(2026, 10, 20)  # Baghdad day of the watchlist
T0 = datetime(2026, 10, 20, 5, 0, tzinfo=UTC)  # 08:00 Baghdad: the watchlist's created_at
T1 = T0 + timedelta(hours=24)
AFTER = datetime(2026, 10, 21, 21, 15, tzinfo=UTC)  # 00:15 Baghdad on the 22nd: window closed


def inst(name: str) -> str:
    return f"T{RUN}{name}-USDT-SWAP"


@pytest.fixture
def db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    conn = engine.connect()
    outer = conn.begin()
    factory = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    try:
        with factory() as s:
            s.execute(delete(WatchlistItem))
            s.execute(delete(Watchlist))
            s.execute(delete(AgentScore).where(AgentScore.agent.in_([scoring.SCOUT, scoring.NEWS])))
            s.commit()
        yield factory
    finally:
        outer.rollback()
        conn.close()


def _ctx(factory: sessionmaker[Session], now: datetime, agent: str = scoring.SCOUT) -> ToolContext:
    return ToolContext(env=ENV, session_factory=factory, agent=agent, clock=lambda: now)


def _candle(inst_id: str, bar: str, ts: datetime, c: float, spread: float = 1.0) -> Candle:
    d = Decimal(str(c))
    s = Decimal(str(spread))
    return Candle(
        env=ENV, inst_id=inst_id, bar=bar, ts=ts, o=d, h=d + s, l=d - s, c=d, vol=Decimal(1), confirmed=True
    )


def _seed_prices(s: Session, inst_id: str, p0: float, p1: float) -> None:
    """20 daily bars at 100 with high-low 2 (so ATR(1D) = 2 exactly), then the 1H bars closing at
    T0 (close p0) and at T1 (close p1)."""
    for k in range(20):
        s.add(_candle(inst_id, "1D", datetime(2026, 9, 30, tzinfo=UTC) + timedelta(days=k), 100.0))
    s.add(_candle(inst_id, "1H", T0 - timedelta(hours=1), p0))
    s.add(_candle(inst_id, "1H", T1 - timedelta(hours=1), p1))


def _pick(rank: int, inst_id: str, bias: str) -> dict[str, Any]:
    return {
        "inst_id": inst_id,
        "rank": rank,
        "asset_class": "crypto",
        "bias": bias,
        "conviction": 0.6,
        "thesis": "Fixture thesis.",
        "catalysts": [],
        "key_levels": {},
        "invalidation": "Fixture invalidation.",
        "data_as_of": "2026-10-20T04:55:00Z",
    }


# Picks: A long +3 ATR (right), B short +1 ATR (wrong), C neutral -2 ATR (not directional),
# G removed by the owner (scored nowhere). Shortlist others: D +0.5, E -1.5, F 0, H no candles.
MOVES = {
    "A": (100, 106),
    "B": (100, 102),
    "C": (100, 96),
    "G": (100, 140),
    "D": (100, 101),
    "E": (100, 97),
    "F": (100, 100),
}


def _seed_scout(factory: sessionmaker[Session], *, created_at: datetime = T0) -> None:
    with factory() as s:
        for name, (p0, p1) in MOVES.items():
            _seed_prices(s, inst(name), p0, p1)
        wl = save_watchlist(
            s,
            ENV,
            DAY,
            [
                _pick(1, inst("A"), "long"),
                _pick(2, inst("B"), "short"),
                _pick(3, inst("C"), "neutral"),
                _pick(4, inst("G"), "long"),
            ],
            "published",
            created_at=created_at,
            data={"shortlist": [inst(n) for n in "ABCGDEFH"]},
        )
        s.execute(
            text(
                "UPDATE watchlist_items SET removed_by_owner_at = :at "
                "WHERE watchlist_id = :w AND inst_id = :i"
            ),
            {"at": created_at + timedelta(minutes=5), "w": wl.id, "i": inst("G")},
        )
        s.commit()


def _rows(factory: sessionmaker[Session], agent: str) -> list[AgentScore]:
    with factory() as s:
        return list(s.scalars(select(AgentScore).where(AgentScore.agent == agent, AgentScore.env == ENV)))


# -- AC1: selection edge and directional accuracy -----------------------------------------------------


def test_pure_selection_edge_matches_hand_calculation() -> None:
    picks = [
        scoring.PickMove("a", "long", 3.0),
        scoring.PickMove("b", "short", 1.0),
        scoring.PickMove("c", "neutral", -2.0),
    ]
    out = scoring.selection_edge(picks, [0.5, -1.5, 0.0])
    # median |picks| = median(3, 1, 2) = 2; median |others| = median(0.5, 1.5, 0) = 0.5
    assert out["median_pick_move_atr"] == 2.0
    assert out["median_shortlist_move_atr"] == 0.5
    assert out["selection_edge_atr"] == 1.5
    # long +3 right, short +1 wrong; neutral excluded
    assert out["directional_accuracy"] == 0.5
    assert (out["directional_n"], out["directional_correct"], out["comparison_n"]) == (2, 1, 3)


def test_zero_move_is_a_directional_miss_and_no_comparison_gives_no_edge() -> None:
    out = scoring.selection_edge([scoring.PickMove("a", "long", 0.0)], [])
    assert out["directional_accuracy"] == 0.0
    assert out["selection_edge_atr"] is None


def test_scout_day_from_fixture_watchlist_and_candles(db: sessionmaker[Session]) -> None:
    _seed_scout(db)
    data = scoring.score_scout_day(_ctx(db, AFTER), DAY)
    assert data is not None
    # ATR(1D) = 2. A: (106-100)/2 = +3, B: +1, C: -2. D: +0.5, E: -1.5, F: 0.
    moves = {p["inst_id"]: p["move_atr"] for p in data["picks"]}
    assert moves == {inst("A"): 3.0, inst("B"): 1.0, inst("C"): -2.0}
    assert data["median_pick_move_atr"] == 2.0
    assert data["median_shortlist_move_atr"] == 0.5
    assert data["selection_edge_atr"] == pytest.approx(1.5)
    assert data["directional_accuracy"] == 0.5
    assert data["comparison_n"] == 3
    assert [s["inst_id"] for s in data["skipped"]] == [inst("H")]
    (row,) = _rows(db, scoring.SCOUT)
    assert row.day == DAY and row.n == 3 and row.hit_rate == Decimal("0.5000")
    assert row.data["metrics"] == {"selection_edge_atr": 1.5, "directional_accuracy": 0.5}


# -- AC2: alert latency ---------------------------------------------------------------------------


def _seed_news(factory: sessionmaker[Session], latencies: list[int], *, unanswered: int = 1) -> None:
    """One news_alert per latency on DAY (Baghdad), each answered by News Intelligence that many
    seconds after its item was fetched, plus ``unanswered`` alerts with no reply."""
    with factory() as s:
        thread = uuid.uuid4()
        s.execute(
            text(
                "INSERT INTO warroom_threads (id, env, kind, key, title, created_at, updated_at) "
                "VALUES (:id, :env, 'agent', :key, 't', now(), now())"
            ),
            {"id": thread, "env": ENV, "key": f"atl110-{RUN}"},
        )

        def message(
            author_type: str,
            author: str,
            kind: str,
            at: datetime,
            card: str | None,
            reply_to: uuid.UUID | None,
        ) -> uuid.UUID:
            mid = uuid.uuid4()
            s.execute(
                text(
                    "INSERT INTO warroom_messages (id, env, thread_id, author_type, author, kind, "
                    "content_md, "
                    "card, reply_to, created_at, updated_at) VALUES (:id, :env, :t, :at_, :a, :k, 'x', "
                    "CAST(:card AS jsonb), :r, :ts, :ts)"
                ),
                {
                    "id": mid,
                    "env": ENV,
                    "t": thread,
                    "at_": author_type,
                    "a": author,
                    "k": kind,
                    "card": card,
                    "r": reply_to,
                    "ts": at,
                },
            )
            return mid

        for k, latency in enumerate([*latencies, *([None] * unanswered)]):
            fetched = datetime(2026, 10, 20, 6, 0, tzinfo=UTC) + timedelta(hours=k)
            item = NewsItem(
                source="fixture",
                url=f"https://n.test/{RUN}/{k}",
                canonical_url=f"https://n.test/{RUN}/{k}",
                title=f"Story {k}",
                published_at=fetched - timedelta(minutes=1),
                fetched_at=fetched,
                importance=5,
                created_at=fetched,
                updated_at=fetched,
            )
            s.add(item)
            s.flush()
            alert = message(
                "system",
                "system:news",
                "news_alert",
                fetched + timedelta(seconds=5),
                f'{{"news_item_id": "{item.id}"}}',
                None,
            )
            if latency is not None:
                message(
                    "agent",
                    scoring.NEWS,
                    "research_report",
                    fetched + timedelta(seconds=latency),
                    '{"type": "news_brief", "form": "alert_analysis"}',
                    alert,
                )
                # A later second reply does not change the latency (the first reply counts).
                message(
                    "agent", scoring.NEWS, "text", fetched + timedelta(seconds=latency + 600), None, alert
                )
        s.commit()


LATENCIES = [40, 10, 100, 30, 60, 20, 90, 50, 80, 70]  # seconds


def test_pure_latency_stats_match_hand_calculation() -> None:
    out = scoring.latency_stats(LATENCIES, 90)
    # sorted 10..100: median = (50 + 60) / 2 = 55; p90 (linear): rank 0.9 * 9 = 8.1 -> 90 + 0.1 * 10 = 91
    assert out["latency_median_s"] == 55.0
    assert out["latency_p90_s"] == pytest.approx(91.0)
    assert out["within_target_share"] == 0.9  # 9 of 10 at or under 90 s


def test_news_day_latency_from_fixture_alerts(db: sessionmaker[Session]) -> None:
    _seed_news(db, LATENCIES)
    data = scoring.score_news_day(_ctx(db, AFTER, scoring.NEWS), DAY)
    assert data is not None
    assert data["latency_median_s"] == 55.0
    assert data["latency_p90_s"] == pytest.approx(91.0)
    assert sorted(a["latency_s"] for a in data["alerts"]) == sorted(float(x) for x in LATENCIES)
    assert len(data["unanswered_alert_ids"]) == 1
    (row,) = _rows(db, scoring.NEWS)
    assert row.day == DAY and row.n == 10 and row.hit_rate == Decimal("0.9000")


def test_news_day_without_answered_alerts_writes_nothing(db: sessionmaker[Session]) -> None:
    _seed_news(db, [], unanswered=2)
    assert scoring.score_news_day(_ctx(db, AFTER, scoring.NEWS), DAY) is None
    assert _rows(db, scoring.NEWS) == []


# -- AC3: idempotent ------------------------------------------------------------------------------


def test_running_the_job_twice_leaves_one_row_per_agent_per_day(db: sessionmaker[Session]) -> None:
    _seed_scout(db)
    _seed_news(db, LATENCIES)
    today = date(2026, 10, 22)
    for _ in range(2):
        for agent in (scoring.SCOUT, scoring.NEWS):
            results = scoring.score_days(_ctx(db, AFTER, agent), agent, today)
            assert results[DAY] is not None
    with db() as s:
        counts = s.execute(
            select(AgentScore.agent, AgentScore.day, func.count())
            .where(AgentScore.env == ENV, AgentScore.agent.in_([scoring.SCOUT, scoring.NEWS]))
            .group_by(AgentScore.agent, AgentScore.day)
        ).all()
    assert sorted((a, d, n) for a, d, n in counts) == sorted(
        [(scoring.NEWS, DAY, 1), (scoring.SCOUT, DAY, 1)]
    )


async def test_the_0015_hook_scores_once_per_day(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_scout(db)
    calls: list[date] = []
    real = scoring.score_days

    def counting(ctx: ToolContext, agent: str, today: date | None = None) -> Any:
        calls.append(today or date.min)
        return real(ctx, agent, today)

    monkeypatch.setattr(scoring, "score_days", counting)
    monkeypatch.setattr(hooks, "_scored", {})

    class Host:
        env = ENV
        replaying = False

        def tool_context(self, agent: str) -> ToolContext:
            return _ctx(db, AFTER, agent)

    fire = ScheduleFire(name="scoring", at=AFTER, day=date(2026, 10, 22), correlation_id="scoring:2026-10-22")
    assert await hooks.score_market_scout(Host(), fire) is None  # type: ignore[arg-type]
    assert await hooks.score_market_scout(Host(), fire) is None  # type: ignore[arg-type]
    assert calls == [date(2026, 10, 22)]
    assert [r.day for r in _rows(db, scoring.SCOUT)] == [DAY]


def test_scoring_hooks_are_registered_at_0015_for_both_agents() -> None:
    found = discover_hooks()
    for slug in (scoring.SCOUT, scoring.NEWS):
        (hook,) = [h for h in found[slug].schedule if h.name == "scoring"]
        assert hook.time_of_day().isoformat() == "00:15:00"


# -- AC4: open window ----------------------------------------------------------------------------


def test_a_day_whose_24h_window_has_not_closed_writes_no_scout_row(db: sessionmaker[Session]) -> None:
    _seed_scout(db)
    just_before = T1 - timedelta(minutes=1)
    assert scoring.score_scout_day(_ctx(db, just_before), DAY) is None
    # Through the daily job too: at 00:15 Baghdad on the 21st, the 20th's window is still open.
    results = scoring.score_days(_ctx(db, datetime(2026, 10, 20, 21, 15, tzinfo=UTC)), scoring.SCOUT)
    assert results[DAY] is None
    assert _rows(db, scoring.SCOUT) == []


def test_empty_watchlist_writes_no_scout_row(db: sessionmaker[Session]) -> None:
    with db() as s:
        save_watchlist(s, ENV, DAY, [], "empty", reason="No instrument passed.", created_at=T0)
        s.commit()
    assert scoring.score_scout_day(_ctx(db, AFTER), DAY) is None
    assert _rows(db, scoring.SCOUT) == []


# -- the scores reach the prompt -----------------------------------------------------------------


def test_scorecard_block_shows_the_scout_metrics(db: sessionmaker[Session]) -> None:
    _seed_scout(db)
    scoring.score_scout_day(_ctx(db, AFTER), DAY)
    block = scorecard_block(load_context(_ctx(db, AFTER)))
    assert "| metrics |" in block
    assert "| 2026-10-20 | 3 | 0.5 |" in block
    assert "directional_accuracy=0.5, selection_edge_atr=1.5" in block
