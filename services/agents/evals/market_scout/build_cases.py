"""Generate Market Scout's replay eval cases (ATL-112, brief §7.1, §7.11).

Run from services/:  uv run python agents/evals/market_scout/build_cases.py

Each case is a replayed 08:00 Baghdad watchlist run (``atlas_agents.evals.drivers``):

* ``data/research_reports.json`` — the stored feature table (kind ``feature_table``) code reuses,
  so the shortlist, its pre-score and the prompt are built by the real pipeline;
* ``data/candles.json`` — 1D candles for every row: the 30-day correlation flags are computed
  from them, and the fake answer's key levels come from ``okx_market.get_candles``;
* ``data/news_sources.json`` — one live source per news category (no class is news-down);
* owner cases add today's earlier watchlist (pins, removals) and a settings blocklist;
* ``inputs.replies`` — the News Intelligence / Historian replies; one left out times out.

The ``fake`` script is a deterministic stand-in for the model: one ``get_candles`` call per pick,
then a short answer ending with the ScoutAnswer JSON. Every number it writes is taken from the
feature table, the candles or a reply, so the number guard has a source for it.

Categories (case id prefix, at least 3 cases each; tests/test_market_scout_evals.py):
mixed_ (normal day, mixed asset classes), notrade_ (known no-trade day), chasing_ (> 3 ATR
extension not picked), correlated_ (<= 2 picks with corr > 0.8), timeout_ (partial evidence,
conviction <= 0.5), owner_ (pins kept, blocked/removed dropped), single_ (one-evidence pick rejected).
"""

from __future__ import annotations

import json
import math
import random
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

HERE = Path(__file__).resolve().parent
ENV = "demo"
AGENT = "market_scout"
DAYS = 34  # 1D bars before the replayed day

CLASS = {
    "BTC-USDT-SWAP": "crypto",
    "ETH-USDT-SWAP": "crypto",
    "SOL-USDT-SWAP": "crypto",
    "XRP-USDT-SWAP": "crypto",
    "DOGE-USDT-SWAP": "crypto",
    "AVAX-USDT-SWAP": "crypto",
    "LINK-USDT-SWAP": "crypto",
    "TSLA-USDT-SWAP": "stock",
    "NVDA-USDT-SWAP": "stock",
    "META-USDT-SWAP": "stock",
    "SPY-USDT-SWAP": "etf",
    "QQQ-USDT-SWAP": "etf",
    "XAU-USDT-SWAP": "commodity",
    "XAG-USDT-SWAP": "commodity",
    "CL-USDT-SWAP": "commodity",
}
PRICE = {
    "BTC-USDT-SWAP": 68_000.0,
    "ETH-USDT-SWAP": 2_600.0,
    "SOL-USDT-SWAP": 160.0,
    "XRP-USDT-SWAP": 0.55,
    "DOGE-USDT-SWAP": 0.13,
    "AVAX-USDT-SWAP": 27.0,
    "LINK-USDT-SWAP": 12.0,
    "TSLA-USDT-SWAP": 250.0,
    "NVDA-USDT-SWAP": 125.0,
    "META-USDT-SWAP": 580.0,
    "SPY-USDT-SWAP": 570.0,
    "QQQ-USDT-SWAP": 490.0,
    "XAU-USDT-SWAP": 2_650.0,
    "XAG-USDT-SWAP": 31.0,
    "CL-USDT-SWAP": 72.0,
}
MARKET = {"crypto": "crypto_perp", "stock": "stock_perp", "etf": "stock_perp", "commodity": "commodity_perp"}


def _places(price: float) -> int:
    return 4 if price < 1 else 3 if price < 50 else 2


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


# -- feature rows -------------------------------------------------------------------------------------


