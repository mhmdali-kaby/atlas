"""ATL-105: Market Scout pre-score and shortlist (brief §7.1 step 3).

The expected values are computed in this file from the definitions in
``atlas_agents.scout.prescore``'s docstring, with numpy and independently of the module, and the
golden order is pinned as a literal.
"""

from __future__ import annotations

import copy
import math
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pytest
from atlas_agents.scout.features import Catalyst, FeatureTable, InstrumentFeatures
from atlas_agents.scout.prescore import (
    COMPONENTS,
    build_shortlist,
    flag_correlated_pairs,
    shortlist_correlations,
)
from atlas_agents.tools.account import CorrelationPair
from atlas_agents.tools.framework import ToolContext
from atlas_shared.db import Candle, Instrument
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

AS_OF = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
TOL = 1e-9


def _row(inst_id: str, **kw: Any) -> InstrumentFeatures:
    base: dict[str, Any] = {
        "inst_id": inst_id,
        "asset_class": "crypto",
        "market": "crypto_perp",
        "volume_24h_usd": Decimal("10000000"),
        "returns_pct": {"1d": 0.0, "3d": 0.0, "7d": 1.0, "30d": 0.0},
        "atr_pct_1h": 0.5,
        "atr_pct_1d": 2.0,
        "vol_ratio_24h_30d": 1.0,
        "volume_z_30d": 0.0,
        "ma_distance_pct": {"20d": 0.0, "50d": 0.0, "200d": 0.0},
        "breakout_high_20d": False,
        "breakout_low_20d": False,
        "funding_rate": 0.0001,
        "funding_z_7d": 0.0,
        "oi_change_24h_pct": 0.0,
        "oi_series": inst_id,
        "long_short_ratio": 1.0,
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
        "data_as_of": AS_OF,
        "missing": {},
        "quality_notes": [],
    }
    base.update(kw)
    return InstrumentFeatures(**base)


def _table(rows: list[InstrumentFeatures]) -> FeatureTable:
    return FeatureTable(
        env="demo",
        as_of=AS_OF,
        valid_until=AS_OF + timedelta(minutes=15),
        settings_version=1,
        btc_reference="BTC-USDT-SWAP",
        spy_reference="SPY-USDT-SWAP",
        shared_catalysts=[],
        rows=rows,
        rejected=[],
        notes=[],
    )


def _earnings(symbol: str) -> Catalyst:
    return Catalyst(
        kind="earnings",
        name=f"{symbol} earnings",
        at=None,
        date=AS_OF.date(),
        importance=None,
        source="finnhub",
    )


# -- the 30-instrument fixture -----------------------------------------------------------------------


def _fixture_rows() -> list[InstrumentFeatures]:
    rows: list[InstrumentFeatures] = []
    for i in range(30):
        kw: dict[str, Any] = {
            "volume_24h_usd": Decimal(5_000_000 + 7_000_000 * ((i * 7) % 30)),
            "returns_pct": {"1d": 0.1 * i, "3d": 0.2 * i, "7d": ((i * 11) % 30 - 12) * 0.9, "30d": 0.0},
            "atr_pct_1d": 1.0 + (i % 5) * 0.5,
            "vol_ratio_24h_30d": 0.6 + ((i * 13) % 30) * 0.05,
            "funding_z_7d": ((i * 17) % 30 - 15) / 6,
            "oi_change_24h_pct": ((i * 19) % 30 - 10) * 1.5,
            "news_count_24h": (i * 3) % 7,
            "news_max_importance_24h": None if (i * 3) % 7 == 0 else 1 + (i % 5),
            "regime_probability": 0.4 + ((i * 23) % 30) / 50,
        }
        if i % 9 == 4:  # a stock perp with earnings in the window
            kw.update(asset_class="stock", market="stock_perp", catalysts=[_earnings(f"S{i}")])
        if i in (3, 21):  # spot: no derivatives data
            kw.update(
                market="spot",
                funding_z_7d=None,
                oi_change_24h_pct=None,
                missing={"funding_z_7d": "no funding (spot)", "oi_change_24h_pct": "no open interest (spot)"},
            )
        if i == 11:
            kw.update(
                returns_pct={"1d": None, "3d": None, "7d": None, "30d": None},
                missing={"returns_pct.7d": "8 daily bars, need 8"},
            )
        if i == 17:
            kw.update(
                regime_probability=None, regime_label=None, missing={"regime_probability": "too few bars"}
            )
        if i == 25:
            kw.update(volume_24h_usd=None, missing={"volume_24h_usd": "volume unknown"})
        rows.append(_row(f"I{i:02d}-USDT-SWAP", **kw))
    return rows


