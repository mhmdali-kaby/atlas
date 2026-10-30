"""ATL-136: the Derivatives & Sentiment Analyst (brief §7.5). Runs use a FakeClaudeClient script;
instruments, positions and watchlists are seeded in this checkout's test DB (`cd backend && php
artisan test` first). Features are built by hand (or with ATL-134's compute_features over frames)
and handed to the agent through a patched ``report.load_features``.

AC2 a watchlist card -> one run per pick, regime = the computed quadrant whatever the model says;
AC3 unreliable features -> the card says so, neutral for both sides; AC4 OI +12%/1h fires the code
trigger once per hour; AC5 the funding schedule fires at 08:05 UTC for watchlist + held only.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
from atlas_agents import testing as fake
from atlas_agents.agents import derivatives as hooks
from atlas_agents.derivatives import report
from atlas_agents.derivatives.features import DerivativesFeatures, DerivativesFrames, compute_features
from atlas_agents.registry import REGISTRY, discover_hooks
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools import ToolContext
from atlas_agents.tools.framework import ToolError
from atlas_agents.tools.positioning import build_positioning_features
from atlas_shared.db import (
    AgentRun,
    AgentState,
    AgentUsageDaily,
    BalanceSnapshot,
    Instrument,
    PositionSnapshot,
    Watchlist,
    WatchlistItem,
    owner_day,
)
from atlas_shared.events import EventEnvelope
from atlas_shared.market_data import MarketSnapshot
from atlas_shared.positioning import PositioningReport
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

ENV = "demo"
SLUG = "derivatives_sentiment"
RUN = uuid.uuid4().hex[:6].upper()
A = f"DA{RUN}-USDT-SWAP"
B = f"DB{RUN}-USDT-SWAP"
HELD = f"DH{RUN}-USDT-SWAP"
REMOVED = f"DR{RUN}-USDT-SWAP"
OTHER = f"DO{RUN}-USDT-SWAP"
ALL = [A, B, HELD, REMOVED, OTHER]
NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
# A day no other test seeds a watchlist for; 08:05 UTC is a funding slot (08:00 + 5 min).
FUNDING_DAY = datetime(2031, 3, 4, tzinfo=UTC)
FAR = datetime(2099, 1, 1, tzinfo=UTC)  # position snapshot newer than any other test's
H = timedelta(hours=1)


def _wipe_runs(engine: Engine) -> None:
    with engine.begin() as conn:
        for model in (AgentRun, AgentUsageDaily, AgentState):
            conn.execute(delete(model).where(model.agent == SLUG, model.env == ENV))


def _instrument(inst_id: str) -> Instrument:
    return Instrument(
        env=ENV,
        inst_id=inst_id,
        inst_type="SWAP",
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
        last_seen_at=NOW - H,
    )


@pytest.fixture
def db(engine: Engine) -> Iterator[Engine]:
    _wipe_runs(engine)
    hooks.reset()
    with Session(engine) as s:
        s.add_all(_instrument(i) for i in ALL)
        s.commit()
    yield engine
    with engine.begin() as conn:
        conn.execute(delete(Watchlist).where(Watchlist.env == ENV, Watchlist.created_by == f"test:{RUN}"))
        conn.execute(delete(Instrument).where(Instrument.inst_id.in_(ALL)))
        conn.execute(delete(PositionSnapshot).where(PositionSnapshot.taken_at == FAR))
        conn.execute(delete(BalanceSnapshot).where(BalanceSnapshot.taken_at == FAR))
    _wipe_runs(engine)
    hooks.reset()


def _supervisor(
    engine: Engine, client: fake.FakeClaudeClient, rec: Recorder, clock: list[datetime]
) -> Supervisor:
    spec = dataclasses.replace(REGISTRY[SLUG], enabled=True)  # disabled by default; enabled here
    return Supervisor(
        env=ENV,
        engine=engine,
        registry={SLUG: spec},
        client_factory=fake.factory(client),
        publisher=rec,
        clock=lambda: clock[0],
    )


# -- features -----------------------------------------------------------------------------------------


def _features(
    inst_id: str,
    *,
    quadrants: dict[int, str | None] | None = None,
    oi_1h: float | None = 2.0,
    unreliable: dict[str, str] | None = None,
    basis: float | None = 4.25,
) -> DerivativesFeatures:
    quads = quadrants if quadrants is not None else {1: "flat", 4: "short_covering", 24: "short_covering"}
    return DerivativesFeatures.model_validate(
        {
            "inst_id": inst_id,
            "ccy": inst_id.split("-")[0],
            "funding": {
                "current": 0.0001,
                "z": {"7d": 0.4, "30d": 0.2},
                "sign_flips": 0,
                "sign_flip_days": 3,
            },
            "open_interest": {
                "series": inst_id,
                "oi": 1000.0,
                "windows": [
                    {
                        "hours": h,
                        "oi_change_pct": oi_1h if h == 1 else -6.0,
                        "price_change_pct": 3.0,
                        "quadrant": q,
                    }
                    for h, q in sorted(quads.items())
                ],
                "unreliable": bool(unreliable),
                "unreliable_reasons": unreliable or {},
            },
            "long_short": {"ratio": 1.4},
            "taker": {"windows": [{"hours": 1, "buy_sell_ratio": 1.1, "rows": 12}]},
            "basis": {"basis_bps": basis, "reason": None if basis is not None else "index_unavailable"},
            "unreliable": bool(unreliable),
            "unreliable_reasons": unreliable or {},
            "data_as_of": NOW,
        }
    )


def _patch_features(monkeypatch: pytest.MonkeyPatch, by_inst: dict[str, DerivativesFeatures]) -> list[str]:
    asked: list[str] = []

    async def load(ctx: ToolContext, inst_id: str) -> DerivativesFeatures:
        asked.append(inst_id)
        if inst_id not in by_inst:
            raise ToolError("not_found", inst_id)
        return by_inst[inst_id]

    monkeypatch.setattr(report, "load_features", load)
    return asked


def _card(inst_id: str, **changes: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "positioning_report",
        "inst_id": inst_id,
        "regime": "new_longs",  # the model's guess; the features say short_covering
        "crowding_score": 0.7,
        "crowded_side": "short",
        "squeeze_risk": {"long_squeeze": 0.2, "short_squeeze": 0.6},
        "funding_view": "Funding is mildly positive (z 0.4 over 7d).",
        "basis_bps": 9.9,
        "underlying_premium_pct": None,
        "sentiment_score": 0.1,
        "supports_direction": {"long": "supports", "short": "opposes"},
        "key_evidence": ["OI -6.0% in 24h with price +3.0% (as_of 2026-10-30T10:00Z)"],
        "data_as_of": NOW.isoformat(),
    }
    card.update(changes)
    return card


def _reply(card: dict[str, Any], text: str = "Short covering: rally on falling OI.") -> list[Any]:
    return [
        fake.assistant_text(f"{text}\n\n```json\n{json.dumps(card)}\n```"),
        fake.result(tokens_in=300, tokens_out=100, turns=1),
    ]


def _reports(rec: Recorder) -> list[EventEnvelope]:
    return [
        e
        for e in rec.of("warroom.message")
        if e.payload.get("kind") == "research_report" and e.payload.get("author") == SLUG
    ]


def _runs(engine: Engine) -> list[AgentRun]:
    with Session(engine) as s:
        return list(s.scalars(select(AgentRun).where(AgentRun.agent == SLUG, AgentRun.env == ENV)))


def _watchlist_envelope(picks: list[dict[str, Any]]) -> EventEnvelope:
    card = {"type": "watchlist", "watchlist_id": str(uuid.uuid4()), "picks": picks}
    return EventEnvelope(
        type="warroom.message",
        env=ENV,
        source="agent:market_scout",
        payload={"author": "market_scout", "author_type": "agent", "kind": "watchlist", "card": card},
    )


# -- registration -------------------------------------------------------------------------------------


def test_disabled_by_default_and_registers_its_triggers() -> None:
    assert not REGISTRY[SLUG].enabled
    found = discover_hooks()[SLUG]
    assert found.on_mention is hooks.answer_mention
    assert set(found.on_event) == {"warroom.message.watchlist"}
    assert {h.name for h in found.schedule} == {hooks.FUNDING_JOB, hooks.EXTREME_JOB}
    assert all(not h.daily for h in found.schedule)


# -- AC2: watchlist card -> one run per pick; regime is code's ---------------------------------------


async def test_watchlist_card_starts_one_run_per_pick_and_regime_is_the_computed_quadrant(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_features(monkeypatch, {A: _features(A), B: _features(B, quadrants={1: None, 4: "new_shorts"})})
    client = fake.FakeClaudeClient([_reply(_card(A)), _reply(_card(B, regime="long_liquidation"))])
    rec = Recorder()
    sup = _supervisor(db, client, rec, [NOW])

    await sup.handle_envelope(_watchlist_envelope([{"inst_id": B, "rank": 2}, {"inst_id": A, "rank": 1}]))
    await hooks.settled()

    assert len(client.queries) == 2
    assert A in client.queries[0] and B in client.queries[1]  # best rank first
    cards = [PositioningReport.model_validate(r.payload["card"]) for r in _reports(rec)]
    assert [c.inst_id for c in cards] == [A, B]
    # A: 24h quadrant short_covering although the model said new_longs.
    assert cards[0].regime == "short_covering"
    assert cards[0].quadrants == {"1h": "flat", "4h": "short_covering", "24h": "short_covering"}
    rules = [c["rule"] for c in cards[0].meta["corrections"]]
    assert "regime_from_features" in rules and "basis_from_features" in rules
    assert cards[0].basis_bps == Decimal("4.25")  # the features', not the model's 9.9
    # B: no 24h window, 1h has none -> the 4h quadrant.
    assert cards[1].regime == "new_shorts"
    assert len(_runs(db)) == 2
    await sup.close()


async def test_watchlist_card_from_anyone_but_the_scout_starts_nothing(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_features(monkeypatch, {A: _features(A)})
    client = fake.FakeClaudeClient([])
    sup = _supervisor(db, client, Recorder(), [NOW])
    envelope = _watchlist_envelope([{"inst_id": A, "rank": 1}])
    envelope.payload["author"] = "Owner"
    envelope.payload["author_type"] = "human"
    await sup.handle_envelope(envelope)
    await hooks.settled()
    assert client.queries == []
    await sup.close()


def test_squeeze_risk_is_clamped_and_premium_is_cleared() -> None:
    card = _card(A, squeeze_risk={"long_squeeze": 1.7, "short_squeeze": -0.2}, underlying_premium_pct=0.8)
    corrections = report.enforce_report(card, _features(A))
    final = PositioningReport.model_validate(card)
    assert final.squeeze_risk == {"long_squeeze": 1.0, "short_squeeze": 0.0}
    assert final.underlying_premium_pct is None
    rules = [c["rule"] for c in corrections]
    assert rules.count("squeeze_risk_clamped") == 2 and "no_underlying_reference" in rules


def test_model_cannot_set_code_fields() -> None:
    card = _card(A, data_reliable=True, quadrants={"1h": "new_longs"}, meta={"x": 1}, basis_reason="ok")
    report.enforce_report(card, _features(A, basis=None))
    final = PositioningReport.model_validate(card)
    assert final.basis_bps is None and final.basis_reason == "index_unavailable"
    assert final.quadrants["1h"] == "flat" and final.meta == {}


# -- AC3: unreliable data ------------------------------------------------------------------------------


async def test_unreliable_features_give_a_card_saying_so_with_neutral_both_sides(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    reasons = {"zero_open_interest": "Open interest reads 0, which is not a real reading on demo."}
    _patch_features(
        monkeypatch, {A: _features(A, quadrants={1: None, 4: None, 24: None}, unreliable=reasons)}
    )
    client = fake.FakeClaudeClient([_reply(_card(A), "Longs crowded.")])
    rec = Recorder()
    sup = _supervisor(db, client, rec, [NOW])

    await sup.handle_envelope(_watchlist_envelope([{"inst_id": A, "rank": 1}]))
    await hooks.settled()

    assert "UNRELIABLE" in client.queries[0]
    [message] = _reports(rec)
    card = PositioningReport.model_validate(message.payload["card"])
    assert card.data_reliable is False
    assert card.unreliable_reasons == reasons
    assert card.supports_direction == {"long": "neutral", "short": "neutral"}
    assert card.regime == "flat"
    assert any("Data unreliable" in n for n in card.notes)
    assert message.payload["content_md"].startswith(f"**Data unreliable for {A}:**")
    await sup.close()


def test_report_model_refuses_a_direction_on_unreliable_data() -> None:
    card = report.enforce_report(_card(A), _features(A))  # corrections only
    assert isinstance(card, list)
    raw = _card(A)
    raw.update(data_reliable=False, supports_direction={"long": "supports", "short": "neutral"})
    with pytest.raises(ValueError, match="neutral"):
        PositioningReport.model_validate(raw)


# -- AC4: extreme readings, debounced --------------------------------------------------------------------


def _oi_jump_features(inst_id: str, as_of: datetime) -> DerivativesFeatures:
    """ATL-134's compute_features over frames where OI rose 12% in the last hour."""
    stamps = [as_of - timedelta(minutes=5 * i) for i in range(30, -1, -1)]
    oi = pd.Series(
        [100.0 if ts <= as_of - H else 112.0 for ts in stamps], index=pd.DatetimeIndex(stamps), dtype=float
    )
    price = pd.DataFrame(
        {"c": [100.0 if ts <= as_of - H else 103.0 for ts in stamps], "vol": [10.0] * len(stamps)},
        index=pd.DatetimeIndex(stamps),
    )
    empty = pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    frames = DerivativesFrames(
        funding=empty,
        oi=oi,
        oi_series=inst_id,
        price=price,
        long_short=empty,
        taker=pd.DataFrame({"buy_vol": [], "sell_vol": []}, index=pd.DatetimeIndex([], tz="UTC")),
    )
    return compute_features(inst_id, frames, as_of)