@dataclass
class Row:
    inst_id: str
    r1: float = 0.4
    r3: float = 1.1
    r7: float = 2.3
    r30: float = 4.2
    atr1h: float = 0.45
    atr1d: float = 2.4
    vol_ratio: float = 1.1
    vol_z: float = 0.3
    funding: float | None = 0.0001
    funding_z: float | None = 0.4
    oi: float | None = 1.5
    ls: float | None = 1.05
    news: int = 0
    news_imp: int | None = None
    regime: str | None = "trend"
    regime_p: float | None = 0.66
    breakout_high: bool = False
    own_catalysts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def cls(self) -> str:
        return CLASS[self.inst_id]

    def features(self, as_of: datetime, shared: list[dict[str, Any]]) -> dict[str, Any]:
        catalysts = self.own_catalysts
        relevant = shared + catalysts
        missing: dict[str, str] = {}
        if self.funding is None:
            missing["funding_rate"] = "no funding history"
            missing["funding_z_7d"] = "no funding history"
        if self.oi is None:
            missing["oi_change_24h_pct"] = "no open interest history"
        if self.ls is None:
            missing["long_short_ratio"] = "no long/short ratio"
        if self.regime is None:
            missing["regime_label"] = "fewer than 120 1D bars"
        return {
            "inst_id": self.inst_id,
            "asset_class": self.cls,
            "market": MARKET[self.cls],
            "volume_24h_usd": "85000000",
            "returns_pct": {"1d": self.r1, "3d": self.r3, "7d": self.r7, "30d": self.r30},
            "atr_pct_1h": self.atr1h,
            "atr_pct_1d": self.atr1d,
            "vol_ratio_24h_30d": self.vol_ratio,
            "volume_z_30d": self.vol_z,
            "ma_distance_pct": {"20d": round(self.r7 / 2, 2), "50d": round(self.r30 / 2, 2), "200d": 6.4},
            "breakout_high_20d": self.breakout_high,
            "breakout_low_20d": False,
            "funding_rate": self.funding,
            "funding_z_7d": self.funding_z,
            "oi_change_24h_pct": self.oi,
            "oi_series": self.inst_id if self.oi is not None else None,
            "long_short_ratio": self.ls,
            "corr_btc_30d": 0.62 if self.cls == "crypto" else 0.18,
            "corr_spy_30d": 0.21 if self.cls == "crypto" else 0.55,
            "news_count_24h": self.news,
            "news_max_importance_24h": self.news_imp,
            "catalysts": catalysts,
            "catalyst_count_48h": len(relevant),
            "catalyst_max_importance_48h": max((c["importance"] or 0 for c in relevant), default=None)
            or None,
            "regime_label": self.regime,
            "regime_probability": self.regime_p,
            "regime_method": "hmm" if self.regime else None,
            "data_as_of": _iso(as_of),
            "missing": missing,
            "quality_notes": [],
        }


def cpi(day: date) -> dict[str, Any]:
    at = datetime(day.year, day.month, day.day, 12, 30, tzinfo=UTC) + timedelta(days=1)
    return {
        "kind": "economic",
        "name": "US CPI m/m",
        "at": _iso(at),
        "date": at.date().isoformat(),
        "importance": 5,
        "country": "US",
        "source": "forexfactory",
    }


def earnings(symbol: str, day: date) -> dict[str, Any]:
    on = day + timedelta(days=1)
    return {
        "kind": "earnings",
        "name": f"{symbol} earnings",
        "at": None,
        "date": on.isoformat(),
        "importance": None,
        "country": None,
        "source": "finnhub",
    }


# -- candles ------------------------------------------------------------------------------------------


def candles(
    rows: list[Row], day: date, groups: list[list[str]], seed: int
) -> dict[str, list[dict[str, Any]]]:
    """Daily bars for DAYS days before ``day``. Instruments in one group share a market factor, so
    their 30-day correlation is high (> 0.9); the others move independently."""
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture data
    factor = {i: [rng.gauss(0, 0.02) for _ in range(DAYS)] for i in range(len(groups))}
    member = {inst: gi for gi, g in enumerate(groups) for inst in g}
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        inst = row.inst_id
        own = random.Random(f"{seed}-{inst}")  # noqa: S311 - deterministic fixture data
        price = PRICE[inst]
        places = _places(price)
        bars: list[dict[str, Any]] = []
        for k in range(DAYS):
            ts = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(days=DAYS - k)
            gi = member.get(inst)
            ret = (factor[gi][k] + own.gauss(0, 0.003)) if gi is not None else own.gauss(0, 0.015)
            o = price
            c = price * math.exp(ret)
            h = max(o, c) * (1 + abs(own.gauss(0, 0.006)))
            lo = min(o, c) * (1 - abs(own.gauss(0, 0.006)))
            bars.append(
                {
                    "env": ENV,
                    "inst_id": inst,
                    "bar": "1D",
                    "ts": _iso(ts),
                    "o": f"{o:.{places}f}",
                    "h": f"{h:.{places}f}",
                    "l": f"{lo:.{places}f}",
                    "c": f"{c:.{places}f}",
                    "vol": "125000",
                    "vol_ccy": None,
                    "vol_ccy_quote": "85000000",
                    "confirmed": True,
                }
            )
            price = float(f"{c:.{places}f}")
        out[inst] = bars
    return out


def levels(bars: list[dict[str, Any]]) -> dict[str, float]:
    """What the fake answer reads off the last 20 closed bars: support = lowest low, resistance =
    highest high."""
    last = bars[-20:]
    return {
        "support": float(min(Decimal(b["l"]) for b in last)),
        "resistance": float(max(Decimal(b["h"]) for b in last)),
    }


# -- one case -----------------------------------------------------------------------------------------