def _expected(
    rows: list[InstrumentFeatures], defaults: Any = DEFAULTS
) -> list[tuple[str, float, dict[str, Any]]]:
    """Independent implementation of the docstring, with numpy."""
    cfg = defaults["agents"]["scout"]["prescore"]
    w = {k: float(v) for k, v in cfg["weights"].items()}
    clip = float(cfg["clip_z"])

    def raw(f: InstrumentFeatures) -> dict[str, float | None]:
        r7 = f.returns_pct["7d"]
        imp = f.news_max_importance_24h if f.news_count_24h else 0
        parts = [
            x
            for x in (
                None if f.funding_z_7d is None else abs(f.funding_z_7d),
                None
                if f.oi_change_24h_pct is None
                else abs(f.oi_change_24h_pct) / float(cfg["oi_scale_pct"]),
            )
            if x is not None
        ]
        return {
            "momentum_z": None if r7 is None or not f.atr_pct_1d else abs(r7) / f.atr_pct_1d,
            "vol_expansion_z": f.vol_ratio_24h_30d,
            "catalyst_score": (imp or 0)
            + np.log1p(f.news_count_24h)
            + (float(cfg["earnings_points"]) if f.catalysts else 0.0),
            "liquidity_score": None if f.volume_24h_usd is None else np.log10(float(f.volume_24h_usd)),
            "derivatives_signal": float(np.mean(parts)) if parts else None,
            "regime_clarity": f.regime_probability,
        }

    raws = [raw(f) for f in rows]
    z: dict[str, list[float | None]] = {}
    for c in COMPONENTS:
        vals = np.array([r[c] for r in raws if r[c] is not None], dtype=float)
        mu, sd = vals.mean(), vals.std(ddof=0)
        z[c] = [None if (v := r[c]) is None else float(np.clip((v - mu) / sd, -clip, clip)) for r in raws]
    out: list[tuple[str, float, dict[str, Any]]] = []
    for i, f in enumerate(rows):
        comps: dict[str, Any] = {c: {"raw": raws[i][c], "z": z[c][i]} for c in COMPONENTS}
        score = float(sum(w[c] * (z[c][i] or 0.0) for c in COMPONENTS))
        out.append((f.inst_id, score, comps))
    out.sort(key=lambda t: (-t[1], t[0]))
    return out


GOLDEN_TOP_25 = [
    "I16-USDT-SWAP",
    "I27-USDT-SWAP",
    "I02-USDT-SWAP",
    "I11-USDT-SWAP",
    "I22-USDT-SWAP",
    "I25-USDT-SWAP",
    "I09-USDT-SWAP",
    "I23-USDT-SWAP",
    "I04-USDT-SWAP",
    "I05-USDT-SWAP",
    "I13-USDT-SWAP",
    "I18-USDT-SWAP",
    "I08-USDT-SWAP",
    "I19-USDT-SWAP",
    "I06-USDT-SWAP",
    "I24-USDT-SWAP",
    "I17-USDT-SWAP",
    "I03-USDT-SWAP",
    "I29-USDT-SWAP",
    "I10-USDT-SWAP",
    "I20-USDT-SWAP",
    "I15-USDT-SWAP",
    "I01-USDT-SWAP",
    "I21-USDT-SWAP",
    "I14-USDT-SWAP",
]


def test_golden_shortlist_order_and_components() -> None:
    rows = _fixture_rows()
    sl = build_shortlist(_table(rows))
    expected = _expected(rows)

    assert sl.universe_size == 30
    assert sl.shortlist_size == 25
    assert [r.inst_id for r in sl.rows] == GOLDEN_TOP_25
    assert [e[0] for e in expected[:25]] == GOLDEN_TOP_25
    assert all(r.in_top and not r.pinned for r in sl.rows)
    assert [r.rank for r in sl.rows] == list(range(1, 26))

    for row, (inst_id, score, comps) in zip(sl.rows, expected, strict=False):
        assert row.inst_id == inst_id
        assert abs(row.score - score) < TOL
        for c in COMPONENTS:
            got = row.components[c]
            want = comps[c]
            if want["raw"] is None:
                assert got.raw is None and got.z is None and got.contribution == 0.0
                assert c in row.null_components
            else:
                assert got.raw is not None and abs(got.raw - want["raw"]) < TOL
                assert got.z is not None and abs(got.z - want["z"]) < TOL
                assert abs(got.contribution - got.weight * want["z"]) < TOL
        assert abs(row.score - sum(v.contribution for v in row.components.values())) < TOL
        assert row.features.inst_id == inst_id


