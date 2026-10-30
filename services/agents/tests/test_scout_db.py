"""ATL-104 AC3/AC4/AC5 against Postgres: the feature table at a replay cutoff T reads nothing after T,
an instrument without funding history gets null funding features, and the table is stored as a
research_report.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. Instrument ids are unique to this module.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pytest
from atlas_agents.scout import features as scout_features
from atlas_agents.scout.features import build_feature_table, save_feature_table
from atlas_agents.scout.universe import build_universe
from atlas_agents.tools import ToolContext
from atlas_shared.db import (
    Candle,
    EconomicEvent,
    FundingRate,
    Instrument,
    LongShortRatio,
    NewsItem,
    OpenInterest,
    ResearchReport,
)
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

ENV = "demo"
FUND = "ATL104F-USDT-SWAP"  # has funding, OI, long/short, news
BARE = "ATL104N-USDT-SWAP"  # no funding history at all
BTC = "ATL104BTC-USDT-SWAP"
SPY = "ATL104SPY-USDT-SWAP"
ALL = (FUND, BARE, BTC, SPY)
CCYS = ("ATL104F", "ATL104N", "ATL104BTC", "ATL104SPY")
SOURCE = "atl104-test"
DAY, HOUR = timedelta(days=1), timedelta(hours=1)
D0 = datetime(2025, 6, 1, 16, 0, tzinfo=UTC)
T = D0 + DAY * 300 + HOUR * 5  # 300 daily bars closed by T, 100 more after it
WALL = T + DAY * 120
H0 = T - HOUR * 760  # 760 closed 1H bars before T, 200 after


def _candles(
    inst: str, bar: str, start: datetime, step: timedelta, n: int, seed: int
) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    rows = []
    for i in range(n):
        ts = start + step * i
        c = Decimal(f"{close[i]:.6f}")
        o = Decimal(f"{(close[i - 1] if i else close[0]):.6f}")
        # Bars after T are wild, so any leak would change the features.
        k = Decimal(3) if ts + step > T else Decimal(1)
        rows.append(
            {
                "env": ENV,
                "inst_id": inst,
                "bar": bar,
                "ts": ts,
                "o": o * k,
                "h": max(o, c) * Decimal("1.01") * k,
                "l": min(o, c) * Decimal("0.99") * k,
                "c": c * k,
                "vol": Decimal(1000),
                "vol_ccy": Decimal(1000),
                "vol_ccy_quote": Decimal(5_000_000) * k,
                "confirmed": True,
            }
        )
    return rows


def _instrument(inst_id: str, asset_class: str = "crypto") -> dict[str, Any]:
    return {
        "env": ENV,
        "inst_id": inst_id,
        "inst_type": "SWAP",
        "asset_class": asset_class,
        "base_ccy": inst_id.split("-")[0],
        "settle_ccy": "USDT",
        "lot_sz": Decimal("1"),
        "min_sz": Decimal("1"),
        "tick_sz": Decimal("0.01"),
        "state": "live",
        "list_time": D0 - DAY * 30,
        "created_at": D0 - DAY * 30,
        "updated_at": D0 - DAY * 30,
    }


def _wipe(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(Candle).where(Candle.inst_id.in_(ALL)))
        conn.execute(delete(FundingRate).where(FundingRate.inst_id.in_(ALL)))
        conn.execute(delete(OpenInterest).where(OpenInterest.inst_id.in_(ALL + CCYS)))
        conn.execute(delete(LongShortRatio).where(LongShortRatio.ccy.in_(CCYS)))
        conn.execute(delete(NewsItem).where(NewsItem.source == SOURCE))
        conn.execute(delete(EconomicEvent).where(EconomicEvent.source == SOURCE))
        conn.execute(delete(Instrument).where(Instrument.inst_id.in_(ALL)))
        conn.execute(
            delete(ResearchReport).where(
                ResearchReport.agent == "market_scout", ResearchReport.content_md.contains(SOURCE)
            )
        )


def _news(title: str, published: datetime, fetched: datetime, importance: int) -> dict[str, Any]:
    url = f"https://example.test/{SOURCE}/{title}"
    return {
        "source": SOURCE,
        "url": url,
        "canonical_url": url,
        "title": title,
        "published_at": published,
        "fetched_at": fetched,
        "instruments": [FUND],
        "asset_classes": ["crypto"],
        "importance": importance,
        "created_at": fetched,
        "updated_at": fetched,
    }


@pytest.fixture
def seeded(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    refs = DEFAULTS["agents"]["scout"]["features"]["references"]
    monkeypatch.setitem(refs, "btc", [BTC])
    monkeypatch.setitem(refs, "spy", ["ATL104MISSING-USDT-SWAP", SPY])
    _wipe(engine)
    with engine.begin() as conn:
        conn.execute(
            Instrument.__table__.insert(),
            [_instrument(FUND), _instrument(BARE), _instrument(BTC), _instrument(SPY, "etf")],
        )
        for seed, inst in enumerate(ALL):
            conn.execute(Candle.__table__.insert(), _candles(inst, "1D", D0, DAY, 400, seed))
            conn.execute(Candle.__table__.insert(), _candles(inst, "1H", H0, HOUR, 960, seed + 10))
        conn.execute(
            FundingRate.__table__.insert(),
            [
                {
                    "env": ENV,
                    "inst_id": FUND,
                    "ts": T - HOUR * 8 * i,
                    "funding_rate": Decimal("0.0001") * (1 + i % 3),
                }
                for i in range(30)
            ]
            + [
                {"env": ENV, "inst_id": FUND, "ts": T + HOUR * 8 * i, "funding_rate": Decimal("0.05")}
                for i in range(1, 5)
            ],
        )
        conn.execute(
            OpenInterest.__table__.insert(),
            [
                {"env": ENV, "inst_id": FUND, "ts": T + HOUR * h, "oi_usd": Decimal(1_000_000 + 1000 * h)}
                for h in range(-40, 20)
            ],
        )
        conn.execute(
            LongShortRatio.__table__.insert(),
            [
                {"env": ENV, "ccy": "ATL104F", "period": "1H", "ts": T - HOUR, "ratio": Decimal("1.4")},
                {"env": ENV, "ccy": "ATL104F", "period": "1H", "ts": T + HOUR, "ratio": Decimal("9.9")},
            ],
        )
        conn.execute(
            NewsItem.__table__.insert(),
            [
                _news("before", T - HOUR * 3, T - HOUR * 3, 3),
                _news("published-after", T + HOUR, T + HOUR, 5),
                _news("fetched-after", T - HOUR * 2, T + HOUR, 5),
            ],
        )
        conn.execute(
            EconomicEvent.__table__.insert(),
            [
                {
                    "source": SOURCE,
                    "external_id": "known",
                    "country": "US",
                    "name": "CPI y/y",
                    "importance": 4,
                    "scheduled_at": T + HOUR * 10,
                    "created_at": T - DAY,
                    "updated_at": T - DAY,
                },
                {
                    "source": SOURCE,
                    "external_id": "stored-later",
                    "country": "US",
                    "name": "FOMC",
                    "importance": 5,
                    "scheduled_at": T + HOUR * 20,
                    "created_at": T + HOUR,
                    "updated_at": T + HOUR,
                },
            ],
        )
    yield engine
    _wipe(engine)


def _ctx(engine: Engine) -> ToolContext:
    return ToolContext(
        env=ENV,
        session_factory=sessionmaker(bind=engine),
        agent="market_scout",
        replay_as_of=T,
        clock=lambda: WALL,
    )


def _table(engine: Engine) -> scout_features.FeatureTable:
    ctx = _ctx(engine)
    universe = build_universe(ctx)
    universe = universe.model_copy(update={"kept": [k for k in universe.kept if k.inst_id in ALL]})
    return build_feature_table(ctx, universe)


def _truncate_after_t(engine: Engine) -> None:
    """Delete every row that only became known after T."""
    with engine.begin() as conn:
        for bar, step in (("1D", DAY), ("1H", HOUR)):
            conn.execute(
                delete(Candle).where(Candle.inst_id.in_(ALL), Candle.bar == bar, Candle.ts > T - step)
            )
        conn.execute(delete(FundingRate).where(FundingRate.inst_id.in_(ALL), FundingRate.ts > T))
        conn.execute(delete(OpenInterest).where(OpenInterest.inst_id.in_(ALL), OpenInterest.ts > T))
        conn.execute(delete(LongShortRatio).where(LongShortRatio.ccy.in_(CCYS), LongShortRatio.ts > T))
        conn.execute(delete(NewsItem).where(NewsItem.source == SOURCE, NewsItem.fetched_at > T))
        conn.execute(
            delete(EconomicEvent).where(EconomicEvent.source == SOURCE, EconomicEvent.created_at > T)
        )


def test_replay_table_at_t_uses_nothing_after_t(seeded: Engine) -> None:
    before = _table(seeded)
    _truncate_after_t(seeded)
    after = _table(seeded)

    assert {r.inst_id for r in before.rows} == set(ALL)
    assert before.model_dump() == after.model_dump()

    row = next(r for r in before.rows if r.inst_id == FUND)
    assert row.data_as_of is not None and row.data_as_of <= T
    assert row.funding_rate == pytest.approx(0.0001)
    assert row.long_short_ratio == pytest.approx(1.4)
    assert row.news_count_24h == 1 and row.news_max_importance_24h == 3
    assert [c.name for c in before.shared_catalysts] == ["CPI y/y"]
    assert row.oi_change_24h_pct == pytest.approx((1_000_000 / 976_000 - 1) * 100)
    assert before.btc_reference == BTC and before.spy_reference == SPY
    assert row.corr_btc_30d is not None and row.corr_spy_30d is not None


def test_instrument_without_funding_history_has_null_funding_with_reason(seeded: Engine) -> None:
    table = _table(seeded)
    row = next(r for r in table.rows if r.inst_id == BARE)
    assert row.funding_rate is None and row.funding_z_7d is None
    assert f"no funding history stored for {BARE}" in row.missing["funding_rate"]
    assert row.returns_pct["7d"] is not None  # the rest of the row is still computed


def test_table_is_saved_as_a_feature_table_report_valid_for_15_minutes(seeded: Engine) -> None:
    table = _table(seeded)
    table = table.model_copy(update={"notes": [*table.notes, SOURCE]})
    with Session(seeded) as session:
        report = save_feature_table(session, _ctx(seeded), table)
        # The summary names the test marker so _wipe can find the row.
        report.content_md += f" [{SOURCE}]"
        session.commit()
        report_id = report.id
    with Session(seeded) as session:
        stored = session.scalars(select(ResearchReport).where(ResearchReport.id == report_id)).one()
        assert stored.agent == "market_scout"
        assert stored.kind == "feature_table"
        assert stored.env == ENV
        assert stored.inst_id is None
        assert stored.valid_until == T + timedelta(minutes=15)
        assert datetime.fromisoformat(stored.data["as_of"]) == T
        assert {r["inst_id"] for r in stored.data["rows"]} >= {FUND, BARE}