@dataclass
class Pick:
    inst_id: str
    bias: str
    conviction: float
    why: str  # one sentence of evidence, numbers from the row
    catalysts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Case:
    case_id: str
    description: str
    day: date
    rows: list[Row]
    picks: list[Pick]
    rejected: list[tuple[str, str]]
    properties: dict[str, Any]
    shared: list[dict[str, Any]] = field(default_factory=list)
    groups: list[list[str]] = field(default_factory=list)
    replies: dict[str, dict[str, Any]] | None = None
    no_pick_reason: str | None = None
    table_age_minutes: int = 5
    hour_utc: int = 5
    blocklist: list[str] = field(default_factory=list)
    pinned: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    seed: int = 1


def default_replies(case: Case) -> dict[str, dict[str, Any]]:
    news = [r for r in case.rows if r.news > 0]
    news_text = (
        "; ".join(f"{r.inst_id}: {r.news} stories in 24h, top importance {r.news_imp}" for r in news)
        or "No instrument-specific stories in 24h; the 48h calendar is the main catalyst."
    )
    regimes = "; ".join(f"{r.inst_id} {r.regime or 'unknown'}" for r in case.rows[:8])
    return {
        "news_intelligence": {
            "kind": "text",
            "content_md": f"Briefs for the shortlist. {news_text}.",
            "card": None,
        },
        "historian": {
            "kind": "text",
            "content_md": f"Regime check for the top candidates: {regimes}.",
            "card": None,
        },
    }


def _thesis(pick: Pick, row: Row) -> str:
    return (
        f"{pick.why} Regime is {row.regime or 'unknown'} and the 7d return is {row.r7}% "
        f"with ATR(14) on 1D at {row.atr1d}%. It beats the rejected names on combined evidence."
    )


def _pick_json(
    rank: int, pick: Pick, row: Row, bars: list[dict[str, Any]], as_of: datetime
) -> dict[str, Any]:
    lv = levels(bars)
    return {
        "inst_id": pick.inst_id,
        "rank": rank,
        "asset_class": row.cls,
        "bias": pick.bias,
        "conviction": pick.conviction,
        "thesis": _thesis(pick, row),
        "catalysts": [
            {"name": c["name"], "time": c["at"], "source": c["source"], "importance": c["importance"] or 3}
            for c in pick.catalysts
        ],
        "key_levels": {
            "support": lv["support"],
            "resistance": lv["resistance"],
            "invalidation": lv["support"] if pick.bias != "short" else lv["resistance"],
        },
        "invalidation": "A daily close beyond the 20-day range on the wrong side of the bias.",
        "data_as_of": _iso(as_of),
    }


def _backdrop(case: Case) -> list[str]:
    by = {r.inst_id: r for r in case.rows}
    btc = by.get("BTC-USDT-SWAP")
    first = (
        f"BTC is in a {btc.regime or 'unclear'} regime, 7d return {btc.r7}%."
        if btc
        else "Crypto majors are not on today's shortlist."
    )
    others = [r for r in case.rows if r.cls != "crypto"]
    second = (
        f"{others[0].inst_id} leads the non-crypto names, 7d return {others[0].r7}%."
        if others
        else "No stock or commodity perp made the shortlist."
    )
    third = (
        f"Main event in 48h: {case.shared[0]['name']}."
        if case.shared
        else "No high-impact event in the next 48h."
    )
    return [first, second, third]


