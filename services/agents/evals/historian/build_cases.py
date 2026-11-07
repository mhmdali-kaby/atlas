# ruff: noqa: E501  (case questions and answer templates are long string literals)
"""Builds the ATL-94 Historian eval cases (brief §7.3, §7.11): the shared snapshot pool and the case
directories.

The local dev DB holds no candle history and this machine has no route to OKX, so the snapshots are
seeded, real-shaped series (OKX column layout, tick-rounded prices, 8-hourly funding, ForexFactory
style CPI/FOMC rows, Finnhub style earnings rows). Every case's ``data/<table>.json`` is a symlink
into ``_pool/`` and each case picks its replay day with ``replay_as_of``: the tools bound every
query by that cutoff, so one pool serves every day without look-ahead.

The scripted (fake) answers are not hand-typed: this script runs each case's tool steps through the
real replay-bounded tools on the checkout's test DB, then writes the answer text and the
HistoryVerdict card from those tool outputs, so every number in a fake answer is one a tool
returned. Re-run after changing a tool or the pool:

    cd services && uv run python agents/evals/historian/build_cases.py

Needs a migrated test DB (``cd backend && php artisan test``).
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import yaml  # type: ignore[import-untyped]
from atlas_agents import historian
from atlas_agents.evals import runner
from atlas_agents.evals.case import load_case
from atlas_shared import testing
from atlas_shared.db import make_engine
from sqlalchemy import Engine

HERE = Path(__file__).resolve().parent
POOL = HERE / "_pool"
ENV = "demo"

START = datetime(2026, 4, 3, tzinfo=UTC)  # first 4H bar
FINE = datetime(2026, 10, 12, tzinfo=UTC)  # 1H bars from here on (4H aggregated from them)
END = datetime(2026, 10, 20, tzinfo=UTC)  # exclusive: the last bar closes here
H4 = timedelta(hours=4)
H1 = timedelta(hours=1)

PRICES_POOL = "candles"
TABLES = ("candles", "funding_rates", "instruments", "economic_events", "earnings_events")


# -- the pool ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    inst_id: str
    asset_class: str
    price: float  # first open
    sigma4h: float  # 4H log-return sigma
    tick: int  # price decimals
    vol: float  # typical 4H volume in contracts
    ct_val: float
    category: str
    segments: tuple[tuple[int, float, float], ...] = ()  # (bars, drift per bar, vol multiplier)
    start: datetime = START
    end: datetime = END
    fine: bool = True  # has 1H bars for the last 8 days (and so a 24h volume)
    holes: tuple[tuple[int, int], ...] = ()  # interior bar ranges left out (unrepaired gaps)


CORE = (
    Spec(
        "BTC-USDT-SWAP",
        "crypto",
        81_250.0,
        0.0105,
        1,
        9_000,
        0.01,
        "1",
        ((300, 0.0006, 1.0), (250, -0.0002, 0.8), (220, 0.0, 1.6), (380, 0.0005, 0.9)),
    ),
    Spec(
        "ETH-USDT-SWAP",
        "crypto",
        2_385.0,
        0.0135,
        2,
        60_000,
        0.1,
        "1",
        ((260, 0.0003, 1.1), (300, -0.0006, 1.3), (300, 0.0004, 0.8), (290, 0.0007, 1.0)),
    ),
    Spec(
        "SOL-USDT-SWAP",
        "crypto",
        131.4,
        0.018,
        3,
        300_000,
        1.0,
        "1",
        ((350, 0.0009, 1.0), (300, -0.0008, 1.4), (500, 0.0002, 0.9)),
    ),
    Spec(
        "XRP-USDT-SWAP",
        "crypto",
        2.213,
        0.017,
        4,
        2_000_000,
        100.0,
        "1",
        ((400, -0.0003, 0.9), (400, 0.0, 0.7), (350, 0.0006, 1.5)),
    ),
    Spec(
        "XAU-USDT-SWAP",
        "commodity",
        3_312.4,
        0.0042,
        2,
        40_000,
        0.001,
        "4",
        ((500, 0.0004, 1.0), (300, 0.0, 0.7), (350, 0.0003, 1.3)),
    ),
    Spec(
        "TSLA-USDT-SWAP",
        "stock",
        262.35,
        0.016,
        2,
        120_000,
        0.01,
        "3",
        ((300, 0.0005, 1.0), (450, -0.0004, 1.2), (400, 0.0006, 0.9)),
    ),
    Spec(
        "NVDA-USDT-SWAP",
        "stock",
        118.62,
        0.014,
        2,
        150_000,
        0.01,
        "3",
        ((600, 0.0008, 1.0), (300, -0.0002, 1.4), (250, 0.0003, 0.8)),
    ),
)
# Short-history and gapped series: no 1H bars, so they never make the regime table.
EXTRA = (
    Spec(
        "HYPE-USDT-SWAP",
        "crypto",
        38.214,
        0.024,
        3,
        400_000,
        1.0,
        "1",
        ((150, 0.0012, 1.2),),
        start=datetime(2026, 9, 25, tzinfo=UTC),
        fine=False,
    ),
    Spec(
        "DOGE-USDT-SWAP",
        "crypto",
        0.21843,
        0.019,
        5,
        9_000_000,
        1000.0,
        "1",
        ((700, 0.0002, 1.0),),
        start=datetime(2026, 6, 23, tzinfo=UTC),
        fine=False,
        holes=((310, 328),),
    ),
    Spec(
        "AVAX-USDT-SWAP",
        "crypto",
        21.374,
        0.02,
        3,
        500_000,
        1.0,
        "1",
        ((700, -0.0001, 1.1),),
        start=datetime(2026, 6, 23, tzinfo=UTC),
        fine=False,
        holes=((240, 251), (505, 512)),
    ),
    Spec(
        "LINK-USDT-SWAP",
        "crypto",
        14.826,
        0.019,
        3,
        600_000,
        1.0,
        "1",
        ((700, 0.0003, 0.9),),
        start=datetime(2026, 6, 23, tzinfo=UTC),
        fine=False,
        holes=((420, 445),),
    ),
)


def _fmt(x: float, dp: int) -> str:
    return f"{x:.{dp}f}"


def _bar_row(
    spec: Spec, bar: str, ts: datetime, o: float, h: float, low: float, c: float, v: float
) -> dict[str, Any]:
    vol = round(v)
    return {
        "env": ENV,
        "inst_id": spec.inst_id,
        "bar": bar,
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "o": _fmt(o, spec.tick),
        "h": _fmt(h, spec.tick),
        "l": _fmt(low, spec.tick),
        "c": _fmt(c, spec.tick),
        "vol": str(vol),
        "vol_ccy_quote": _fmt(vol * spec.ct_val * c, 2),
        "confirmed": True,
    }


def _path(
    rng: np.random.Generator, spec: Spec, n: int, sigma: float, start_price: float, scale: float = 1.0
) -> list[tuple[float, float, float, float, float]]:
    """(o, h, l, c, volume) bars of a regime-switching log-normal walk."""
    drifts, mults = [], []
    for bars, drift, mult in spec.segments:
        drifts += [drift * scale] * bars
        mults += [mult] * bars
    while len(drifts) < n:
        drifts.append(drifts[-1] if drifts else 0.0)
        mults.append(mults[-1] if mults else 1.0)
    out = []
    price = start_price
    for i in range(n):
        s = sigma * mults[i]
        r = drifts[i] + s * rng.standard_t(5) / math.sqrt(5 / 3)
        o, c = price, price * math.exp(r)
        h = max(o, c) * math.exp(abs(rng.normal(0, s * 0.45)))
        low = min(o, c) * math.exp(-abs(rng.normal(0, s * 0.45)))
        v = spec.vol * scale * math.exp(rng.normal(0, 0.3)) * (1 + 1.5 * abs(r) / max(s, 1e-9)) * mults[i]
        out.append((o, h, low, c, v))
        price = c
    return out


def _candles(spec: Spec, seed: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    coarse_end = FINE if spec.fine else spec.end
    n4 = int((coarse_end - spec.start) / H4)
    bars4 = _path(rng, spec, n4, spec.sigma4h, spec.price)
    four: list[tuple[datetime, tuple[float, float, float, float, float]]] = [
        (spec.start + H4 * i, b) for i, b in enumerate(bars4)
    ]
    if spec.fine:
        n1 = int((spec.end - FINE) / H1)
        fine = _path(rng, spec, n1, spec.sigma4h / 2, bars4[-1][3], scale=0.25)
        for i, b in enumerate(fine):
            rows.append(_bar_row(spec, "1H", FINE + H1 * i, *b))
        for j in range(0, n1, 4):
            chunk = fine[j : j + 4]
            four.append(
                (
                    FINE + H1 * j,
                    (
                        chunk[0][0],
                        max(x[1] for x in chunk),
                        min(x[2] for x in chunk),
                        chunk[-1][3],
                        sum(x[4] for x in chunk),
                    ),
                )
            )
    holes = {i for lo, hi in spec.holes for i in range(lo, hi)}
    for i, (ts, b) in enumerate(four):
        if i not in holes:
            rows.append(_bar_row(spec, "4H", ts, *b))
    if spec.fine:  # 1D bars aggregated from the 4H ones (core series have no holes)
        for j in range(0, len(four), 6):
            chunk = [b for _, b in four[j : j + 6]]
            if len(chunk) < 6:
                break
            rows.append(
                _bar_row(
                    spec,
                    "1D",
                    four[j][0],
                    chunk[0][0],
                    max(x[1] for x in chunk),
                    min(x[2] for x in chunk),
                    chunk[-1][3],
                    sum(x[4] for x in chunk),
                )
            )
    return rows


def _funding(spec: Spec, seed: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed + 1000)
    rows, rate = [], 0.0001
    ts = spec.start
    while ts < spec.end:
        rate = 0.6 * rate + 0.4 * 0.0001 + rng.normal(0, 0.00006)
        rate = min(max(rate, -0.00075), 0.00075)
        r = f"{rate:.6f}"
        rows.append(
            {
                "env": ENV,
                "inst_id": spec.inst_id,
                "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "funding_rate": r,
                "realized_rate": r,
            }
        )
        ts += timedelta(hours=8)
    return rows


def _uid(*parts: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, "atlas-evals/" + "/".join(parts)).hex


def _instruments() -> list[dict[str, Any]]:
    out = []
    for spec in CORE + EXTRA:
        base = spec.inst_id.split("-")[0]
        out.append(
            {
                "id": _uid("inst", spec.inst_id),
                "env": ENV,
                "inst_id": spec.inst_id,
                "inst_type": "SWAP",
                "inst_family": f"{base}-USDT",
                "inst_category": spec.category,
                "base_ccy": base,
                "quote_ccy": "USDT",
                "settle_ccy": "USDT",
                "ct_val": str(spec.ct_val),
                "ct_val_ccy": base,
                "ct_mult": "1",
                "lot_sz": "0.01",
                "min_sz": "0.01",
                "tick_sz": f"{10**-spec.tick:.{spec.tick}f}",
                "max_lever": "50" if spec.asset_class == "crypto" else "20",
                "state": "live",
                "list_time": (spec.start - timedelta(days=400 if spec.fine else 0)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "exp_time": None,
                "asset_class": spec.asset_class,
            }
        )
    return out


# ForexFactory-style rows: CPI m/m and y/y share a time (the tool dedupes timestamps).
CPI_DAYS = (
    "2026-03-11",
    "2026-04-10",
    "2026-05-12",
    "2026-06-10",
    "2026-07-14",
    "2026-08-12",
    "2026-09-11",
    "2026-10-14",
    "2026-11-12",
)
FOMC_DAYS = ("2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28")


def _economic() -> list[dict[str, Any]]:
    rows = []
    for d in CPI_DAYS:
        for name in ("CPI m/m", "CPI y/y"):
            rows.append(
                {
                    "id": _uid("ev", d, name),
                    "source": "forexfactory",
                    "external_id": f"ff-{d}-{name}",
                    "country": "USD",
                    "name": name,
                    "importance": 5,
                    "scheduled_at": f"{d}T12:30:00Z",
                    "actual": None,
                    "forecast": "0.3%" if "m/m" in name else "2.9%",
                    "previous": "0.2%" if "m/m" in name else "2.8%",
                }
            )
    for d in FOMC_DAYS:
        rows.append(
            {
                "id": _uid("ev", d, "fomc"),
                "source": "forexfactory",
                "external_id": f"ff-{d}-ffr",
                "country": "USD",
                "name": "Federal Funds Rate",
                "importance": 5,
                "scheduled_at": f"{d}T18:00:00Z",
                "actual": None,
                "forecast": "4.00%",
                "previous": "4.00%",
            }
        )
    # A non-US CPI must never be counted as a US CPI event.
    rows.append(
        {
            "id": _uid("ev", "eu-cpi"),
            "source": "forexfactory",
            "external_id": "ff-2026-09-17-eur-cpi",
            "country": "EUR",
            "name": "CPI y/y",
            "importance": 4,
            "scheduled_at": "2026-09-17T09:00:00Z",
            "actual": None,
            "forecast": "2.1%",
            "previous": "2.0%",
        }
    )
    return rows


def _earnings() -> list[dict[str, Any]]:
    rows = []
    for sym, days in (
        ("TSLA", ("2026-04-22", "2026-07-22", "2026-10-21")),
        ("NVDA", ("2026-05-27", "2026-08-26")),
    ):
        for d in days:
            rows.append(
                {
                    "id": _uid("earn", sym, d),
                    "symbol": sym,
                    "date": d,
                    "time": "amc",
                    "eps_est": "0.52",
                    "eps_act": None,
                    "rev_est": "25100000000",
                    "rev_act": None,
                }
            )
    return rows


def build_pool() -> None:
    if POOL.exists():
        shutil.rmtree(POOL)
    POOL.mkdir()
    candles: list[dict[str, Any]] = []
    funding: list[dict[str, Any]] = []
    for seed, spec in enumerate(CORE + EXTRA, start=94):
        candles += _candles(spec, seed)
        if spec.asset_class == "crypto":
            funding += _funding(spec, seed)
    tables = {
        "candles": candles,
        "funding_rates": funding,
        "instruments": _instruments(),
        "economic_events": _economic(),
        "earnings_events": _earnings(),
    }
    for name, rows in tables.items():
        # One row per line keeps diffs readable and the files compact.
        body = "[\n" + ",\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n]\n"
        (POOL / f"{name}.json").write_text(body, encoding="utf-8")


# -- cases ------------------------------------------------------------------------------------------


Outputs = list[dict[str, Any]]  # each tool step's parsed {as_of, source, data} envelope, in order
Composer = Callable[["Case", Outputs], tuple[str, dict[str, Any]]]


@dataclass
class Case:
    case_id: str
    category: str
    replay_as_of: datetime
    question: str
    steps: list[tuple[str, dict[str, Any]]]
    compose: Composer
    verdicts: list[str] | None  # verdict_in values; None for the regime table (it has no verdict)
    mentions: dict[str, int] = field(default_factory=dict)
    model: str = "HistoryVerdict"
    horizon: str = "24h"  # analogs: the horizon the verdict is read from
    stop_mult: float = 0.0  # stop-noise: stop distance as a multiple of ATR(1H)
    dp: int = 2  # stop-noise: price decimals
    stop_distance: float = 0.0  # stop-noise: filled in from the ATR before the prompt is written
    table: dict[str, Any] = field(default_factory=dict)  # regime: the computed RegimeTable JSON
    table_model: Any = None
    inst_id: str = ""


def mention_prompt(case: Case) -> str:
    req = historian.MentionRequest(
        event_id=_uid("mention", case.case_id),
        env=ENV,
        author="owner",
        author_type="human",
        content_md=case.question,
        correlation_id=None,
        ts=case.replay_as_of,
    )
    return historian.mention_prompt(req)


def _iso(ts: str | None) -> str:
    return (ts or "").replace("+00:00", "Z")


def _day(ts: str | None) -> str:
    return (ts or "")[:10]


def _period(sample: dict[str, Any]) -> str | None:
    p = sample.get("period") or {}
    if not p.get("start") or not p.get("end"):
        return None
    return f"{_day(p['start'])}..{_day(p['end'])}"


def _pct(x: float, dp: int = 2) -> str:
    return f"{x * 100:+.{dp}f}%"


def _quality_notes(sample: dict[str, Any]) -> list[str]:
    notes = []
    for q in sample.get("data_quality") or []:
        interior = [g for g in q.get("gaps") or [] if g.get("position") == "interior"]
        if interior:
            spans = ", ".join(f"{_iso(g['from_ts'])} to {_iso(g['to_ts'])}" for g in interior)
            notes.append(f"{q['inst_id']} {q['bar']}: unrepaired interior gap(s) {spans}")
    if not notes:
        notes.append("no interior gaps in the window")
    return notes


def _cap(verdict: str, n: int) -> str:
    """The Historian's own small-sample rule: n < 20 is never stronger than weakly_supports, in
    either direction."""
    if n < 20 and verdict in ("supports", "contradicts"):
        return "weakly_supports" if verdict == "supports" else "does_not_support"
    return verdict


def _card(
    case: Case, sample: dict[str, Any], as_of: str, verdict: str, key_stat: str, **extra: Any
) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "history_verdict",
        "question": case.question,
        "inst_id": sample.get("inst_id"),
        "bar": sample.get("bar"),
        "period": _period(sample),
        "n": int(sample.get("n") or 0),
        "distribution": {},
        "base_rate": {},
        "regime": None,
        "stop_noise_check": None,
        "overfit_warning": False,
        "verdict": verdict,
        "key_stat": key_stat,
        "data_quality_notes": _quality_notes(sample),
        "data_as_of": _iso(as_of),
    }
    card.update(extra)
    return card


def _fence(card: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(card, indent=1) + "\n```"


def _dists(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for d in rows:
        if d.get("n") and d.get("median") is not None:
            out[d["horizon"]] = {
                "median": d["median"],
                "p10": d["p10"],
                "p90": d["p90"],
                "pct_positive": d["pct_positive"],
                "n": d["n"],
            }
    return out


# analogs ------------------------------------------------------------------------------------------


def compose_analogs(case: Case, outs: Outputs) -> tuple[str, dict[str, Any]]:
    env = outs[-1]
    data = env["data"]
    sample, res = data["sample"], data.get("result")
    n = int(sample["n"])
    side = (
        "long"
        if "long" in case.question or "bounce" in case.question or "continue" in case.question
        else "short"
    )
    if not res or n == 0:
        verdict = "does_not_support"
        text = (
            f"No analogs: {data.get('note') or 'no eligible windows'}. Sample: {sample['inst_id']} "
            f"{sample['bar']}, n = 0 analog windows (as_of {_iso(env['as_of'])}). History does not "
            "support the idea; there is nothing to compare with."
        )
        card = _card(case, sample, env["as_of"], verdict, "no analog windows (n = 0)")
        return text, card
    dist = {d["horizon"]: d for d in res["distribution"]}
    base = {d["horizon"]: d for d in res["base_rate"]}
    h = case.horizon if dist.get(case.horizon, {}).get("n") else next(k for k, v in dist.items() if v["n"])
    d, b = dist[h], base[h]
    edge = d["pct_positive"] - b["pct_positive"]
    if side == "short":
        edge = -edge
    better = (d["median"] - b["median"]) * (1 if side == "long" else -1)
    if edge >= 8 and better > 0:
        verdict = "supports"
    elif edge >= 3 and better > 0:
        verdict = "weakly_supports"
    elif edge <= -7 and better < 0:
        verdict = "contradicts"
    else:
        verdict = "does_not_support"
    verdict = _cap(verdict, n)
    weak = " The sample is under 20 cases, so the evidence is weak." if n < 20 else ""
    words = {
        "supports": "supports",
        "weakly_supports": "weakly supports",
        "does_not_support": "does not support",
        "contradicts": "contradicts",
    }[verdict]
    key = (
        f"{h} median {_pct(d['median'])} vs base {_pct(b['median'])}; "
        f"{d['pct_positive']:.1f}% positive vs {b['pct_positive']:.1f}%"
    )
    text = (
        f"History {words} the {side} idea on {sample['inst_id']}.{weak} Sample: {sample['bar']} bars "
        f"{_iso(sample['period']['start'])} to {_iso(sample['period']['end'])}, n = {n} analog windows "
        f"out of {res['n_eligible']} eligible (as_of {_iso(env['as_of'])}). After the {n} nearest analogs the "
        f"{h} forward return had median {_pct(d['median'])} (p10 {_pct(d['p10'])}, p90 {_pct(d['p90'])}), "
        f"{d['pct_positive']:.1f}% positive, against an unconditional base rate of median {_pct(b['median'])} "
        f"and {b['pct_positive']:.1f}% positive. Key stat: {key}."
    )
    card = _card(
        case,
        sample,
        env["as_of"],
        verdict,
        key,
        distribution=_dists(res["distribution"]),
        base_rate=_dists(res["base_rate"]),
    )
    if len(outs) > 1 and outs[0]["data"].get("result"):
        r = outs[0]["data"]["result"]
        card["regime"] = {
            "label": r["label"],
            "probability": r["probability"],
            "days_in_regime": r["days_in_regime"],
        }
        text += f" Current regime: {r['label']} ({r['method']}) for {r['days_in_regime']:.2f} days."
    return text, card


def analog_case(
    cid: str,
    category: str,
    inst: str,
    as_of: datetime,
    question: str,
    *,
    bar: str = "4H",
    window: int = 12,
    horizons: list[str] | None = None,
    regime: bool = False,
    verdicts: list[str] | None = None,
    horizon: str = "24h",
) -> Case:
    steps: list[tuple[str, dict[str, Any]]] = []
    if regime:
        steps.append(("history.detect_regime", {"inst_id": inst, "bar": bar, "lookback": 600}))
    steps.append(
        (
            "history.find_analogs",
            {
                "inst_id": inst,
                "bar": bar,
                "window": window,
                "k": 20,
                "horizons": horizons or ["4h", "24h", "72h"],
                "lookback_years": 1,
            },
        )
    )
    case = Case(cid, category, as_of, question, steps, compose_analogs, verdicts or ["auto"], {"min": 0})
    case.horizon = horizon
    return case


# event studies ------------------------------------------------------------------------------------


def compose_event(case: Case, outs: Outputs) -> tuple[str, dict[str, Any]]:
    env = outs[0]
    if "data" not in env:  # a ToolError: the event type is not in the calendar
        msg = env.get("message") or env.get("error")
        ctx_env = outs[1]
        ctx = ctx_env["data"]
        st, cs = ctx["result"], ctx["sample"]
        text = (
            f"History cannot answer this: history.event_study has no such event type ({msg}). "
            "No past events were studied, so n = 0 cases and history does not support any claim "
            f"about the move. For scale only: {cs['inst_id']} {cs['bar']} ATR is {st['atr_pct']:.2f}% of "
            f"price over n = {cs['n']} bars to {_iso(ctx_env['as_of'])}, with realised volatility "
            f"{st['realised_vol']:.2f} annualised; that is ordinary noise, not an event study."
        )
        card: dict[str, Any] = {
            "type": "history_verdict",
            "question": case.question,
            "inst_id": case.steps[0][1].get("inst_id"),
            "bar": None,
            "period": None,
            "n": 0,
            "distribution": {},
            "base_rate": {},
            "regime": None,
            "stop_noise_check": None,
            "overfit_warning": False,
            "verdict": "does_not_support",
            "key_stat": "no event history for this event type (n = 0)",
            "data_quality_notes": ["event_study supports only calendar macro events and earnings"],
            "data_as_of": _iso(ctx_env["as_of"]),
        }
        return text, card
    data = env["data"]
    sample, res = data["sample"], data.get("result")
    n = int(sample["n"])
    if not res or n == 0:
        text = (
            f"No usable events: {data.get('note')}. n = 0 cases, so history does not support a "
            "view on the move."
        )
        return text, _card(case, sample, env["as_of"], "does_not_support", "no usable events (n = 0)")
    offs = {o["offset_hours"]: o for o in res["offsets"]}
    atr = {a["horizon_hours"]: a for a in res["atr_moves"]}
    after = max(k for k, o in offs.items() if o["n"] and o["median"] is not None and k <= 24)
    o = offs[after]
    a24 = atr.get(24.0) or next(iter(atr.values()))
    big = a24["mean_abs_move_atr"] is not None and a24["mean_abs_move_atr"] >= 1.0
    verdict = _cap("supports" if big else "weakly_supports", n)
    words = "supports" if verdict == "supports" else "weakly supports"
    weak = " With fewer than 20 cases the evidence is weak." if n < 20 else ""
    key = (
        f"mean absolute move {a24['mean_abs_move_atr']:.2f} ATR at {a24['horizon_hours']:.0f}h after "
        f"{data['event_type']} (n = {a24['n']})"
    )
    text = (
        f"History {words} treating {data['event_type'].upper()} as a volatility event for "
        f"{', '.join(res['inst_ids'])}.{weak} Sample: {res['n_events']} events, n = {n} cases on "
        f"{res['bar']} bars (as_of {_iso(env['as_of'])}); {res['dropped_out_of_range']} cases fell outside "
        f"the loaded history. Return {after:.0f}h after the event: median {_pct(o['median'])} (p10 "
        f"{_pct(o['p10'])}, p90 {_pct(o['p90'])}). Key stat: {key}. Direction is not reliable from "
        "this sample, only the size of the move."
    )
    card = _card(case, sample, env["as_of"], verdict, key)
    if "asset_class" in case.steps[0][1]:
        card["inst_id"] = None
    return text, card


def event_case(
    cid: str, category: str, as_of: datetime, question: str, args: dict[str, Any], verdicts: list[str]
) -> Case:
    full = {"window": [-24, 72], "bar": "4H", "lookback_years": 1, **args}
    steps: list[tuple[str, dict[str, Any]]] = [("history.event_study", full)]
    if full["event_type"] not in ("cpi", "fomc", "earnings"):  # the tool refuses it; give scale instead
        steps.append(("history.compute_stats", {"inst_id": args["inst_id"], "bar": "4H", "lookback": 120}))
    return Case(cid, category, as_of, question, steps, compose_event, verdicts, {"min": 0})


# backtests ----------------------------------------------------------------------------------------


def compose_backtest(case: Case, outs: Outputs) -> tuple[str, dict[str, Any]]:
    env = outs[-1]
    data = env["data"]
    sample, res = data["sample"], data.get("result")
    n = int(sample["n"])
    if not res:
        text = f"The backtest returned no result ({data.get('note')}); n = 0 trades, no claim."
        return text, _card(case, sample, env["as_of"], "does_not_support", "no backtest result (n = 0)")
    inst = res["instruments"][0]
    full = inst["full"]
    gap = bool(sample["unrepaired_gap"])
    overfit = bool(res["overfit_warning"])
    sharpe = full["sharpe"]
    if gap:
        verdict = "does_not_support"
    elif overfit:
        verdict = "does_not_support"
    elif sharpe is not None and sharpe >= 1.0 and (full["total_return"] or 0) > 0:
        verdict = "supports"
    elif sharpe is not None and sharpe > 0:
        verdict = "weakly_supports"
    elif sharpe is not None and sharpe <= -1.0:
        verdict = "contradicts"
    else:
        verdict = "does_not_support"
    verdict = _cap(verdict, n)
    words = {
        "supports": "supports",
        "weakly_supports": "weakly supports",
        "does_not_support": "does not support",
        "contradicts": "contradicts",
    }[verdict]

    def m(x: float | None, pct: bool = False) -> str:
        if x is None:
            return "n/a"
        return _pct(x) if pct else f"{x:.2f}"

    parts = [
        f"History {words} the rule {res['rule']['name']} on {inst['inst_id']}.",
        f"Sample: {inst['bar']} bars {_iso(res['period_start'])} to {_iso(res['period_end'])}, n = {n} trades "
        f"(as_of {_iso(env['as_of'])}), taker fee {res['taker_fee_rate']} and {res['slippage_bps']} bps slippage, "
        "funding included.",
        f"Full period: total return {m(full['total_return'], True)}, Sharpe {m(full['sharpe'])}, max drawdown "
        f"{m(full['max_drawdown'], True)}, win rate {m(full['win_rate'], True)}.",
        f"Walk-forward over {res['splits']} folds: mean in-sample Sharpe {m(inst['is_sharpe_mean'])} vs "
        f"out-of-sample {m(inst['oos_sharpe_mean'])}.",
    ]
    if n < 20:
        parts.append("Fewer than 20 trades: the evidence is weak.")
    if overfit:
        parts.append("Out-of-sample is much worse than in-sample, so the rule is probably overfit.")
    if gap:
        parts.append(
            "The series has unrepaired interior gaps, so no backtest-based claim can be made from it."
        )
    key = (
        f"OOS Sharpe {m(inst['oos_sharpe_mean'])} vs IS {m(inst['is_sharpe_mean'])} over {n} trades"
        if overfit or gap
        else f"Sharpe {m(full['sharpe'])} over {n} trades, total {m(full['total_return'], True)}"
    )
    card = _card(case, sample, env["as_of"], verdict, key, overfit_warning=overfit)
    return " ".join(parts), card


def backtest_case(
    cid: str,
    category: str,
    inst: str,
    as_of: datetime,
    question: str,
    rule: dict[str, Any],
    verdicts: list[str],
    *,
    splits: int = 3,
) -> Case:
    args = {"inst_ids": [inst], "rule_spec": rule, "bar": "4H", "splits": splits}
    return Case(
        cid,
        category,
        as_of,
        question,
        [("history.backtest_rule", args)],
        compose_backtest,
        verdicts,
        {"min": 0},
    )


def sma(name: str, fast: int, slow: int, side: str = "long", **extra: Any) -> dict[str, Any]:
    up, down = ("crosses_above", "crosses_below") if side == "long" else ("crosses_below", "crosses_above")
    return {
        "name": name,
        "side": side,
        "indicators": {"f": {"kind": "sma", "window": fast}, "s": {"kind": "sma", "window": slow}},
        "entry": [{"left": "f", "op": up, "right": "s"}],
        "exit": [{"left": "f", "op": down, "right": "s"}],
        **extra,
    }


# stop noise ---------------------------------------------------------------------------------------


def compose_stop(case: Case, outs: Outputs) -> tuple[str, dict[str, Any]]:
    s1, s4 = outs[0]["data"], outs[1]["data"]
    atr1, atr4 = s1["result"]["atr"], s4["result"]["atr"]
    dist = case.stop_distance
    stop_atr = dist / atr1
    range_atr = atr4 / atr1
    inside = stop_atr < 0.8 * range_atr
    verdict = "contradicts" if inside else "supports"
    sample = s4["sample"]
    key = f"stop {stop_atr:.2f} ATR(1H) vs typical 4h range {range_atr:.2f} ATR(1H)"
    text = (
        f"The stop is {'inside' if inside else 'outside'} normal 4h noise on {sample['inst_id']}. "
        f"ATR(1H) is {atr1:.{case.dp}f} (n = {s1['sample']['n']} 1H bars, as_of {_iso(outs[0]['as_of'])}) and "
        f"ATR(4H) is {atr4:.{case.dp}f} (n = {sample['n']} 4H bars), so the {dist} stop distance is "
        f"{stop_atr:.2f} ATR(1H) against a typical 4h range of {range_atr:.2f} ATR(1H); the warning line is "
        f"0.8 of that range. ATR(4H) stands in for the median 4h range. "
        + (
            "History contradicts this stop: normal noise will likely hit it."
            if inside
            else "History supports this stop distance: it sits beyond normal 4h noise."
        )
    )
    card = _card(
        case,
        sample,
        outs[1]["as_of"],
        verdict,
        key,
        stop_noise_check={
            "stop_distance_atr": stop_atr,
            "median_4h_range_atr": range_atr,
            "verdict": "inside_noise" if inside else "ok",
        },
    )
    card["data_quality_notes"] = card["data_quality_notes"] + ["median 4h range approximated by ATR(4H)"]
    return text, card


# regime table -------------------------------------------------------------------------------------


REGIME_SUFFIX = (
    "\n\nFor this replay, also end your answer with the table you were given as one fenced ```json "
    "block (the RegimeTable: type, rows, data_as_of), unchanged, and state how many instruments it "
    "covers as n = <count>."
)


def compose_regime(case: Case, outs: Outputs) -> tuple[str, dict[str, Any]]:
    table = case.table
    rows = table["rows"]
    labels = ", ".join(f"{r['inst_id']} {r['label']}" for r in rows)
    newest = min((r for r in rows if r["days_in_regime"] is not None), key=lambda r: r["days_in_regime"])
    shaky = [
        r
        for r in rows
        if r["method"] == "fallback" or (r["probability"] is not None and r["probability"] < 0.7)
    ]
    text = (
        f"Regime table, n = {len(rows)} instruments by 24h quote volume (as_of {_iso(table['data_as_of'])}): "
        f"{labels}. Newest change: {newest['inst_id']} turned {newest['label']} {newest['days_in_regime']} days ago"
        + (f" (probability {newest['probability']})" if newest["probability"] is not None else "")
        + ". "
        + (
            f"Low-confidence labels: {', '.join(r['inst_id'] for r in shaky)}. "
            if shaky
            else "No label is low-confidence. "
        )
        + "Labels come from 1D bars; treat a very recent change as unconfirmed."
    )
    return text, table


# -- the 30 ------------------------------------------------------------------------------------------


def T(day: str, hh: int = 0, mm: int = 0) -> datetime:
    y, mo, d = (int(x) for x in day.split("-"))
    return datetime(y, mo, d, hh, mm, tzinfo=UTC)


def cases() -> list[Case]:
    any_v = ["supports", "weakly_supports", "does_not_support", "contradicts"]
    small = ["weakly_supports", "does_not_support"]
    out = [
        # 8 analog questions (4H windows of 2 days, k = 20, a year of history at most).
        analog_case(
            "analog_btc_after_selloff",
            "analog",
            "BTC-USDT-SWAP",
            T("2026-10-20"),
            "@Historian BTC-USDT-SWAP: what usually happens after the last two days on 4H? Does history favour a long bounce over the next 72h?",
            regime=True,
            horizon="72h",
        ),
        analog_case(
            "analog_eth_continuation",
            "analog",
            "ETH-USDT-SWAP",
            T("2026-10-18", 8),
            "@Historian ETH-USDT-SWAP 4H: do the closest past analogs continue higher over the next 24h (long)?",
        ),
        analog_case(
            "analog_sol_breakdown_short",
            "analog",
            "SOL-USDT-SWAP",
            T("2026-08-10", 12),
            "@Historian SOL-USDT-SWAP is breaking down on 4H. Do analogs back a short for the next day?",
        ),
        analog_case(
            "analog_xrp_quiet_range",
            "analog",
            "XRP-USDT-SWAP",
            T("2026-09-05"),
            "@Historian XRP-USDT-SWAP has gone quiet on 4H. Do analogs favour a long from here?",
        ),
        analog_case(
            "analog_xau_trend_long",
            "analog",
            "XAU-USDT-SWAP",
            T("2026-10-16", 4),
            "@Historian XAU-USDT-SWAP 4H: after windows like the last two days, does gold continue up over 72h (long)?",
            regime=True,
            horizon="72h",
        ),
        analog_case(
            "analog_tsla_weekend_drift",
            "analog",
            "TSLA-USDT-SWAP",
            T("2026-09-27"),
            "@Historian TSLA-USDT-SWAP: what did the perp do after weekend windows like this one? Long bias for the next 72h?",
            horizon="72h",
        ),
        analog_case(
            "analog_nvda_vol_spike",
            "analog",
            "NVDA-USDT-SWAP",
            T("2026-09-02", 16),
            "@Historian NVDA-USDT-SWAP just had a volatility spike on 4H. Do analogs support a long bounce over 72h?",
            horizon="72h",
        ),
        analog_case(
            "analog_btc_august_lookahead",
            "analog",
            "BTC-USDT-SWAP",
            T("2026-08-20"),
            "@Historian BTC-USDT-SWAP on 20 Aug: do 4H analogs support a long for the next 72h?",
            horizon="72h",
        ),
        # 5 event studies.
        event_case(
            "event_cpi_crypto",
            "event",
            T("2026-10-20"),
            "@Historian how have crypto perps moved around US CPI releases this year?",
            {"asset_class": "crypto", "event_type": "cpi"},
            any_v,
        ),
        event_case(
            "event_fomc_crypto",
            "event",
            T("2026-10-20"),
            "@Historian what do crypto perps usually do around FOMC rate decisions?",
            {"asset_class": "crypto", "event_type": "fomc"},
            any_v,
        ),
        event_case(
            "event_tsla_earnings",
            "event",
            T("2026-10-20"),
            "@Historian how big is the TSLA-USDT-SWAP move around Tesla earnings?",
            {"inst_id": "TSLA-USDT-SWAP", "event_type": "earnings"},
            small,
        ),
        event_case(
            "event_sol_token_unlock",
            "event",
            T("2026-10-20"),
            "@Historian what does SOL-USDT-SWAP do around token unlocks?",
            {"inst_id": "SOL-USDT-SWAP", "event_type": "token_unlock"},
            ["does_not_support"],
        ),
        event_case(
            "event_hype_listing",
            "event",
            T("2026-10-20"),
            "@Historian how do new perps like HYPE-USDT-SWAP trade in the days after listing?",
            {"inst_id": "HYPE-USDT-SWAP", "event_type": "listing"},
            ["does_not_support"],
        ),
        # 5 backtests, the last two deliberately overfit (tight, tuned parameters).
        backtest_case(
            "backtest_btc_sma_20_50",
            "backtest",
            "BTC-USDT-SWAP",
            T("2026-10-20"),
            "@Historian backtest a 4H SMA 20/50 crossover long on BTC-USDT-SWAP with real costs.",
            sma("sma_20_50", 20, 50),
            any_v,
        ),
        backtest_case(
            "backtest_eth_ema_12_26",
            "backtest",
            "ETH-USDT-SWAP",
            T("2026-10-20"),
            "@Historian does an EMA 12/26 crossover long on ETH-USDT-SWAP 4H make money after fees?",
            {
                **sma("ema_12_26", 12, 26),
                "indicators": {"f": {"kind": "ema", "window": 12}, "s": {"kind": "ema", "window": 26}},
            },
            any_v,
        ),
        backtest_case(
            "backtest_xau_rsi_dip",
            "backtest",
            "XAU-USDT-SWAP",
            T("2026-10-20"),
            "@Historian backtest buying XAU-USDT-SWAP 4H when RSI(14) drops below 35, exiting above 55.",
            {
                "name": "rsi_dip_35_55",
                "side": "long",
                "indicators": {"r": {"kind": "rsi", "window": 14}},
                "entry": [{"left": "r", "op": "crosses_below", "right": 35}],
                "exit": [{"left": "r", "op": "crosses_above", "right": 55}],
            },
            any_v,
        ),
        backtest_case(
            "backtest_overfit_sol_tuned",
            "backtest",
            "SOL-USDT-SWAP",
            T("2026-10-20"),
            "@Historian I tuned this SOL-USDT-SWAP 4H rule until it looked great: SMA 3/7 long with a 1.7% stop. Is it real?",
            sma("sma_3_7_stop", 3, 7, stop_pct=1.7),
            ["does_not_support", "contradicts"],
        ),
        backtest_case(
            "backtest_overfit_btc_short_tuned",
            "backtest",
            "BTC-USDT-SWAP",
            T("2026-10-20"),
            "@Historian my best BTC-USDT-SWAP rule after many tries: short on SMA 9/23 with a 1.7% stop. Backtest it honestly.",
            sma("sma_9_23_short_stop", 9, 23, side="short", stop_pct=1.7),
            ["does_not_support", "contradicts"],
        ),
        # 4 small-sample cases: n < 20 is never stronger than weakly_supports.
        analog_case(
            "small_hype_new_listing_analogs",
            "small",
            "HYPE-USDT-SWAP",
            T("2026-10-20"),
            "@Historian HYPE-USDT-SWAP only listed in late September. Do its 4H analogs support a long?",
            verdicts=small,
        ),
        event_case(
            "small_xau_fomc",
            "small",
            T("2026-10-20"),
            "@Historian how does XAU-USDT-SWAP react to FOMC decisions? Do we have enough history?",
            {"inst_id": "XAU-USDT-SWAP", "event_type": "fomc"},
            small,
        ),
        backtest_case(
            "small_xau_rare_rsi",
            "small",
            "XAU-USDT-SWAP",
            T("2026-10-20"),
            "@Historian backtest buying XAU-USDT-SWAP 4H only on RSI(14) below 25, exit above 50.",
            {
                "name": "rsi_extreme_25_50",
                "side": "long",
                "indicators": {"r": {"kind": "rsi", "window": 14}},
                "entry": [{"left": "r", "op": "crosses_below", "right": 25}],
                "exit": [{"left": "r", "op": "crosses_above", "right": 50}],
            },
            small,
        ),
        analog_case(
            "small_btc_1h_analogs",
            "small",
            "BTC-USDT-SWAP",
            T("2026-10-20"),
            "@Historian on 1H, what followed the last day of BTC-USDT-SWAP in the past? Long or not?",
            bar="1H",
            window=24,
            horizons=["1h", "4h", "24h"],
            verdicts=small,
        ),
        # 3 gapped-data backtests: a backtest claim on an unrepaired gap is does_not_support.
        backtest_case(
            "gap_doge_sma_backtest",
            "gap",
            "DOGE-USDT-SWAP",
            T("2026-10-20"),
            "@Historian backtest a 4H SMA 10/30 long on DOGE-USDT-SWAP.",
            sma("sma_10_30", 10, 30),
            ["does_not_support"],
        ),
        backtest_case(
            "gap_avax_ema_backtest",
            "gap",
            "AVAX-USDT-SWAP",
            T("2026-10-20"),
            "@Historian is an EMA 8/21 crossover long on AVAX-USDT-SWAP 4H profitable?",
            {
                **sma("ema_8_21", 8, 21),
                "indicators": {"f": {"kind": "ema", "window": 8}, "s": {"kind": "ema", "window": 21}},
            },
            ["does_not_support"],
        ),
        backtest_case(
            "gap_link_rsi_backtest",
            "gap",
            "LINK-USDT-SWAP",
            T("2026-10-20"),
            "@Historian backtest buying LINK-USDT-SWAP 4H on RSI(14) under 30, exit over 60.",
            {
                "name": "rsi_30_60",
                "side": "long",
                "indicators": {"r": {"kind": "rsi", "window": 14}},
                "entry": [{"left": "r", "op": "crosses_below", "right": 30}],
                "exit": [{"left": "r", "op": "crosses_above", "right": 60}],
            },
            ["does_not_support"],
        ),
    ]
    # 3 stop-noise checks; the stop distance (in price) is filled in from the ATRs (see build()).
    for cid, inst, mult, dp in (
        ("stopnoise_btc_tight", "BTC-USDT-SWAP", 0.6, 1),
        ("stopnoise_eth_wide", "ETH-USDT-SWAP", 2.6, 2),
        ("stopnoise_xau_borderline", "XAU-USDT-SWAP", 1.4, 2),
    ):
        c = Case(
            cid,
            "stopnoise",
            T("2026-10-19", 16),
            "",
            [
                ("history.compute_stats", {"inst_id": inst, "bar": "1H", "lookback": 168}),
                ("history.compute_stats", {"inst_id": inst, "bar": "4H", "lookback": 180}),
            ],
            compose_stop,
            None,
            {"min": 20},
        )
        c.stop_mult, c.dp, c.inst_id = mult, dp, inst
        out.append(c)
    # 2 regime-table mornings (07:30 Baghdad = 04:30 UTC); the prompt is the production one.
    for cid, day in (("regime_table_oct20", "2026-10-20"), ("regime_table_oct13", "2026-10-13")):
        out.append(
            Case(cid, "regime", T(day, 4, 30), "", [], compose_regime, None, {"min": 5}, model="RegimeTable")
        )
    return out


# -- build ------------------------------------------------------------------------------------------


def _write(case: Case, prompt: str, steps: list[dict[str, Any]], props: dict[str, Any]) -> Path:
    path = HERE / case.case_id
    data = path / "data"
    data.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        link = data / f"{table}.json"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(Path("..") / ".." / "_pool" / f"{table}.json")
    doc = {
        "agent": "historian",
        "replay_as_of": case.replay_as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "env": ENV,
        "description": f"[{case.category}] {case.question or case.case_id}",
        "prompt": prompt,
        "properties": props,
        "fake": {"steps": steps},
    }
    header = (
        f"# ATL-94 category: {case.category}. Generated by build_cases.py from the shared _pool "
        "snapshot; the fake answer is composed from the real tool outputs.\n"
    )
    (path / "case.yaml").write_text(
        header + yaml.safe_dump(doc, sort_keys=False, width=110, allow_unicode=True), encoding="utf-8"
    )
    return path


# The verdicts a case accepts when its verdict is read from data rather than fixed by a rule: the
# computed one and one step weaker (a careful real answer may hedge, never flip sides).
NEAR: dict[str, list[str]] = {
    "supports": ["supports", "weakly_supports"],
    "weakly_supports": ["weakly_supports", "does_not_support"],
    "does_not_support": ["does_not_support", "weakly_supports"],
    "contradicts": ["contradicts", "does_not_support"],
}


def _props(case: Case) -> dict[str, Any]:
    guard: dict[str, Any] = {"from_prompt": True} if case.category in ("stopnoise", "regime") else {}
    props: dict[str, Any] = {"valid_schema": {"model": case.model}, "no_invented_numbers": guard}
    if case.verdicts is not None:
        props["verdict_in"] = {"values": case.verdicts}
    props["no_trade"] = {}
    props["mentions_n"] = dict(case.mentions)
    return props


async def _tool_outputs(case: Case, prompt: str, engine: Engine) -> Outputs:
    steps = [{"tool": t, "input": a} for t, a in case.steps] + [{"text": "probe"}]
    path = _write(case, prompt, steps, {"no_trade": {}})
    result = await runner.run_case(load_case(path), engine, fake=True)
    if result.status != "ok":
        raise RuntimeError(f"{case.case_id}: probe run {result.status}: {result.error}")
    outs = result.tool_outputs
    if len(outs) != len(case.steps):
        raise RuntimeError(f"{case.case_id}: {len(outs)} outputs for {len(case.steps)} steps")
    for o in outs:
        if isinstance(o.get("data"), str):
            o["data"] = json.loads(o["data"])
    return outs


async def _regime_table(case: Case, engine: Engine) -> dict[str, Any]:
    """The table the 07:30 job would compute on this replay day (historian.build_regime_table)."""
    from atlas_agents.evals.case import EvalCase
    from atlas_agents.evals.snapshot import load_snapshot
    from atlas_agents.tools import ToolContext
    from sqlalchemy.orm import sessionmaker

    probe = EvalCase(agent="historian", replay_as_of=case.replay_as_of, prompt="x", properties={})
    probe.path = HERE / case.case_id
    conn = engine.connect()
    tx = conn.begin()
    try:
        load_snapshot(conn, probe.snapshot(), replay_as_of=case.replay_as_of)
        ctx = ToolContext(
            env=ENV,
            session_factory=sessionmaker(bind=conn, join_transaction_mode="create_savepoint"),
            agent="historian",
            correlation_id="build",
            replay_as_of=case.replay_as_of,
            publish=runner.Recorder(),
        )
        table = await historian.build_regime_table(ctx)
        assert table is not None, "no instrument had volume"
        case.table_model = table
        return table.model_dump(mode="json", exclude={"meta"})
    finally:
        tx.rollback()
        conn.close()


async def build() -> None:
    build_pool()
    engine = make_engine(testing.db_settings())
    try:
        for case in cases():
            if case.category == "regime":
                _write(case, "x", [{"text": "x"}], {"no_trade": {}})
                case.table = await _regime_table(case, engine)
                prompt = historian.regime_prompt(case.table_model) + REGIME_SUFFIX
                outs: Outputs = []
            elif case.category == "stopnoise":
                case.question = "probe"
                outs = await _tool_outputs(case, "x", engine)
                atr1 = outs[0]["data"]["result"]["atr"]
                dist = round(atr1 * case.stop_mult, case.dp)
                case.stop_distance = dist
                case.question = (
                    f"@Historian stop-noise check: long {case.inst_id} with the stop {dist} below "
                    "entry. Is it inside normal 4h noise?"
                )
                prompt = mention_prompt(case)
            else:
                prompt = mention_prompt(case)
                outs = await _tool_outputs(case, prompt, engine)
            text, card = case.compose(case, outs)
            if case.category != "regime" and case.verdicts in (None, ["auto"]):
                case.verdicts = NEAR[card["verdict"]]
            steps: list[dict[str, Any]] = [{"tool": t, "input": a} for t, a in case.steps]
            steps.append({"text": text + "\n\n" + _fence(card)})
            steps.append(
                {
                    "result": {
                        "tokens_in": 3000 + 400 * len(case.steps),
                        "tokens_out": 420,
                        "turns": len(case.steps) + 1,
                    }
                }
            )
            _write(case, prompt, steps, _props(case))
            print(
                f"{case.case_id:34s} n={card.get('n', len(card.get('rows', [])))!s:>4} "
                f"verdict={card.get('verdict', '-')}"
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(build())


__all__ = ["CORE", "EXTRA", "build", "build_pool", "cases", "date"]