def test_nulls_contribute_zero_and_are_listed() -> None:
    sl = build_shortlist(_table(_fixture_rows()))
    by_id = {r.inst_id: r for r in sl.rows}
    for inst, comp, reason in (
        ("I21-USDT-SWAP", "derivatives_signal", "funding_z_7d: no funding (spot)"),
        ("I11-USDT-SWAP", "momentum_z", "returns_pct.7d: 8 daily bars, need 8"),
        ("I17-USDT-SWAP", "regime_clarity", "regime_probability: too few bars"),
        ("I25-USDT-SWAP", "liquidity_score", "volume_24h_usd unknown"),
    ):
        row = by_id[inst]
        assert row.components[comp].raw is None
        assert row.components[comp].z is None
        assert row.components[comp].contribution == 0.0
        assert row.null_components == {comp: reason}
    assert by_id["I16-USDT-SWAP"].null_components == {}


def test_hand_calculated_three_instruments() -> None:
    # liquidity raw = log10(volume) = 6, 7, 8 -> mean 7, population std sqrt(2/3)
    # momentum raw = |7d| / atr_1d = 2, 4, 6 -> mean 4, std sqrt(8/3); z = -1.224745, 0, 1.224745
    rows = [
        _row("A-USDT-SWAP", volume_24h_usd=Decimal("1000000"), returns_pct={"7d": -2.0}, atr_pct_1d=1.0),
        _row("B-USDT-SWAP", volume_24h_usd=Decimal("10000000"), returns_pct={"7d": 8.0}, atr_pct_1d=2.0),
        _row("C-USDT-SWAP", volume_24h_usd=Decimal("100000000"), returns_pct={"7d": 3.0}, atr_pct_1d=0.5),
    ]
    sl = build_shortlist(_table(rows))
    z = 1.224744871391589
    by_id = {r.inst_id: r for r in sl.rows}
    assert abs(by_id["A-USDT-SWAP"].components["liquidity_score"].z - (-z)) < TOL  # type: ignore[operator]
    assert abs(by_id["C-USDT-SWAP"].components["liquidity_score"].z - z) < TOL  # type: ignore[operator]
    assert abs(by_id["A-USDT-SWAP"].components["momentum_z"].raw - 2.0) < TOL  # type: ignore[operator]
    assert abs(by_id["C-USDT-SWAP"].components["momentum_z"].z - z) < TOL  # type: ignore[operator]
    assert by_id["B-USDT-SWAP"].components["momentum_z"].z == 0.0
    # identical vol ratio, funding, OI, regime and news everywhere: those components are uninformative
    assert by_id["B-USDT-SWAP"].components["regime_clarity"].z == 0.0
    assert any(n.startswith("regime_clarity:") for n in sl.notes)
    # C: 0.25*z + 0.15*z; B: 0; A: -0.40*z
    assert abs(by_id["C-USDT-SWAP"].score - 0.40 * z) < TOL
    assert abs(by_id["A-USDT-SWAP"].score + 0.40 * z) < TOL
    assert [r.inst_id for r in sl.rows] == ["C-USDT-SWAP", "B-USDT-SWAP", "A-USDT-SWAP"]


def test_changing_a_weight_in_config_changes_the_ranking() -> None:
    rows = _fixture_rows()
    defaults = copy.deepcopy(DEFAULTS)
    weights = defaults["agents"]["scout"]["prescore"]["weights"]
    for k in weights:
        weights[k] = "0"
    weights["liquidity_score"] = "1"

    sl = build_shortlist(_table(rows), defaults=defaults)
    # Liquidity alone: ordered by 24h volume, highest first; I25 (volume unknown) scores 0.
    known = sorted(
        (r for r in rows if r.volume_24h_usd is not None),
        key=lambda r: (-float(r.volume_24h_usd or 0), r.inst_id),
    )
    assert [r.inst_id for r in sl.rows][:10] == [r.inst_id for r in known][:10]
    assert [r.inst_id for r in sl.rows] != GOLDEN_TOP_25
    assert [e[0] for e in _expected(rows, defaults)[:25]] == [r.inst_id for r in sl.rows]

    defaults["agents"]["scout"]["prescore"]["shortlist_size"] = 5
    assert len(build_shortlist(_table(rows), defaults=defaults).rows) == 5


def test_owner_pinned_instrument_ranked_28th_is_included() -> None:
    rows = _fixture_rows()
    expected = _expected(rows)
    rank_28 = expected[27][0]
    sl = build_shortlist(_table(rows), pinned=[rank_28, "BLOCKED-USDT-SWAP"])
    assert len(sl.rows) == 26
    assert [r.inst_id for r in sl.rows[:25]] == GOLDEN_TOP_25
    pinned = sl.rows[25]
    assert pinned.inst_id == rank_28
    assert pinned.rank == 28
    assert pinned.pinned and not pinned.in_top
    assert [p.inst_id for p in sl.pinned_missing] == ["BLOCKED-USDT-SWAP"]