def build(case: Case) -> None:
    folder = HERE / case.case_id
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "data").mkdir(parents=True)
    replay = datetime(case.day.year, case.day.month, case.day.day, case.hour_utc, tzinfo=UTC)
    as_of = replay - timedelta(minutes=case.table_age_minutes)
    by = {r.inst_id: r for r in case.rows}
    bars = candles(case.rows, case.day, case.groups, case.seed)

    table = {
        "env": ENV,
        "as_of": _iso(as_of),
        "valid_until": _iso(replay + timedelta(minutes=10)),
        "settings_version": None,
        "btc_reference": "BTC-USDT-SWAP" if "BTC-USDT-SWAP" in by else None,
        "spy_reference": None,
        "shared_catalysts": case.shared,
        "rows": [r.features(as_of, case.shared) for r in case.rows],
        "rejected": [],
        "notes": [],
    }
    data: dict[str, list[dict[str, Any]]] = {
        "research_reports": [
            {
                "env": ENV,
                "agent": AGENT,
                "inst_id": None,
                "kind": "feature_table",
                "content_md": f"Feature table: {len(case.rows)} instruments.",
                "data": table,
                "confidence": None,
                "valid_until": table["valid_until"],
                "created_at": _iso(as_of),
                "updated_at": _iso(as_of),
            }
        ],
        "candles": [b for inst in by for b in bars[inst]],
        "news_sources": [
            {
                "key": f"eval_{cat}",
                "provider": "rss",
                "kind": "rss",
                "category": cat,
                "name": f"Eval {cat}",
                "url": None,
                "enabled": True,
                "poll_seconds": 300,
                "requires_key": False,
                "reputable": True,
                "official": False,
                "api_key": None,
                "consecutive_failures": 0,
                "disabled_until": None,
                "last_success_at": _iso(replay - timedelta(minutes=3)),
                "last_item_at": _iso(replay - timedelta(minutes=8)),
                "last_error": None,
            }
            for cat in ("crypto_news", "markets_news")
        ],
    }
    if case.pinned or case.removed:
        earlier = replay - timedelta(minutes=50)
        wid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"atlas-eval/{case.case_id}/watchlist"))
        data["watchlists"] = [
            {
                "id": wid,
                "env": ENV,
                "date": case.day.isoformat(),
                "version": 1,
                "created_by": AGENT,
                "status": "published",
                "edit_window_ends_at": _iso(earlier + timedelta(minutes=10)),
                "reason": None,
                "message_id": None,
                "data": {"trigger": "schedule"},
                "created_at": _iso(earlier),
                "updated_at": _iso(earlier),
            }
        ]
        items = []
        for rank, inst in enumerate([*case.pinned, *case.removed], start=1):
            row = by[inst]
            items.append(
                {
                    "watchlist_id": wid,
                    "inst_id": inst,
                    "rank": rank,
                    "asset_class": row.cls,
                    "bias": "long",
                    "conviction": "0.5",
                    "thesis": "Owner pin." if inst in case.pinned else "Earlier pick.",
                    "catalysts": [],
                    "key_levels": {},
                    "invalidation": "Owner pin: no invalidation set yet.",
                    "pinned_by_owner": inst in case.pinned,
                    "removed_by_owner_at": _iso(earlier + timedelta(minutes=5))
                    if inst in case.removed
                    else None,
                    "data_as_of": _iso(earlier),
                    "created_at": _iso(earlier),
                    "updated_at": _iso(earlier + timedelta(minutes=5)),
                }
            )
        data["watchlist_items"] = items
    # Every case has the owner's settings (the Scout prompt needs MAX_PICKS); owner cases add a blocklist.
    sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"atlas-eval/{case.case_id}/settings"))
    settings_data: dict[str, Any] = {"max_instruments_per_day": 5, "min_expected_r": "1.8", "max_leverage": 5}
    if case.blocklist:
        settings_data["blocklist"] = case.blocklist
    saved_at = _iso(replay - timedelta(days=2))
    data["settings"] = [
        {
            "id": sid,
            "env": ENV,
            "version": 1,
            "data": settings_data,
            "risk_profile": "balanced",
            "autonomy_level": 2,
            "max_loss_per_trade_pct": "1.0",
            "max_position_pct": "5.0",
            "max_leverage": 5,
            "created_at": saved_at,
            "updated_at": saved_at,
        }
    ]
    data["settings_history"] = [
        {
            "settings_id": sid,
            "version": 1,
            "data": settings_data,
            "diff": {},
            "changed_by": None,
            "created_at": saved_at,
        }
    ]
    for name, rows in data.items():
        body = "[\n" + ",\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n]\n"
        (folder / "data" / f"{name}.json").write_text(body, encoding="utf-8")

    steps: list[dict[str, Any]] = [
        {"tool": "okx_market.get_candles", "input": {"inst_id": p.inst_id, "bar": "1D", "limit": 20}}
        for p in case.picks
    ]
    answer = {
        "type": "watchlist",
        "backdrop": _backdrop(case),
        "picks": [
            _pick_json(rank, p, by[p.inst_id], bars[p.inst_id], as_of)
            for rank, p in enumerate(case.picks, start=1)
        ],
        "rejected": [{"inst_id": i, "reason": why} for i, why in case.rejected],
        "no_pick_reason": case.no_pick_reason,
    }
    listed = ", ".join(f"{p.inst_id} {p.bias}" for p in case.picks) or "no picks"
    prose = f"{answer['backdrop'][0]} Today: {listed}."
    steps.append({"text": f"{prose}\n\n```json\n{json.dumps(answer, indent=1)}\n```"})
    steps.append({"result": {"tokens_in": 9000, "tokens_out": 900, "turns": len(case.picks) + 1}})

    doc: dict[str, Any] = {
        "agent": AGENT,
        "replay_as_of": _iso(replay),
        "env": ENV,
        "description": case.description,
        "prompt": f"Market Scout watchlist for {case.day.isoformat()} ({case.case_id.split('_')[0]} case)",
        "properties": case.properties,
        "inputs": {"replies": default_replies(case) if case.replies is None else case.replies},
        "fake": {"steps": steps},
    }
    (folder / "case.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8"
    )