def test_extreme_reasons_for_oi_and_funding_z() -> None:
    jump = _oi_jump_features(A, NOW)
    [one_hour] = [w for w in jump.open_interest.windows if w.hours == 1]
    assert one_hour.oi_change_pct == pytest.approx(12.0)
    assert hooks.extreme_reasons(jump) == ["open interest +12.0% in 1h (threshold 10%)"]
    assert hooks.extreme_reasons(_features(A, oi_1h=9.9)) == []
    assert hooks.extreme_reasons(_features(A, oi_1h=-10.5)) != []
    hot = _features(A).model_copy(
        update={"funding": _features(A).funding.model_copy(update={"z": {"7d": -2.6}})}
    )
    assert hooks.extreme_reasons(hot) == ["funding z-score (7d) -2.60 (threshold 2.5)"]


def _hold(engine: Engine, inst_id: str) -> None:
    with Session(engine) as s:
        s.add(
            BalanceSnapshot(
                env=ENV, account="trading", ccy="USDT", eq=Decimal("1"), eq_usd=Decimal("1"), taken_at=FAR
            )
        )
        s.add(PositionSnapshot(env=ENV, inst_id=inst_id, pos_side="net", pos=Decimal("3"), taken_at=FAR))
        s.commit()


async def test_oi_jump_fires_the_code_trigger_once_per_hour(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _hold(db, HELD)
    clock = [NOW]
    feats = {HELD: _oi_jump_features(HELD, NOW)}
    _patch_features(monkeypatch, feats)
    client = fake.FakeClaudeClient([_reply(_card(HELD)), _reply(_card(HELD))])
    rec = Recorder()
    sup = _supervisor(db, client, rec, clock)

    extreme = next(h for h in discover_hooks()[SLUG].schedule if h.name == hooks.EXTREME_JOB)
    # Both schedule jobs tick; 10:00 UTC is outside every funding slot, so only the extreme job runs.
    await sup.tick(NOW)
    await hooks.settled()
    assert len(client.queries) == 1
    assert "open interest +12.0% in 1h" in client.queries[0]

    # A second crossing 30 minutes later (next check): debounced.
    clock[0] = NOW + timedelta(minutes=30)
    feats[HELD] = _oi_jump_features(HELD, clock[0])
    await sup.tick(clock[0])
    await hooks.settled()
    assert len(client.queries) == 1

    # After the hour, a crossing fires again.
    clock[0] = NOW + timedelta(minutes=61)
    feats[HELD] = _oi_jump_features(HELD, clock[0])
    await sup.tick(clock[0])
    await hooks.settled()
    assert len(client.queries) == 2
    assert extreme.every_seconds == 300
    assert len(_reports(rec)) == 2
    await sup.close()


def test_debounce_is_per_trigger_and_instrument() -> None:
    hooks.reset()
    assert hooks.debounce(ENV, "funding", [A, B], NOW) == [A, B]
    assert hooks.debounce(ENV, "funding", [A], NOW + timedelta(minutes=59)) == []
    assert hooks.debounce(ENV, "extreme", [A], NOW + timedelta(minutes=59)) == [A]
    assert hooks.debounce(ENV, "funding", [A], NOW + timedelta(minutes=60)) == [A]
    hooks.reset()


# -- AC5: funding settlement schedule ------------------------------------------------------------------


def test_funding_slot_times() -> None:
    day = FUNDING_DAY
    assert hooks.funding_slot(day + timedelta(hours=8, minutes=4)) is None
    assert hooks.funding_slot(day + timedelta(hours=8, minutes=5)) == day + timedelta(hours=8, minutes=5)
    assert hooks.funding_slot(day + timedelta(hours=8, minutes=35)) == day + timedelta(hours=8, minutes=5)
    assert hooks.funding_slot(day + timedelta(hours=8, minutes=36)) is None  # past the grace
    assert hooks.funding_slot(day + timedelta(minutes=5)) == day + timedelta(minutes=5)
    assert hooks.funding_slot(day + timedelta(hours=16, minutes=6)) == day + timedelta(hours=16, minutes=5)
    assert hooks.funding_slot(day + timedelta(minutes=1)) is None  # yesterday's 16:05 is long gone


def _watchlist(engine: Engine, day: date, items: list[tuple[str, int, bool]]) -> None:
    with Session(engine) as s:
        wl = Watchlist(env=ENV, date=day, version=1, created_by=f"test:{RUN}", status="published", data={})
        s.add(wl)
        s.flush()
        for inst_id, rank, removed in items:
            s.add(
                WatchlistItem(
                    watchlist_id=wl.id,
                    inst_id=inst_id,
                    rank=rank,
                    asset_class="crypto",
                    bias="long",
                    conviction=Decimal("0.6"),
                    thesis="Test pick.",
                    catalysts=[],
                    key_levels={},
                    invalidation="Test.",
                    removed_by_owner_at=FUNDING_DAY if removed else None,
                    data_as_of=FUNDING_DAY,
                )
            )
        s.commit()


async def test_funding_schedule_fires_at_0805_utc_for_watchlist_and_held_only(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    at_0805 = FUNDING_DAY + timedelta(hours=8, minutes=5)
    _watchlist(db, owner_day(at_0805), [(A, 1, False), (REMOVED, 2, True)])
    _hold(db, HELD)
    asked = _patch_features(monkeypatch, {i: _features(i) for i in ALL})
    # The extreme check ticks too, reading the same watched set; its features are not extreme.
    client = fake.FakeClaudeClient([_reply(_card(A)), _reply(_card(HELD))])
    rec = Recorder()
    clock = [at_0805 - timedelta(minutes=2)]
    sup = _supervisor(db, client, rec, clock)

    await sup.tick(clock[0])  # 08:03: not yet
    await hooks.settled()
    assert client.queries == []

    clock[0] = at_0805
    await sup.tick(at_0805)
    await hooks.settled()
    assert set(asked) == {A, HELD}  # never REMOVED or OTHER
    assert len(client.queries) == 2
    assert A in client.queries[0] and HELD in client.queries[1]  # watchlist first, then held
    assert "Funding settled" in client.queries[0]
    runs = _runs(db)
    assert {r.trigger for r in runs} == {"schedule"}
    assert {r.correlation_id for r in runs} == {"derivatives:funding:2031-03-04T08:05Z"}

    clock[0] = at_0805 + timedelta(minutes=2)  # the same slot is not run twice
    await sup.tick(clock[0])
    await hooks.settled()
    assert len(client.queries) == 2
    await sup.close()


# -- @Derivatives -----------------------------------------------------------------------------------------


def _mention(text: str, author_type: str = "human") -> EventEnvelope:
    return EventEnvelope(
        type="warroom.message",
        env=ENV,
        source="human:owner" if author_type == "human" else "agent:market_scout",
        payload={
            "thread": {"kind": "main", "key": "main"},
            "thread_id": str(uuid.uuid4()),
            "message_id": str(uuid.uuid4()),
            "author_type": author_type,
            "author": "Owner" if author_type == "human" else "market_scout",
            "kind": "text",
            "content_md": text,
            "reply_to": None,
            "mentions": [SLUG],
        },
    )


async def test_owner_mention_runs_each_named_instrument_and_is_never_debounced(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_features(monkeypatch, {A: _features(A)})
    client = fake.FakeClaudeClient([_reply(_card(A)), _reply(_card(A))])
    rec = Recorder()
    sup = _supervisor(db, client, rec, [NOW])
    for _ in range(2):
        await sup.handle_envelope(_mention(f"@Derivatives how crowded is {A.lower()}?"))
        await hooks.settled()
    assert len(client.queries) == 2
    assert all(r.payload["reply_to"] for r in _reports(rec))
    await sup.close()


async def test_mention_without_an_instrument_asks_for_one(db: Engine) -> None:
    client = fake.FakeClaudeClient([])
    rec = Recorder()
    sup = _supervisor(db, client, rec, [NOW])
    await sup.handle_envelope(_mention("@Derivatives what do you think?"))
    await hooks.settled()
    assert client.queries == []
    [reply] = [e for e in rec.of("warroom.message") if e.payload.get("author") == SLUG]
    assert "Name the instrument" in reply.payload["content_md"]
    await sup.close()


# -- the tool ---------------------------------------------------------------------------------------------


async def test_get_positioning_features_tool(db: Engine) -> None:
    factory = sessionmaker(db)
    ctx = ToolContext(
        env=ENV,
        session_factory=factory,
        agent=SLUG,
        clock=lambda: NOW,
        market_snapshot=lambda env, inst: MarketSnapshot(
            env=env, inst_id=inst, last_msg_at=None, ticker=None, mark=None, books5=None, channel_at={}
        ),
    )
    data, as_of = await build_positioning_features(ctx, A)
    assert data["inst_id"] == A and as_of == NOW
    assert data["basis"]["basis_bps"] is None and data["basis"]["reason"] == "stale"
    assert {w["hours"] for w in data["open_interest"]["windows"]} == {1, 4, 24}
    with pytest.raises(ToolError):
        await build_positioning_features(ctx, f"NOPE{RUN}-USDT-SWAP")

    replay = dataclasses.replace(ctx, replay_as_of=NOW, market_snapshot=_no_live_read)
    data, _ = await build_positioning_features(replay, A)  # replay never reads the live snapshot
    assert data["basis"]["reason"] == "stale"


def _no_live_read(env: str, inst_id: str) -> MarketSnapshot:
    raise AssertionError("replay read the live Redis snapshot")
