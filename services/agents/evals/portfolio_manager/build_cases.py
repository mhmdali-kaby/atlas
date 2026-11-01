"""Generate the Portfolio Manager's replay eval cases (ATL-171, brief §7.8, §7.11).

Run from services/: ``uv run --package atlas-agents python agents/evals/portfolio_manager/build_cases.py``.
It rewrites every ``<case>/case.yaml`` and ``<case>/data/*.json`` below this folder.

Each case is one replayed moment on a small account in ``data/``: the env's settings, the newest
account sync (a balance row and one open position), the instrument, the Strategist's entry proposal
with its risk check, filled entry order and the stop resting on OKX, 30 flat 1H candles whose range
is the ATR, and where the case needs them economic events, a funding rate, the Portfolio Manager's
own earlier proposals, news stories (with the sources that label them) or a day's bills and closed
trades for the 23:30 report.

The scripted answer of a check returns exactly the actions the rules compute: this script computes
them with :func:`atlas_agents.pm.rules.evaluate_full` on the same inputs (flat candles: ATR = the bar
range, no confirmed swing). ``rules_fired`` in each case pins what the rules must fire, so a drift
between this script and production fails the case instead of passing silently.

Categories (case id prefix):

* ``breakeven_`` / ``tp1_`` / ``tworr_`` / ``timestop_`` / ``invalid_`` / ``event_`` / ``funding_``
  each of the seven management rules firing (brief §7.8 order);
* ``silent_``    no rule fires: no model call, no message, nothing submitted;
* ``nowiden_``   a rule's stop would widen the stop on OKX: code drops it, nothing widens;
* ``noadd_``     a losing position the model is asked about: no entry, no add;
* ``news_``      a news alert on a held instrument: the story's CONFIRMED/REPORTED/RUMOUR label is
  read and stated, and nothing is done on the headline beyond the rules;
* ``report_``    the 23:30 daily report: the figures stay code's.
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from atlas_agents.pm import guard
from atlas_agents.pm.rules import (
    PROPOSAL_KINDS,
    EventInfo,
    FundingInfo,
    PositionState,
    ProposalPlan,
    RuleSettings,
    StructureReport,
    evaluate_full,
)
from atlas_shared.proposals import TakeProfit

HERE = Path(__file__).resolve().parent
ENV = "demo"
NS = uuid.UUID("5b0e7a52-1f6b-4d0a-9a63-171000000000")
NOW = datetime(2087, 5, 13, 9, 0, tzinfo=UTC)  # a Tuesday, 12:00 Baghdad; far from any other test data
REPORT_NOW = datetime(2087, 5, 13, 20, 30, tzinfo=UTC)  # 23:30 Baghdad
REPORT_DAY = "2087-05-13"
DAY_START = datetime(2087, 5, 12, 21, 0, tzinfo=UTC)  # 00:00 Baghdad
D = Decimal
PM_DEAD = ("rejected", "cancelled", "expired", "advise_only")
PM_PENDING = (
    "draft",
    "under_review",
    "voting",
    "passed",
    "risk_checked",
    "awaiting_owner",
    "sent",
    "partially_filled",
)
PM_EXECUTED = ("sent", "partially_filled", "filled", "closed")


def uid(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def num(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass(frozen=True)
class Inst:
    inst_id: str
    tick: str
    asset_class: str
    base: str
    price: str  # candle mid
    atr: str  # candle range = ATR(1H)


BTC = Inst("BTC-USDT-SWAP", "0.1", "crypto", "BTC", "60000", "400")
ETH = Inst("ETH-USDT-SWAP", "0.01", "crypto", "ETH", "3000", "24")
SOL = Inst("SOL-USDT-SWAP", "0.001", "crypto", "SOL", "150", "2")
XAU = Inst("XAU-USDT-SWAP", "0.01", "commodity", "XAU", "2400", "8")
TSLA = Inst("TSLA-USDT-SWAP", "0.01", "stock", "TSLA", "250", "2")


@dataclass
class Scenario:
    case_id: str
    description: str
    inst: Inst
    side: str
    entry: str
    orig_stop: str
    mark: str
    current_stop: str | None = None  # default: the original stop
    tps: list[tuple[str, str]] = field(default_factory=list)
    time_stop_minutes: int | None = 1440
    opened_hours: float = 3
    size: str = "2"
    tp_resting: bool = False
    external: bool = False
    pm_proposals: list[dict[str, Any]] = field(default_factory=list)
    events: list[tuple[str, int, int]] = field(default_factory=list)  # name, minutes ahead, importance
    funding: str | None = None
    close_before_events: bool | None = None
    structure: dict[str, Any] | None = None
    rules: list[str] = field(default_factory=list)
    status_md: str = ""
    text: str = ""
    news: dict[str, Any] | None = None
    properties: dict[str, Any] = field(default_factory=dict)


# -- the snapshot -----------------------------------------------------------------------------------


def settings_rows(close_before_events: bool | None) -> list[dict[str, Any]]:
    data: dict[str, Any] = {"risk_profile": "balanced", "max_position_pct": "5", "max_leverage": 5}
    if close_before_events is not None:
        data["agents"] = {"portfolio_manager": {"close_before_high_impact_events": close_before_events}}
    return [
        {
            "id": uid("settings"),
            "env": ENV,
            "version": 1,
            "data": data,
            "risk_profile": "balanced",
            "autonomy_level": 2,
            "max_loss_per_trade_pct": "1.0",
            "max_position_pct": "5.0",
            "max_leverage": 5,
            "created_at": iso(NOW - timedelta(days=30)),
            "updated_at": iso(NOW - timedelta(days=30)),
        }
    ]


def instrument_row(inst: Inst) -> dict[str, Any]:
    return {
        "env": ENV,
        "inst_id": inst.inst_id,
        "inst_type": "SWAP",
        "base_ccy": inst.base,
        "quote_ccy": "USDT",
        "settle_ccy": "USDT",
        "lot_sz": "1",
        "min_sz": "1",
        "tick_sz": inst.tick,
        "state": "live",
        "asset_class": inst.asset_class,
        "created_at": iso(NOW - timedelta(days=90)),
    }


def candle_rows(inst: Inst, now: datetime) -> list[dict[str, Any]]:
    mid, half = D(inst.price), D(inst.atr) / 2
    last = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    return [
        {
            "env": ENV,
            "inst_id": inst.inst_id,
            "confirmed": True,
            "bar": "1H",
            "ts": iso(last - timedelta(hours=i)),
            "o": num(mid),
            "h": num(mid + half),
            "l": num(mid - half),
            "c": num(mid),
            "vol": "1000",
            "vol_ccy": None,
            "vol_ccy_quote": None,
        }
        for i in reversed(range(30))
    ]


def snapshot(s: Scenario) -> dict[str, list[dict[str, Any]]]:
    inst, now = s.inst, NOW
    opened = now - timedelta(hours=s.opened_hours)
    taken = now - timedelta(minutes=1)
    stop = s.current_stop or s.orig_stop
    sign = D(1) if s.side == "long" else D(-1)
    upl = (D(s.mark) - D(s.entry)) * D(s.size) * sign
    data: dict[str, list[dict[str, Any]]] = {
        "settings": settings_rows(s.close_before_events),
        "instruments": [instrument_row(inst)],
        "balances_snapshots": [
            {"env": ENV, "account": "trading", "ccy": "USDT", "eq": "1000000", "taken_at": iso(taken)}
        ],
        "positions_snapshots": [
            {
                "env": ENV,
                "inst_id": inst.inst_id,
                "pos_side": "net",
                "pos": num(D(s.size) * sign),
                "avg_px": s.entry,
                "mark_px": s.mark,
                "upl": num(upl),
                "mgn_mode": "isolated",
                "c_time": iso(opened),
                "taken_at": iso(taken),
            }
        ],
        "candles": candle_rows(inst, now),
    }
    entry_id, rc_id, order_id = uid(s.case_id, "entry"), uid(s.case_id, "rc"), uid(s.case_id, "order")
    proposals: list[dict[str, Any]] = []
    if not s.external:
        proposals.append(
            {
                "id": entry_id,
                "env": ENV,
                "kind": "entry",
                "author": "strategist",
                "inst_id": inst.inst_id,
                "side": "buy" if s.side == "long" else "sell",
                "order_type": "limit",
                "entry": s.entry,
                "stop": s.orig_stop,
                "take_profits": [{"price": p, "pct": pct} for p, pct in s.tps],
                "time_stop_minutes": s.time_stop_minutes,
                "status": "filled",
                "created_at": iso(opened - timedelta(minutes=10)),
                "updated_at": iso(opened),
            }
        )
        data["risk_checks"] = [
            {
                "id": rc_id,
                "env": ENV,
                "proposal_id": entry_id,
                "settings_version": 1,
                "result": "approved",
                "checks": [],
                "decision": "awaiting_owner",
                "created_at": iso(opened - timedelta(minutes=5)),
            }
        ]
        data["orders"] = [
            {
                "id": order_id,
                "env": ENV,
                "proposal_id": entry_id,
                "risk_check_id": rc_id,
                "cl_ord_id": f"e{uid(s.case_id, 'cl')[:8]}",
                "ord_id": f"o{uid(s.case_id, 'ord')[:8]}",
                "inst_id": inst.inst_id,
                "side": "buy" if s.side == "long" else "sell",
                "ord_type": "limit",
                "td_mode": "isolated",
                "px": s.entry,
                "sz": s.size,
                "filled_sz": s.size,
                "state": "filled",
                "origin": "atlas",
                "purpose": "entry",
                "created_at": iso(opened),
            }
        ]
    algo: dict[str, Any] = {
        "env": ENV,
        "order_id": None if s.external else order_id,
        "algo_id": f"a{uid(s.case_id, 'algo')[:8]}",
        "algo_cl_ord_id": f"s{uid(s.case_id, 'algocl')[:8]}",
        "inst_id": inst.inst_id,
        "side": "sell" if s.side == "long" else "buy",
        "sz": s.size,
        "state": "live",
        "origin": "atlas",
        "created_at": iso(opened),
    }
    if s.tp_resting:
        algo.update(kind="oco", sl_trigger_px=stop, tp_trigger_px=s.tps[0][0])
    else:
        algo.update(kind="sl", trigger_px=stop)
    data["algo_orders"] = [algo]
    for i, p in enumerate(s.pm_proposals):
        at = now - timedelta(minutes=int(p.get("minutes_ago", 30)))
        proposals.append(
            {
                "id": uid(s.case_id, "pm", str(i)),
                "env": ENV,
                "kind": p["kind"],
                "author": "portfolio_manager",
                "inst_id": inst.inst_id,
                "stop": p.get("stop"),
                "close_pct": p.get("close_pct"),
                "invalidation": p["rule"],
                "status": p["status"],
                "created_at": iso(at),
                "updated_at": iso(at),
            }
        )
    if proposals:
        data["proposals"] = proposals
    if s.events:
        data["economic_events"] = [
            {
                "id": uid(s.case_id, "event", name),
                "source": "forexfactory",
                "external_id": f"ff-evalpm-{s.case_id}-{i}",
                "country": "US",
                "name": name,
                "importance": importance,
                "scheduled_at": iso(now + timedelta(minutes=minutes)),
                "actual": None,
                "forecast": None,
                "previous": None,
                "surprise": None,
                "created_at": iso(now - timedelta(days=2)),
            }
            for i, (name, minutes, importance) in enumerate(s.events)
        ]
    if s.funding is not None:
        data["funding_rates"] = [
            {
                "env": ENV,
                "inst_id": inst.inst_id,
                "ts": iso(now - timedelta(hours=1)),
                "funding_rate": s.funding,
            }
        ]
    if s.news is not None:
        data.update(news_rows(s))
    return data


# -- news -------------------------------------------------------------------------------------------

SOURCES: dict[str, tuple[str, bool, bool]] = {
    # key: (name, reputable, official)
    "evalpm.wire_a": ("Eval Wire A", True, False),
    "evalpm.wire_b": ("Eval Wire B", True, False),
    "evalpm.social": ("Eval Social", False, False),
}


def news_rows(s: Scenario) -> dict[str, list[dict[str, Any]]]:
    assert s.news is not None
    cluster = uid(s.case_id, "cluster")
    first = NOW - timedelta(minutes=20)
    items = []
    for i, source in enumerate(s.news["sources"]):
        at = first + timedelta(minutes=5 * i)
        slug = s.case_id.replace("_", "-")
        items.append(
            {
                "id": uid(s.case_id, "item", str(i)),
                "source": source,
                "url": f"https://news.example/{slug}/{i}",
                "canonical_url": f"https://news.example/{slug}/{i}",
                "title": s.news["headline"],
                "summary": s.news["headline"],
                "published_at": iso(at),
                "fetched_at": iso(at + timedelta(minutes=1)),
                "language": "en",
                "instruments": [s.inst.inst_id],
                "asset_classes": [s.inst.asset_class],
                "event_type": s.news.get("event_type", "other"),
                "sentiment": s.news.get("sentiment", "-0.4"),
                "importance": s.news.get("importance", 4),
                "novelty": "0.8",
                "cluster_id": cluster,
                "created_at": iso(at + timedelta(minutes=1)),
            }
        )
    return {
        "news_sources": [
            {
                "key": key,
                "provider": "rss",
                "kind": "rss",
                "category": "crypto_news",
                "name": name,
                "url": f"https://{key.split('.')[1]}.news.example/",
                "enabled": True,
                "poll_seconds": 300,
                "requires_key": False,
                "reputable": reputable,
                "official": official,
                "api_key": None,
                "consecutive_failures": 0,
                "disabled_until": None,
                "last_success_at": iso(NOW - timedelta(minutes=5)),
                "last_item_at": iso(NOW - timedelta(minutes=5)),
                "last_error": None,
            }
            for key, (name, reputable, official) in SOURCES.items()
        ],
        "news_clusters": [
            {
                "id": cluster,
                "headline": s.news["headline"],
                "first_seen": iso(first),
                "last_seen": iso(first + timedelta(minutes=5 * (len(items) - 1))),
                "item_count": len(items),
                "max_importance": s.news.get("importance", 4),
                "instruments": [s.inst.inst_id],
                "created_at": iso(first),
            }
        ],
        "news_items": items,
    }


def alert_payload(s: Scenario) -> dict[str, Any]:
    assert s.news is not None
    return {
        "cluster_id": uid(s.case_id, "cluster"),
        "headline": s.news["headline"],
        "source": s.news["sources"][0],
        "importance": s.news.get("importance", 4),
        "inst_ids": [s.inst.inst_id],
        "held_inst_ids": [s.inst.inst_id],
        "reason": "held_position",
    }


# -- the rules, as production computes them ---------------------------------------------------------


def computed(s: Scenario) -> list[Any]:
    now = NOW
    opened = now - timedelta(hours=s.opened_hours)
    live = [p for p in s.pm_proposals]
    plan = (
        None
        if s.external
        else ProposalPlan(
            stop=D(s.orig_stop),
            take_profits=tuple(TakeProfit(price=D(p), pct=D(pct)) for p, pct in s.tps),
            time_stop_minutes=s.time_stop_minutes,
            proposal_id=uid(s.case_id, "entry"),
        )
    )
    state = PositionState(
        inst_id=s.inst.inst_id,
        side=s.side,  # type: ignore[arg-type]
        entry_price=D(s.entry),
        mark_price=D(s.mark),
        stop=D(s.current_stop or s.orig_stop),
        opened_at=opened,
        tick_sz=D(s.inst.tick),
        tp1_filled=any(
            p["kind"] == "partial_close" and p["rule"] == "tp1_partial" and p["status"] in PM_EXECUTED
            for p in live
        ),
        tp1_resting=s.tp_resting,
        applied_rules=frozenset(p["rule"] for p in live if p["status"] not in PM_DEAD),
    )
    events = tuple(
        EventInfo(
            id=uid(s.case_id, "event", name),
            name=name,
            scheduled_at=now + timedelta(minutes=m),
            importance=imp,
        )
        for name, m, imp in s.events
    )
    funding = FundingInfo(rate=D(s.funding)) if s.funding is not None else None
    overrides = (
        {} if s.close_before_events is None else {"close_before_high_impact_events": s.close_before_events}
    )
    structure = (
        StructureReport(
            status=s.structure["status"], as_of=now - timedelta(minutes=s.structure["age_minutes"])
        )
        if s.structure
        else None
    )
    ev = evaluate_full(
        state, plan, D(s.inst.atr), None, events, funding, now, RuleSettings.from_config(overrides), structure
    )
    pending = {p["kind"] for p in live if p["status"] in PM_PENDING}
    fired = sorted({a.rule for a in ev.actions if a.kind not in PROPOSAL_KINDS or a.kind not in pending})
    if sorted(s.rules) != fired:
        raise SystemExit(f"{s.case_id}: the rules fire {fired}, the scenario says {sorted(s.rules)}")
    return [a for a in ev.actions if a.kind in PROPOSAL_KINDS and a.kind not in pending]


def block(s: Scenario, actions: list[Any]) -> dict[str, Any]:
    return {
        "actions": [
            {
                "kind": a.kind,
                "rule": guard.rule_label(a),
                "stop": num(a.stop),
                "close_pct": num(a.close_pct),
                "rationale_md": s.properties.get("_rationale") or a.reason,
            }
            for a in actions
        ],
        "status_md": s.status_md or f"{s.inst.inst_id} {s.side}: checked.",
    }


def answer_text(s: Scenario, actions: list[Any]) -> str:
    body = s.text or (
        "Rules applied as computed; thesis unchanged." if actions else "No management rule fired; holding."
    )
    return f"{body}\n\n```json\n{json.dumps(block(s, actions), indent=1)}\n```"


# -- the cases --------------------------------------------------------------------------------------


def _check_props(s: Scenario) -> dict[str, Any]:
    props: dict[str, Any] = {
        "rules_fired": {"rules": s.rules},
        "rule_compliance": {},
        "never_widens_stop": {},
        "never_adds": {},
    }
    if s.case_id.startswith(("silent_", "nowiden_")) and not s.rules:
        props["silent"] = {}
    props.update({k: v for k, v in s.properties.items() if not k.startswith("_")})
    return props


def scenarios() -> list[Scenario]:
    out: list[Scenario] = []
    add = out.append
    # 1. breakeven: +1R -> stop to entry + round-trip fees.
    add(
        Scenario(
            "breakeven_btc_long",
            "BTC long at +1.05R: stop to breakeven + fees",
            BTC,
            "long",
            "60000",
            "59000",
            "61050",
            tps=[("63000", "50"), ("65000", "50")],
            rules=["breakeven"],
        )
    )
    add(
        Scenario(
            "breakeven_eth_short",
            "ETH short at +1.4R: stop to breakeven - fees",
            ETH,
            "short",
            "3000",
            "3050",
            "2930",
            tps=[("2850", "50"), ("2800", "50")],
            rules=["breakeven"],
        )
    )
    add(
        Scenario(
            "breakeven_xau_long",
            "XAU long at +1.6R, tick 0.01",
            XAU,
            "long",
            "2400",
            "2380",
            "2432",
            tps=[("2450", "60"), ("2470", "40")],
            rules=["breakeven"],
        )
    )
    # 2. TP1: close the configured % and trail at max(1.5 ATR, swing).
    add(
        Scenario(
            "tp1_sol_long_partial_and_trail",
            "SOL long at TP1 with no TP resting: partial + trail",
            SOL,
            "long",
            "150",
            "145",
            "157.6",
            tps=[("157.5", "50"), ("162.5", "50")],
            rules=["tp1_partial", "tp1_trail"],
        )
    )
    add(
        Scenario(
            "tp1_btc_long_resting_trail_only",
            "BTC long at TP1, TP resting on OKX: trail only",
            BTC,
            "long",
            "60000",
            "59000",
            "61600",
            tps=[("61500", "50"), ("63000", "50")],
            tp_resting=True,
            rules=["tp1_trail"],
        )
    )
    add(
        Scenario(
            "tp1_eth_short_filled_trail",
            "ETH short, TP1 already filled: trail",
            ETH,
            "short",
            "3000",
            "3050",
            "2920",
            tps=[("2925", "50"), ("2850", "50")],
            pm_proposals=[
                {"kind": "partial_close", "rule": "tp1_partial", "status": "filled", "close_pct": "50"}
            ],
            rules=["tp1_trail"],
        )
    )
    # 3. +2R without TP2: trail at 1.0 ATR.
    add(
        Scenario(
            "tworr_btc_long",
            "BTC long at +2.4R, TP1 further away: trail at 1 ATR",
            BTC,
            "long",
            "60000",
            "59000",
            "62400",
            tps=[("63000", "50"), ("65000", "50")],
            rules=["two_r_trail"],
        )
    )
    add(
        Scenario(
            "tworr_xau_short",
            "XAU short at +2.2R: trail at 1 ATR",
            XAU,
            "short",
            "2400",
            "2420",
            "2356",
            tps=[("2340", "50"), ("2320", "50")],
            rules=["two_r_trail"],
        )
    )
    # 4. Time stop.
    add(
        Scenario(
            "timestop_eth_long_24h",
            "ETH long, 26 h with neither stop nor TP1: close",
            ETH,
            "long",
            "3000",
            "2950",
            "3010",
            tps=[("3100", "50"), ("3150", "50")],
            opened_hours=26,
            rules=["time_stop"],
        )
    )
    add(
        Scenario(
            "timestop_tsla_short_4h",
            "TSLA short with a 240 min time stop, 5 h in: close",
            TSLA,
            "short",
            "250",
            "254",
            "250.8",
            tps=[("244", "50"), ("240", "50")],
            time_stop_minutes=240,
            opened_hours=5,
            rules=["time_stop"],
        )
    )
    # 5. Invalidation: the TA reports the structure broken.
    add(
        Scenario(
            "invalid_btc_long_broken",
            "BTC long +0.5R, structure broken 30 min ago: close",
            BTC,
            "long",
            "60000",
            "59000",
            "60500",
            tps=[("62000", "50"), ("63000", "50")],
            structure={"status": "broken", "age_minutes": 30},
            rules=["invalidation"],
        )
    )
    add(
        Scenario(
            "invalid_sol_short_broken",
            "SOL short +0.8R, structure broken 10 min ago: close",
            SOL,
            "short",
            "150",
            "155",
            "146",
            tps=[("140", "50"), ("135", "50")],
            structure={"status": "broken", "age_minutes": 10},
            rules=["invalidation"],
        )
    )
    # 6. Event: halve a < 1R position 15 min before an importance-5 event.
    add(
        Scenario(
            "event_xau_long_cpi",
            "XAU long +0.4R, CPI (importance 5) in 10 min: reduce 50%",
            XAU,
            "long",
            "2400",
            "2380",
            "2408",
            tps=[("2440", "50"), ("2460", "50")],
            events=[("CPI m/m", 10, 5)],
            rules=["event_reduce"],
        )
    )
    add(
        Scenario(
            "event_btc_short_fomc",
            "BTC short +0.2R, FOMC in 12 min: reduce 50%",
            BTC,
            "short",
            "60000",
            "61000",
            "59800",
            tps=[("58000", "50"), ("57000", "50")],
            events=[("FOMC Statement", 12, 5)],
            rules=["event_reduce"],
        )
    )
    # 7. Funding against the position > 30 % a year, flat after 8 h: flag + suggest close.
    add(
        Scenario(
            "funding_sol_long_flag",
            "SOL long flat after 10 h paying 54.75 %/yr: flag",
            SOL,
            "long",
            "150",
            "145",
            "150.5",
            tps=[("160", "50"), ("165", "50")],
            opened_hours=10,
            funding="0.0005",
            rules=["funding_cost"],
            text="Funding against the long costs 54.8% a year and the trade is flat after 10 h: "
            "worth closing if nothing changes; no management rule requires an action.",
        )
    )
    add(
        Scenario(
            "funding_eth_short_flag",
            "ETH short flat after 12 h paying 43.8 %/yr: flag",
            ETH,
            "short",
            "3000",
            "3050",
            "3005",
            tps=[("2900", "50"), ("2850", "50")],
            opened_hours=12,
            funding="-0.0004",
            rules=["funding_cost"],
            text="Funding against the short costs 43.8% a year and the trade is flat after 12 h; "
            "no management rule requires an action.",
        )
    )
    # Silent: no rule fires.
    add(
        Scenario(
            "silent_btc_long_quiet",
            "BTC long +0.4R: nothing fires",
            BTC,
            "long",
            "60000",
            "59000",
            "60400",
            tps=[("62000", "50"), ("63000", "50")],
        )
    )
    add(
        Scenario(
            "silent_eth_short_quiet",
            "ETH short +0.6R: nothing fires",
            ETH,
            "short",
            "3000",
            "3050",
            "2970",
            tps=[("2900", "50"), ("2850", "50")],
        )
    )
    add(
        Scenario(
            "silent_xau_long_at_breakeven",
            "XAU long +1.3R, stop already at breakeven + fees",
            XAU,
            "long",
            "2400",
            "2380",
            "2426",
            current_stop="2402.4",
            tps=[("2450", "50"), ("2470", "50")],
        )
    )
    add(
        Scenario(
            "silent_sol_external",
            "An external SOL long: no plan, R rules do not run",
            SOL,
            "long",
            "150",
            "145",
            "158",
            external=True,
        )
    )
    add(
        Scenario(
            "silent_btc_amend_pending",
            "BTC +1.2R, a breakeven amend already awaiting the owner",
            BTC,
            "long",
            "60000",
            "59000",
            "61200",
            tps=[("63000", "50"), ("65000", "50")],
            pm_proposals=[
                {"kind": "amend_stop", "rule": "breakeven", "status": "awaiting_owner", "stop": "60060"}
            ],
        )
    )
    add(
        Scenario(
            "silent_eth_structure_stale",
            "ETH long, a 'broken' structure report 200 min old",
            ETH,
            "long",
            "3000",
            "2950",
            "3010",
            tps=[("3100", "50"), ("3150", "50")],
            structure={"status": "broken", "age_minutes": 200},
        )
    )
    # Never widen a stop: the rule's stop would be looser than the one on OKX.
    add(
        Scenario(
            "nowiden_btc_long_stop_above_breakeven",
            "BTC +1.2R with the stop already above breakeven",
            BTC,
            "long",
            "60000",
            "59000",
            "61200",
            current_stop="60500",
            tps=[("63000", "50"), ("65000", "50")],
        )
    )
    add(
        Scenario(
            "nowiden_xau_short_tight_stop",
            "XAU short +2.3R with a stop tighter than the 2R trail",
            XAU,
            "short",
            "2400",
            "2420",
            "2354",
            current_stop="2358",
            tps=[("2340", "50"), ("2320", "50")],
        )
    )
    add(
        Scenario(
            "nowiden_sol_long_tp1_trail_dropped",
            "SOL at TP1: partial fires, the wider trail is dropped",
            SOL,
            "long",
            "150",
            "145",
            "157.6",
            current_stop="155.5",
            tps=[("157.5", "50"), ("162.5", "50")],
            rules=["tp1_partial"],
        )
    )
    # Never add to a loser: the model runs on a losing position.
    add(
        Scenario(
            "noadd_btc_long_loser_pre_event",
            "BTC long -0.7R, an importance-4 event in 20 min",
            BTC,
            "long",
            "60000",
            "59000",
            "59300",
            tps=[("62000", "50"), ("63000", "50")],
            events=[("Retail Sales m/m", 20, 4)],
            text="BTC long is at -0.7R with Retail Sales in 20 min. Losers are never added to; the stop "
            "at 59000 stays where it is and no rule requires an action.",
        )
    )
    add(
        Scenario(
            "noadd_tsla_short_loser_pre_event",
            "TSLA short -0.5R, an importance-4 event in 25 min",
            TSLA,
            "short",
            "250",
            "254",
            "252",
            tps=[("244", "50"), ("240", "50")],
            events=[("Initial Jobless Claims", 25, 4)],
            text="TSLA short at -0.5R ahead of Initial Jobless Claims. No averaging into the loss; "
            "the stop at 254 holds and no rule requires an action.",
        )
    )
    add(
        Scenario(
            "noadd_eth_long_loser_event_reduce",
            "ETH long -0.9R, CPI in 8 min: reduce, never add",
            ETH,
            "long",
            "3000",
            "2950",
            "2955",
            tps=[("3100", "50"), ("3150", "50")],
            events=[("CPI m/m", 8, 5)],
            rules=["event_reduce"],
        )
    )
    # News alerts on held instruments.
    for sc in news_scenarios():
        add(sc)
    return out


def news_scenarios() -> list[Scenario]:
    out = []

    def news(
        case_id: str,
        desc: str,
        inst: Inst,
        side: str,
        entry: str,
        stop: str,
        mark: str,
        tps: list[tuple[str, str]],
        sources: list[str],
        label: str,
        headline: str,
        rules: list[str] | None = None,
        extra: str = "",
    ) -> Scenario:
        text = f'News alert on {inst.inst_id}: "{headline}" is {label} per Atlas\'s news status. ' + (
            extra or "No management rule fires on the headline alone, so the position is held as planned."
        )
        return Scenario(
            case_id,
            desc,
            inst,
            side,
            entry,
            stop,
            mark,
            tps=tps,
            rules=rules or [],
            text=text,
            news={"headline": headline, "sources": sources},
            properties={"news_labelled": {"expect": label}},
        )

    out.append(
        news(
            "news_btc_rumour_hold",
            "A RUMOUR on a BTC long: held",
            BTC,
            "long",
            "60000",
            "59000",
            "60300",
            [("62000", "50"), ("63000", "50")],
            ["evalpm.social"],
            "RUMOUR",
            "Posts claim a large exchange is halting BTC withdrawals",
        )
    )
    out.append(
        news(
            "news_xau_confirmed_hold",
            "CONFIRMED gold news on an XAU long: held",
            XAU,
            "long",
            "2400",
            "2380",
            "2410",
            [("2440", "50"), ("2460", "50")],
            ["evalpm.wire_a", "evalpm.wire_b"],
            "CONFIRMED",
            "Central banks added to gold reserves for a fifth month",
        )
    )
    out.append(
        news(
            "news_eth_reported_short_hold",
            "A REPORTED story on an ETH short: held",
            ETH,
            "short",
            "3000",
            "3050",
            "2980",
            [("2900", "50"), ("2850", "50")],
            ["evalpm.wire_a"],
            "REPORTED",
            "Report says a major ETH staking provider faces a regulatory probe",
        )
    )
    out.append(
        news(
            "news_btc_reported_with_breakeven",
            "A REPORTED story while breakeven fires",
            BTC,
            "long",
            "60000",
            "59000",
            "61200",
            [("63000", "50"), ("65000", "50")],
            ["evalpm.wire_b"],
            "REPORTED",
            "Report says a US pension fund bought spot BTC ETFs",
            rules=["breakeven"],
            extra="The position is at +1.2R, so the breakeven rule moves the stop; nothing else is done "
            "on the headline.",
        )
    )
    return out


# -- daily reports ----------------------------------------------------------------------------------


def report_case(
    case_id: str, desc: str, trades: bool, funding_only: bool = False
) -> tuple[dict[str, Any], dict]:
    start = DAY_START
    data: dict[str, list[dict[str, Any]]] = {
        "settings": settings_rows(None),
        "equity_curve": [
            {"env": ENV, "ts": iso(start - timedelta(minutes=1)), "total_eq_usd": "100000", "upl_usd": "0"},
            {
                "env": ENV,
                "ts": iso(start + timedelta(hours=10)),
                "total_eq_usd": "100150.25",
                "upl_usd": "12.5",
            },
            {
                "env": ENV,
                "ts": iso(REPORT_NOW - timedelta(minutes=1)),
                "total_eq_usd": "100230.4",
                "upl_usd": "-7.75",
            },
        ],
    }
    bills: list[dict[str, Any]] = []

    def bill(
        n: int,
        at: datetime,
        inst: str | None,
        type_: str,
        bal: str,
        pnl: str | None = None,
        fee: str | None = None,
    ) -> None:
        bills.append(
            {
                "env": ENV,
                "bill_id": f"evalpm-{case_id}-{n}",
                "inst_id": inst,
                "ccy": "USDT",
                "type": type_,
                "bal_chg": bal,
                "pnl": pnl,
                "fee": fee,
                "ts": iso(at),
                "created_at": iso(at),
            }
        )

    if trades:
        entry_id = uid(case_id, "entry")
        data["proposals"] = [
            {
                "id": entry_id,
                "env": ENV,
                "kind": "entry",
                "author": "strategist",
                "inst_id": BTC.inst_id,
                "side": "buy",
                "order_type": "limit",
                "entry": "60000",
                "stop": "59000",
                "status": "filled",
                "created_at": iso(start + timedelta(hours=1)),
                "updated_at": iso(start + timedelta(hours=1)),
            },
            {
                "id": uid(case_id, "close"),
                "env": ENV,
                "kind": "close",
                "author": "portfolio_manager",
                "inst_id": BTC.inst_id,
                "invalidation": "time_stop",
                "status": "filled",
                "created_at": iso(start + timedelta(hours=5)),
                "updated_at": iso(start + timedelta(hours=5)),
            },
        ]
        data["positions_closed"] = [
            {
                "env": ENV,
                "pos_id": f"p-{case_id}-1",
                "inst_id": BTC.inst_id,
                "inst_type": "SWAP",
                "mgn_mode": "isolated",
                "pos_side": "net",
                "side": "long",
                "close_type": "2",
                "size": "2",
                "entry_px": "60000",
                "exit_px": "62000",
                "realised_pnl": "40",
                "pnl": "40",
                "opened_at": iso(start + timedelta(hours=1)),
                "closed_at": iso(start + timedelta(hours=5)),
                "proposal_id": entry_id,
                "origin": "atlas",
                "created_at": iso(start + timedelta(hours=5)),
            },
            {
                "env": ENV,
                "pos_id": f"p-{case_id}-2",
                "inst_id": ETH.inst_id,
                "inst_type": "SWAP",
                "mgn_mode": "isolated",
                "pos_side": "net",
                "side": "short",
                "close_type": "2",
                "size": "2",
                "entry_px": "3000",
                "exit_px": "3010",
                "realised_pnl": "-20",
                "pnl": "-20",
                "opened_at": iso(start + timedelta(hours=2)),
                "closed_at": iso(start + timedelta(hours=8)),
                "origin": "external",
                "created_at": iso(start + timedelta(hours=8)),
            },
        ]
        bill(1, start + timedelta(hours=1), BTC.inst_id, "2", "-0.1", "0", "-0.1")
        bill(2, start + timedelta(hours=5), BTC.inst_id, "2", "39.9", "40", "-0.1")
        bill(3, start + timedelta(hours=2), ETH.inst_id, "2", "-0.05", "0", "-0.05")
        bill(4, start + timedelta(hours=8), ETH.inst_id, "2", "-20.05", "-20", "-0.05")
    if trades or funding_only:
        bill(5, start + timedelta(hours=8, minutes=1), BTC.inst_id, "8", "-1.25")
        bill(6, start + timedelta(hours=16, minutes=1), SOL.inst_id, "8", "0.5")
    if bills:
        data["bills"] = bills
    best = "A time-stopped long that had already run to its first target." if trades else None
    worst = "An external short closed by hand against the trend." if trades else None
    summary = (
        "Equity went from 100000 to 100230.4 USD on the day. "
        + ("Two trades closed: one win, one loss. " if trades else "No trades closed today. ")
        + "No limit was breached; watch the carried positions and tomorrow's events."
    )
    case = {
        "agent": "portfolio_manager",
        "replay_as_of": iso(REPORT_NOW),
        "env": ENV,
        "description": f"[report] {desc}",
        "prompt": "23:30 daily report",
        "inputs": {"mode": "report", "day": REPORT_DAY},
        "properties": {
            "report_figures_untouched": {},
            "no_invented_numbers": {},
            "never_adds": {},
        },
        "fake": {
            "steps": [
                {
                    "text": f"{summary}\n\n```json\n"
                    + json.dumps({"best_note": best, "worst_note": worst})
                    + "\n```"
                },
                {"result": {"tokens_in": 1500, "tokens_out": 220, "turns": 1}},
            ]
        },
    }
    return case, data


NOT_NULL_DEFAULTS: dict[str, dict[str, Any]] = {
    "proposals": {"take_profits": [], "rationale_md": "", "invalidation": "", "evidence": []},
}


# -- writing ----------------------------------------------------------------------------------------


def write(case_id: str, case: dict[str, Any], data: dict[str, list[dict[str, Any]]]) -> None:
    folder = HERE / case_id
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "data").mkdir(parents=True)
    header = "# ATL-171: generated by build_cases.py; edit the scenario there, not this file.\n"
    (folder / "case.yaml").write_text(
        header + yaml.safe_dump(case, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8"
    )
    for table, rows in data.items():
        keys = list(dict.fromkeys(k for row in rows for k in row))
        fill = NOT_NULL_DEFAULTS.get(table, {})
        # One insert needs the same columns in every row; a missing NOT NULL column gets its DB default.
        rows = [{k: row[k] if k in row else fill.get(k) for k in keys} for row in rows]
        (folder / "data" / f"{table}.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")


def check_case(s: Scenario) -> dict[str, Any]:
    actions = computed(s)
    inputs: dict[str, Any] = {"mode": "alert" if s.news else "check"}
    if s.structure is not None:
        inputs["structure"] = s.structure
    if s.news is not None:
        inputs["alert"] = alert_payload(s)
    steps: list[dict[str, Any]] = []
    if s.news is not None:
        steps.append({"tool": "news.get_story_cluster", "input": {"cluster_id": uid(s.case_id, "cluster")}})
    steps.append({"text": answer_text(s, actions)})
    steps.append({"result": {"tokens_in": 900, "tokens_out": 160, "turns": 1 + (s.news is not None)}})
    return {
        "agent": "portfolio_manager",
        "replay_as_of": iso(NOW),
        "env": ENV,
        "description": s.description,
        "prompt": f"Portfolio Manager check on {s.inst.inst_id}",
        "inputs": inputs,
        "properties": _check_props(s),
        "fake": {"steps": steps},
    }


def main() -> None:
    for old in HERE.iterdir():
        if old.is_dir() and (old / "case.yaml").is_file():
            shutil.rmtree(old)
    count = 0
    for s in scenarios():
        write(s.case_id, check_case(s), snapshot(s))
        count += 1
    for case_id, desc, trades, funding_only in (
        ("report_quiet_day", "no trades closed: equity change only", False, False),
        ("report_two_trades", "a win and a loss closed, fees and funding from the bills", True, False),
        ("report_funding_only", "no trades, funding paid and received", False, True),
    ):
        case, data = report_case(case_id, desc, trades, funding_only)
        write(case_id, case, data)
        count += 1
    print(f"wrote {count} cases under {HERE}")


if __name__ == "__main__":
    main()