# -- the cases ----------------------------------------------------------------------------------------

BASE = {"valid_watchlist": {}, "no_invented_numbers": {}, "rejected_listed": {"count": 3}}


def props(**extra: Any) -> dict[str, Any]:
    return {**BASE, **extra}


def mixed_cases() -> list[Case]:
    out: list[Case] = []
    specs = [
        (
            "XAU-USDT-SWAP",
            "TSLA-USDT-SWAP",
            "SOL-USDT-SWAP",
            ["ETH-USDT-SWAP", "SPY-USDT-SWAP", "XRP-USDT-SWAP"],
        ),
        (
            "NVDA-USDT-SWAP",
            "BTC-USDT-SWAP",
            "XAG-USDT-SWAP",
            ["META-USDT-SWAP", "DOGE-USDT-SWAP", "CL-USDT-SWAP"],
        ),
        (
            "ETH-USDT-SWAP",
            "CL-USDT-SWAP",
            "QQQ-USDT-SWAP",
            ["LINK-USDT-SWAP", "XAU-USDT-SWAP", "TSLA-USDT-SWAP"],
        ),
        (
            "META-USDT-SWAP",
            "XAU-USDT-SWAP",
            "AVAX-USDT-SWAP",
            ["SOL-USDT-SWAP", "NVDA-USDT-SWAP", "XAG-USDT-SWAP"],
        ),
        ("SOL-USDT-SWAP", "SPY-USDT-SWAP", None, ["BTC-USDT-SWAP", "XAU-USDT-SWAP", "QQQ-USDT-SWAP"]),
        (
            "XAG-USDT-SWAP",
            "TSLA-USDT-SWAP",
            "LINK-USDT-SWAP",
            ["ETH-USDT-SWAP", "CL-USDT-SWAP", "SPY-USDT-SWAP"],
        ),
    ]
    for n, (a, b, c, others) in enumerate(specs, start=1):
        day = date(2026, 10, 5 + n)
        shared = [cpi(day)]
        chosen = [x for x in (a, b, c) if x]
        rows = [
            Row(
                x,
                r1=0.9 + k / 10,
                r3=2.1 + k / 10,
                r7=4.4 - k / 5,
                vol_ratio=1.5,
                vol_z=1.4,
                news=3,
                news_imp=4,
            )
            for k, x in enumerate(chosen)
        ]
        rows += [
            Row(x, r1=0.2, r3=0.5, r7=0.9 + k / 10, vol_ratio=0.9, vol_z=-0.2) for k, x in enumerate(others)
        ]
        rows += [
            Row("DOGE-USDT-SWAP" if "DOGE-USDT-SWAP" not in chosen + others else "AVAX-USDT-SWAP", r7=0.4)
        ]
        picks = [
            Pick(
                x,
                "long" if k != 1 else "short",
                [0.72, 0.65, 0.58][k],
                f"{rows[k].news} stories in 24h (top importance {rows[k].news_imp}) "
                f"and volume z {rows[k].vol_z}.",
                catalysts=shared,
            )
            for k, x in enumerate(chosen)
        ]
        rejected = [(x, f"{x}: no news and volume z {-0.2} vs 30 days; momentum alone.") for x in others]
        out.append(
            Case(
                f"mixed_{n:02d}_{a.split('-')[0].lower()}_{b.split('-')[0].lower()}",
                "Normal day: catalysts across asset classes; picks diversify across classes.",
                day,
                rows,
                picks,
                rejected,
                props(asset_classes={"min": 2}, verdict_in={"field": "status", "values": ["published"]}),
                shared=shared,
                seed=100 + n,
            )
        )
    return out