def test_a_pin_inside_the_top_is_marked_not_duplicated() -> None:
    sl = build_shortlist(_table(_fixture_rows()), pinned=["I27-USDT-SWAP"])
    assert len(sl.rows) == 25
    assert [r.pinned for r in sl.rows].count(True) == 1 and sl.rows[1].pinned


def test_empty_table() -> None:
    sl = build_shortlist(_table([]))
    assert sl.rows == [] and sl.universe_size == 0 and sl.notes


def test_unknown_weight_is_refused() -> None:
    defaults = copy.deepcopy(DEFAULTS)
    defaults["agents"]["scout"]["prescore"]["weights"]["typo_z"] = "0.1"
    with pytest.raises(ValueError, match="typo_z"):
        build_shortlist(_table(_fixture_rows()), defaults=defaults)


# -- correlation flag --------------------------------------------------------------------------------


def test_flag_correlated_pairs_above_threshold_only() -> None:
    pairs = [
        CorrelationPair(a="A", b="B", corr=0.9, n=30),
        CorrelationPair(a="A", b="C", corr=0.8, n=30),  # not strictly above
        CorrelationPair(a="B", b="C", corr=-0.95, n=30),
        CorrelationPair(a="C", b="D", corr=None, n=2),
        CorrelationPair(a="D", b="E", corr=0.97, n=30),
    ]
    flagged = flag_correlated_pairs(pairs, 0.8)
    assert [(p.a, p.b, p.corr) for p in flagged] == [("D", "E", 0.97), ("A", "B", 0.9)]


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


def _inst(inst_id: str) -> Instrument:
    return Instrument(
        env="demo",
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
        list_time=AS_OF - timedelta(days=400),
        created_at=AS_OF - timedelta(days=400),
        updated_at=AS_OF - timedelta(days=400),
        last_seen_at=AS_OF - timedelta(hours=1),
    )


def _candle(inst_id: str, ts: datetime, close: float) -> Candle:
    c = Decimal(repr(close))
    return Candle(
        env="demo",
        inst_id=inst_id,
        bar="1D",
        ts=ts,
        o=c,
        h=c,
        l=c,
        c=c,
        vol=Decimal("10"),
        vol_ccy=Decimal("1"),
        vol_ccy_quote=Decimal("1000"),
        confirmed=True,
    )


def test_shortlist_pairs_with_30d_correlation_0_9_are_flagged(db: sessionmaker[Session]) -> None:
    uid = uuid.uuid4().hex[:6].upper()
    a, b, c = f"PA{uid}-USDT-SWAP", f"PB{uid}-USDT-SWAP", f"PC{uid}-USDT-SWAP"
    days = 29  # a 30-day window holds 30 closed 1D bars, so 29 daily returns
    rng = np.random.default_rng(7)
    x = rng.normal(0, 0.02, days)
    noise = rng.normal(0, 0.02, days)
    # Orthogonalise the noise against x and scale so the sample correlation is exactly 0.9.
    xc, nc = x - x.mean(), noise - noise.mean()
    nc = nc - xc * (nc @ xc) / (xc @ xc)
    y = 0.9 * xc / np.linalg.norm(xc) + math.sqrt(1 - 0.81) * nc / np.linalg.norm(nc)
    zr = rng.normal(0, 0.02, days)
    zr = zr - xc * (zr - zr.mean()) @ xc / (xc @ xc)  # uncorrelated with x
    expected = float(np.corrcoef(x, y)[0, 1])
    assert abs(expected - 0.9) < 1e-9

    last_open = datetime(2026, 10, 28, 16, 0, tzinfo=UTC)  # closed by AS_OF
    with db() as s:
        s.add_all([_inst(a), _inst(b), _inst(c)])
        pa = pb = pc = 100.0
        for i in range(days + 1):
            ts = last_open - timedelta(days=days - i)
            if i > 0:
                pa *= math.exp(x[i - 1])
                pb *= math.exp(y[i - 1] * 0.02)
                pc *= math.exp(zr[i - 1])
            s.add_all([_candle(a, ts, pa), _candle(b, ts, pb), _candle(c, ts, pc)])
        s.commit()

    ctx = ToolContext(env="demo", session_factory=db, agent="market_scout", replay_as_of=AS_OF)
    sl = build_shortlist(_table([_row(a), _row(b, atr_pct_1d=3.0), _row(c, atr_pct_1d=4.0)]))
    assert sl.correlated_pairs is None
    sl = shortlist_correlations(ctx, sl)
    assert sl.correlated_pairs is not None and len(sl.correlated_pairs) == 1
    pair = sl.correlated_pairs[0]
    assert {pair.a, pair.b} == {a, b}
    assert pair.n == days
    assert abs(pair.corr - expected) < 1e-6  # exactly 0.9 by construction
    assert sl.correlation_threshold == 0.8