def notrade_cases() -> list[Case]:
    out: list[Case] = []
    names = [
        [
            "BTC-USDT-SWAP",
            "ETH-USDT-SWAP",
            "XAU-USDT-SWAP",
            "SPY-USDT-SWAP",
            "SOL-USDT-SWAP",
            "TSLA-USDT-SWAP",
        ],
        [
            "ETH-USDT-SWAP",
            "XRP-USDT-SWAP",
            "QQQ-USDT-SWAP",
            "XAG-USDT-SWAP",
            "LINK-USDT-SWAP",
            "NVDA-USDT-SWAP",
        ],
        [
            "BTC-USDT-SWAP",
            "AVAX-USDT-SWAP",
            "CL-USDT-SWAP",
            "META-USDT-SWAP",
            "DOGE-USDT-SWAP",
            "SPY-USDT-SWAP",
        ],
        [
            "SOL-USDT-SWAP",
            "XAU-USDT-SWAP",
            "TSLA-USDT-SWAP",
            "ETH-USDT-SWAP",
            "QQQ-USDT-SWAP",
            "XAG-USDT-SWAP",
        ],
        [
            "BTC-USDT-SWAP",
            "NVDA-USDT-SWAP",
            "LINK-USDT-SWAP",
            "XAU-USDT-SWAP",
            "META-USDT-SWAP",
            "CL-USDT-SWAP",
        ],
    ]
    for n, insts in enumerate(names, start=1):
        day = date(2026, 10, 12 + n)  # quiet days: no catalysts, low volume, range regimes
        rows = [
            Row(
                x,
                r1=0.1,
                r3=-0.2 + k / 10,
                r7=0.3 - k / 10,
                r30=0.8,
                atr1d=1.3,
                vol_ratio=0.6,
                vol_z=-1.1,
                funding_z=0.1,
                oi=-0.4,
                regime="range",
                regime_p=0.81,
            )
            for k, x in enumerate(insts)
        ]
        one = n % 2 == 0  # two cases keep one low-conviction pick, three pick nothing
        picks = (
            [
                Pick(
                    insts[0],
                    "neutral",
                    0.4,
                    f"Range regime with probability {rows[0].regime_p}; levels only.",
                )
            ]
            if one
            else []
        )
        pool = insts[1:] if one else insts
        rejected = [
            (x, f"{x}: range regime, volume z {-1.1} and vol ratio {0.6}; no catalyst.") for x in pool[:3]
        ]
        out.append(
            Case(
                f"notrade_{n:02d}_quiet_{day.isoformat()}",
                "Known no-trade day: flat ranges, falling volume, no catalyst in 48h.",
                day,
                rows,
                picks,
                rejected,
                props(no_trade_day={"max_picks": 1, "max_conviction": 0.5}),
                no_pick_reason=None
                if one
                else "Every shortlist name is in a range regime with falling volume and no catalyst.",
                seed=200 + n,
            )
        )
    return out


def chasing_cases() -> list[Case]:
    out: list[Case] = []
    specs = [
        ("SOL-USDT-SWAP", 11.8, 2.9, ["XAU-USDT-SWAP", "NVDA-USDT-SWAP"]),
        ("TSLA-USDT-SWAP", 14.6, 3.4, ["ETH-USDT-SWAP", "XAG-USDT-SWAP"]),
        ("DOGE-USDT-SWAP", 18.2, 4.1, ["BTC-USDT-SWAP", "SPY-USDT-SWAP"]),
        ("XAG-USDT-SWAP", 9.6, 2.2, ["META-USDT-SWAP", "AVAX-USDT-SWAP"]),
    ]
    for n, (hot, move, atr, good) in enumerate(specs, start=1):
        day = date(2026, 10, 18 + n)
        rows = [
            Row(
                hot,
                r1=move,
                r3=move + 2.0,
                r7=move + 4.0,
                atr1d=atr,
                vol_ratio=2.6,
                vol_z=3.1,
                news=4,
                news_imp=4,
                breakout_high=True,
                funding_z=2.8,
            ),
            *(Row(x, r1=0.8, r3=1.9, r7=3.5, vol_ratio=1.4, vol_z=1.2, news=2, news_imp=3) for x in good),
        ]
        fill = [
            x
            for x in ("LINK-USDT-SWAP", "QQQ-USDT-SWAP", "CL-USDT-SWAP", "XRP-USDT-SWAP")
            if x not in [*good, hot]
        ]
        rows += [Row(x, r7=0.6) for x in fill[:3]]
        ext = round(move / atr, 1)
        picks = [
            Pick(
                x,
                "long",
                [0.66, 0.6][k],
                f"{rows[k + 1].news} stories in 24h and vol ratio {rows[k + 1].vol_ratio}.",
            )
            for k, x in enumerate(good)
        ]
        rejected = [
            (hot, f"{hot}: already moved {move}% in 1d against ATR {atr}%, about {ext} ATR; chasing."),
            (fill[0], f"{fill[0]}: no catalyst and volume z {0.3}."),
            (fill[1], f"{fill[1]}: no catalyst and volume z {0.3}."),
        ]
        out.append(
            Case(
                f"chasing_{n:02d}_{hot.split('-')[0].lower()}",
                f"{hot} already moved more than 3 ATR in a day; it must not be picked.",
                day,
                rows,
                picks,
                rejected,
                props(not_picked={"inst_ids": [hot]}),
                seed=300 + n,
            )
        )
    return out


def correlated_cases() -> list[Case]:
    out: list[Case] = []
    specs = [
        (["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"], "XAU-USDT-SWAP"),
        (["ETH-USDT-SWAP", "SOL-USDT-SWAP", "AVAX-USDT-SWAP", "LINK-USDT-SWAP"], "TSLA-USDT-SWAP"),
        (["SPY-USDT-SWAP", "QQQ-USDT-SWAP", "NVDA-USDT-SWAP"], "BTC-USDT-SWAP"),
        (["XAU-USDT-SWAP", "XAG-USDT-SWAP", "CL-USDT-SWAP"], "SOL-USDT-SWAP"),
    ]
    for n, (group, other) in enumerate(specs, start=1):
        day = date(2026, 9, 20 + n)
        rows = [
            Row(x, r1=1.1 + k / 10, r3=2.6, r7=5.1 - k / 5, vol_ratio=1.6, vol_z=1.7, news=3, news_imp=4)
            for k, x in enumerate(group)
        ]
        rows.append(Row(other, r1=0.7, r3=1.6, r7=3.2, vol_ratio=1.3, vol_z=1.1, news=2, news_imp=4))
        extra = [
            x
            for x in ("DOGE-USDT-SWAP", "META-USDT-SWAP", "XRP-USDT-SWAP", "QQQ-USDT-SWAP")
            if x not in [*group, other]
        ]
        rows += [Row(x, r7=0.5) for x in extra[:2]]
        picks = [
            Pick(group[0], "long", 0.7, f"{rows[0].news} stories in 24h and volume z {rows[0].vol_z}."),
            Pick(
                other, "long", 0.62, f"{rows[len(group)].news} stories in 24h; uncorrelated with the group."
            ),
            Pick(group[1], "long", 0.55, f"Volume z {rows[1].vol_z}; second name of the group."),
        ]
        rest = group[2:]
        rejected = [
            (
                x,
                f"{x}: same 30-day move as {group[0]} (corr above the limit); "
                "a third pick adds no diversification.",
            )
            for x in rest
        ]
        rejected += [(x, f"{x}: no catalyst and volume z {0.3}.") for x in extra[: 3 - len(rejected)]]
        out.append(
            Case(
                f"correlated_{n:02d}_{group[0].split('-')[0].lower()}_group",
                "Highly correlated candidates: at most 2 picks with 30-day correlation above 0.8.",
                day,
                rows,
                picks,
                rejected[:3],
                props(max_correlated={"groups": [group], "max": 2}),
                groups=[group],
                seed=400 + n,
            )
        )
    return out


def timeout_cases() -> list[Case]:
    out: list[Case] = []
    specs = [
        ("historian", ["XAU-USDT-SWAP", "BTC-USDT-SWAP"]),
        ("news_intelligence", ["NVDA-USDT-SWAP", "ETH-USDT-SWAP"]),
        ("both", ["SOL-USDT-SWAP", "XAG-USDT-SWAP"]),
        ("historian", ["TSLA-USDT-SWAP", "LINK-USDT-SWAP"]),
    ]
    for n, (missing, chosen) in enumerate(specs, start=1):
        day = date(2026, 9, 26 + n)
        shared = [cpi(day)]
        rows = [
            Row(x, r1=1.0, r3=2.2, r7=4.0 - k / 2, vol_ratio=1.5, vol_z=1.5, news=3, news_imp=4)
            for k, x in enumerate(chosen)
        ]
        others = [
            x
            for x in ("QQQ-USDT-SWAP", "AVAX-USDT-SWAP", "CL-USDT-SWAP", "META-USDT-SWAP")
            if x not in chosen
        ]
        rows += [Row(x, r7=0.7) for x in others]
        picks = [
            Pick(
                x,
                "long",
                [0.74, 0.61][k],
                f"{rows[k].news} stories in 24h and volume z {rows[k].vol_z}.",
                catalysts=shared,
            )
            for k, x in enumerate(chosen)
        ]
        rejected = [(x, f"{x}: no news and volume z {0.3}.") for x in others[:3]]
        case = Case(
            f"timeout_{n:02d}_{missing}",
            f"{missing} did not reply in time: partial evidence, conviction capped at 0.5.",
            day,
            rows,
            picks,
            rejected,
            props(
                verdict_in={"field": "status", "values": ["partial_evidence"]},
                max_conviction={"max": 0.5},
            ),
            shared=shared,
            seed=500 + n,
        )
        replies = default_replies(case)
        for agent in ("historian", "news_intelligence") if missing == "both" else (missing,):
            replies.pop(agent)
        case.replies = replies
        out.append(case)
    return out


def owner_cases() -> list[Case]:
    out: list[Case] = []
    specs = [
        # (pinned, removed, blocklist, model picks, model leaves the pin out)
        ("XAG-USDT-SWAP", "DOGE-USDT-SWAP", [], ["BTC-USDT-SWAP", "TSLA-USDT-SWAP"]),
        ("META-USDT-SWAP", None, ["SOL-USDT-SWAP"], ["SOL-USDT-SWAP", "XAU-USDT-SWAP", "ETH-USDT-SWAP"]),
        ("CL-USDT-SWAP", "NVDA-USDT-SWAP", ["XRP-USDT-SWAP"], ["CL-USDT-SWAP", "ETH-USDT-SWAP"]),
        (None, None, ["XAU-USDT-SWAP", "TSLA-USDT-SWAP"], ["SPY-USDT-SWAP", "BTC-USDT-SWAP"]),
    ]
    for n, (pin, removed, block, chosen) in enumerate(specs, start=1):
        day = date(2026, 10, 1 + n)
        names = list(dict.fromkeys([*chosen, *(x for x in (pin, removed) if x), *block]))
        fill = [
            x
            for x in ("LINK-USDT-SWAP", "QQQ-USDT-SWAP", "AVAX-USDT-SWAP", "XAG-USDT-SWAP", "META-USDT-SWAP")
            if x not in names
        ]
        rows = [
            Row(x, r1=1.0, r3=2.0, r7=3.8 - k / 5, vol_ratio=1.4, vol_z=1.3, news=2, news_imp=4)
            for k, x in enumerate(names)
        ]
        rows += [Row(x, r7=0.6) for x in fill[:3]]
        by = {r.inst_id: r for r in rows}
        picks = [
            Pick(x, "long", [0.7, 0.62, 0.56][k], f"{by[x].news} stories in 24h and volume z {by[x].vol_z}.")
            for k, x in enumerate(chosen)
        ]
        rejected = [(x, f"{x}: no news and volume z {0.3}.") for x in fill[:3]]
        banned = [*([removed] if removed else []), *block]
        properties = props(not_picked={"inst_ids": banned})
        if pin:
            properties["picked"] = {"inst_ids": [pin]}
        out.append(
            Case(
                f"owner_{n:02d}_{'pin' if pin else 'block'}",
                "Re-run after the owner's edits: pins stay, removed and blocklisted instruments are dropped.",
                day,
                rows,
                picks,
                rejected,
                properties,
                hour_utc=6,
                blocklist=block,
                pinned=[pin] if pin else [],
                removed=[removed] if removed else [],
                seed=600 + n,
            )
        )
    return out


def single_cases() -> list[Case]:
    out: list[Case] = []
    specs = [
        ("AVAX-USDT-SWAP", ["XAU-USDT-SWAP", "ETH-USDT-SWAP"]),
        ("META-USDT-SWAP", ["BTC-USDT-SWAP", "XAG-USDT-SWAP"]),
        ("LINK-USDT-SWAP", ["TSLA-USDT-SWAP", "SPY-USDT-SWAP"]),
    ]
    for n, (lonely, good) in enumerate(specs, start=1):
        day = date(2026, 9, 14 + n)
        rows = [
            # Top momentum, nothing else: no news, no catalyst, no derivatives data, no regime.
            Row(
                lonely,
                r1=2.1,
                r3=4.8,
                r7=7.9,
                r30=12.5,
                vol_ratio=1.0,
                vol_z=0.1,
                funding=None,
                funding_z=None,
                oi=None,
                ls=None,
                regime=None,
                regime_p=None,
            ),
            *(Row(x, r1=0.9, r3=1.8, r7=3.1, vol_ratio=1.4, vol_z=1.3, news=3, news_imp=4) for x in good),
        ]
        fill = [
            x for x in ("QQQ-USDT-SWAP", "CL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP") if x not in good
        ]
        rows += [Row(x, r7=0.5) for x in fill[:3]]
        picks = [
            Pick(
                x,
                "long",
                [0.64, 0.57][k],
                f"{rows[k + 1].news} stories in 24h plus volume z {rows[k + 1].vol_z}.",
            )
            for k, x in enumerate(good)
        ]
        rejected = [
            (
                lonely,
                f"{lonely}: only price momentum (7d return {7.9}%); "
                "no news, catalyst, positioning or regime evidence.",
            ),
            (fill[0], f"{fill[0]}: no catalyst and volume z {0.3}."),
            (fill[1], f"{fill[1]}: no catalyst and volume z {0.3}."),
        ]
        out.append(
            Case(
                f"single_{n:02d}_{lonely.split('-')[0].lower()}",
                f"{lonely} tops momentum with no second evidence type; the quality bar rejects it.",
                day,
                rows,
                picks,
                rejected,
                props(not_picked={"inst_ids": [lonely]}),
                seed=700 + n,
            )
        )
    return out


def main() -> None:
    cases = [
        *mixed_cases(),
        *notrade_cases(),
        *chasing_cases(),
        *correlated_cases(),
        *timeout_cases(),
        *owner_cases(),
        *single_cases(),
    ]
    for old in HERE.iterdir():
        if old.is_dir() and (old / "case.yaml").is_file():
            shutil.rmtree(old)
    for case in cases:
        build(case)
    print(f"{len(cases)} cases written to {HERE}")


if __name__ == "__main__":
    main()
