"""Generate the Devil's Advocate's replay eval cases (ATL-167, brief §7.7, §7.11).

Run from services/: ``uv run --package atlas-agents python agents/evals/devils_advocate/build_cases.py``.
It rewrites every ``<case>/case.yaml`` and ``<case>/data/*.json`` below this folder.

Each case is one replayed moment: the env's settings, the instrument, 200 1H and 60 4H candles, the
Strategist's proposal (status under_review) with its War Room card, and where the case needs them
economic or earnings events inside the trade window and (re-rates) the stored round-1 review. Code
computes the review table from that world (stop noise against the median 4H range, chasing in
ATR(1H), correlation with open positions, events in the window); the scripted answer reviews it
with numbers from that table and the proposal only.

The candles are a slow sine around the instrument's price: every 4H bar spans 1.2 % of the price
(so 0.8 x the median 4H range is 0.96 %) and every 1H bar 0.6 %. A clean stop sits 2 % from the
entry (outside noise); a stop "inside noise" sits 0.7 % from it.

Categories (case id prefix):

* ``items_``   all 10 checklist items addressed, with minor problems on different items;
* ``guide_``   the risk rating follows the §7.7 guide (1, 2, 3, 4 and 5);
* ``event_``   a high-impact event (CPI, FOMC, NFP, earnings) inside the window is flagged (item 1);
* ``noise_``   a stop inside normal 4H noise is flagged (item 4);
* ``clean_``   a clean proposal is not objected to: rating <= 2 and a take (false-alarm cases);
* ``rerate_``  the Strategist's reply gets exactly one re-rate; a second reply, a reply to the
  re-rate, or a stored re-rate starts nothing.
"""

from __future__ import annotations

import json
import math
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
AGENT = "devils_advocate"
ENV = "demo"
NS = uuid.UUID("5b0e7a52-1f6b-4d0a-9a63-167000000da0")
D = Decimal
CHECKLIST = (
    "event_risk",
    "crowding",
    "history",
    "stop_quality",
    "chasing",
    "correlation",
    "liquidity",
    "priced_in",
    "data_quality",
    "overconfidence",
)


def uid(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def rnd(value: Decimal, tick: Decimal) -> Decimal:
    return ((value / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick).normalize()


def fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class Inst:
    inst_id: str
    price: Decimal
    tick: Decimal
    asset_class: str

    @property
    def base(self) -> str:
        return self.inst_id.split("-")[0]


INSTS = {
    "btc": Inst("BTC-USDT-SWAP", D("64250"), D("0.1"), "crypto"),
    "eth": Inst("ETH-USDT-SWAP", D("2480"), D("0.01"), "crypto"),
    "sol": Inst("SOL-USDT-SWAP", D("152.4"), D("0.01"), "crypto"),
    "xau": Inst("XAU-USDT-SWAP", D("2655"), D("0.01"), "commodity"),
    "xag": Inst("XAG-USDT-SWAP", D("31.2"), D("0.001"), "commodity"),
    "tsla": Inst("TSLA-USDT-SWAP", D("251.3"), D("0.01"), "stock"),
    "nvda": Inst("NVDA-USDT-SWAP", D("121.7"), D("0.01"), "stock"),
    "spy": Inst("SPY-USDT-SWAP", D("571.4"), D("0.01"), "etf"),
    "link": Inst("LINK-USDT-SWAP", D("11.52"), D("0.001"), "crypto"),
    "avax": Inst("AVAX-USDT-SWAP", D("27.1"), D("0.001"), "crypto"),
}

# Item -> (severity, detail template). Templates use the case's numbers: {entry} {stop} {dist}
# {thr} {ratio} {p} {r} {size} {event} {imp} {minutes} {window} {date}.
PROBLEMS: dict[str, tuple[int, str]] = {
    "event": (1, "{event} (importance {imp}) is {minutes} minutes into the {window}-minute window"),
    "earnings": (1, "{base} earnings on {date} fall inside the {window}-minute window"),
    "noise": (
        4,
        "stop {stop} is {dist} from the entry {entry}, inside 0.8 x the median 4H range (threshold "
        "{thr}, ratio {ratio})",
    ),
    "history": (3, "probability {p} rests on no journal record of this setup on {base}"),
    "liquidity": (7, "size suggestion {size} of equity on a stock perp outside US cash hours"),
    "overconfidence": (10, "probability {p} with expected R {r} is ambitious for this setup"),
    "priced_in": (8, "the catalyst may be priced in; expected R {r} assumes it is not"),
    "data_quality": (9, "the 4H noise check uses 60 bars of demo data; probability {p} unverified"),
    "crowding": (2, "no positioning report backs the long; probability {p} ignores crowding"),
}


@dataclass
class Spec:
    case_id: str
    category: str
    inst: str
    side: str = "buy"
    day: int = 1  # April 2026
    hour: int = 9
    noise: bool = False  # stop inside noise
    events: list[tuple[str, int, int]] = field(default_factory=list)  # (name, importance, minutes)
    earnings_in_days: int | None = None
    problems: dict[str, str] = field(default_factory=dict)  # PROBLEMS key -> severity
    vote: str = "take"
    confidence: str = "0.75"
    fix: str | None = None
    fix_applied: bool = False
    p: str = "0.55"
    r: str = "2.4"
    size: str = "2.5"
    strongest: str | None = None
    # rerate cases
    rerate: str | None = None  # once | twice | thrice | stored | to_rerate
    rerate_problems: dict[str, str] | None = None
    rerate_vote: str | None = None
    rerate_fix_applied: bool = False

    @property
    def at(self) -> datetime:
        return datetime(2026, 4, self.day, self.hour, 0, tzinfo=UTC)


class World:
    """The prices and the review numbers of one case."""

    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.inst = INSTS[spec.inst]
        p, t = self.inst.price, self.inst.tick
        self.sign = 1 if spec.side == "buy" else -1
        self.zone = [rnd(p * D("0.999"), t), rnd(p * D("1.001"), t)]
        self.mid = (self.zone[0] + self.zone[1]) / 2
        frac = D("0.007") if spec.noise else D("0.02")
        self.stop = rnd(self.mid - self.sign * frac * p, t)
        self.targets = [rnd(self.mid + self.sign * k * p, t) for k in (D("0.04"), D("0.06"))]
        self.dist = abs(self.mid - self.stop)
        self.median_4h = 2 * rnd(p * D("0.006"), t)
        self.threshold = D("0.8") * self.median_4h
        self.ratio = self.dist / self.threshold

    def values(self, event: tuple[str, int, int] | None) -> dict[str, str]:
        s = self.spec
        out = {
            "entry": fmt(self.mid),
            "stop": fmt(self.stop),
            "dist": fmt(self.dist),
            "thr": fmt(self.threshold.quantize(self.inst.tick)),
            "ratio": fmt(self.ratio.quantize(D("0.01"))),
            "p": s.p,
            "r": s.r,
            "size": s.size,
            "base": self.inst.base,
            "window": "1440",
            "date": (s.at + timedelta(days=s.earnings_in_days or 0)).date().isoformat(),
        }
        if event is not None:
            out.update(event=event[0], imp=str(event[1]), minutes=str(event[2]))
        return out


def candles(world: World, bar: str, step: timedelta, bars: int, half_range: Decimal) -> list[dict[str, Any]]:
    inst, p, t = world.inst, world.inst.price, world.inst.tick
    last_open = world.spec.at - step
    rows = []
    for i in range(bars):
        c = rnd(p * (1 + D(repr(0.004 * math.sin(i / 7)))), t)
        rows.append(
            {
                "env": ENV,
                "inst_id": inst.inst_id,
                "bar": bar,
                "ts": iso(last_open - step * (bars - 1 - i)),
                "o": fmt(rnd(c - p * D("0.0005"), t)),
                "h": fmt(c + rnd(p * half_range, t)),
                "l": fmt(c - rnd(p * half_range, t)),
                "c": fmt(c),
                "vol": "1000",
                "vol_ccy": None,
                "vol_ccy_quote": "1000000",
                "confirmed": True,
            }
        )
    return rows


def proposal_row(spec: Spec, world: World) -> dict[str, Any]:
    return {
        "id": uid(spec.case_id, "proposal"),
        "env": ENV,
        "version": 1,
        "kind": "entry",
        "author": "strategist",
        "inst_id": world.inst.inst_id,
        "side": spec.side,
        "order_type": "limit",
        "entry_zone": [fmt(world.zone[0]), fmt(world.zone[1])],
        "stop": fmt(world.stop),
        "take_profits": [
            {"price": fmt(tp), "pct": pct} for tp, pct in zip(world.targets, ["50", "50"], strict=True)
        ],
        "time_stop_minutes": 1440,
        "size_suggestion_pct": spec.size,
        "leverage_suggestion": 2,
        "expected_r": spec.r,
        "probability": spec.p,
        "rationale_md": f"{'Long' if spec.side == 'buy' else 'Short'} {world.inst.inst_id} from the setup.",
        "invalidation": f"1H close beyond {fmt(world.stop)}",
        "evidence": [],
        "status": "under_review",
        "created_at": iso(spec.at - timedelta(minutes=2)),
        "updated_at": iso(spec.at - timedelta(minutes=2)),
    }


def message(
    spec: Spec,
    name: str,
    author: str,
    kind: str,
    content: str,
    card: Any,
    at: datetime,
    reply_to: str | None = None,
) -> dict[str, Any]:
    return {
        "id": uid(spec.case_id, "msg", name),
        "env": ENV,
        "thread_id": uid(spec.case_id, "thread"),
        "event_id": uid(spec.case_id, "event", name),
        "author_type": "agent",
        "author": author,
        "kind": kind,
        "content_md": content,
        "card": card,
        "reply_to": reply_to,
        "mentions": [],
        "created_at": iso(at),
        "updated_at": iso(at),
    }


def items(spec: Spec, world: World, problems: dict[str, str]) -> list[dict[str, Any]]:
    event = spec.events[0] if spec.events else None
    values = world.values(event)
    out = []
    by_item = {PROBLEMS[k][0]: (k, sev) for k, sev in problems.items()}
    for n, name in enumerate(CHECKLIST, start=1):
        if n in by_item:
            key, sev = by_item[n]
            out.append(
                {
                    "item": n,
                    "name": name,
                    "status": "problem",
                    "severity": sev,
                    "detail": PROBLEMS[key][1].format(**values),
                }
            )
        else:
            out.append({"item": n, "name": name, "status": "ok", "severity": "none", "detail": "OK"})
    return out


def guide(item_list: list[dict[str, Any]]) -> int:
    sev = [i["severity"] for i in item_list]
    if "loss_gt_1r" in sev:
        return 5
    serious = sev.count("serious")
    return 4 if serious >= 2 else 3 if serious == 1 else 2 if "minor" in sev else 1


def review_card(
    spec: Spec, world: World, problems: dict[str, str], vote: str, fix: str | None, fix_applied: bool
) -> dict[str, Any]:
    item_list = items(spec, world, problems)
    worst = next((i for i in item_list if i["severity"] in ("loss_gt_1r", "serious")), None) or next(
        (i for i in item_list if i["status"] == "problem"), None
    )
    strongest = spec.strongest or (worst["detail"] if worst else "None: every item is OK.")
    return {
        "type": "da_review",
        "items": item_list,
        "strongest_objection": strongest,
        "risk_rating": guide(item_list),
        "fix": fix,
        "fix_applied": fix_applied,
        "vote": vote,
        "confidence": float(spec.confidence),
        "reason": strongest if worst else "Clean proposal: nothing in the table argues against it.",
    }


def answer(card: dict[str, Any]) -> str:
    lead = card["strongest_objection"]
    return f"**Strongest objection:** {lead}\n\n```json\n{json.dumps(card, indent=1)}\n```"


def snapshot(spec: Spec, world: World) -> dict[str, list[dict[str, Any]]]:
    at, case, inst = spec.at, spec.case_id, world.inst
    settings_id = uid(case, "settings")
    values = {"risk_profile": "balanced", "min_expected_r": "1.8", "max_position_pct": "5", "max_leverage": 5}
    data: dict[str, list[dict[str, Any]]] = {
        "settings": [
            {
                "id": settings_id,
                "env": ENV,
                "version": 1,
                "data": values,
                "risk_profile": "balanced",
                "autonomy_level": 2,
                "max_loss_per_trade_pct": "1.0",
                "max_position_pct": "5.0",
                "max_leverage": 5,
                "created_at": iso(at - timedelta(days=3)),
                "updated_at": iso(at - timedelta(days=3)),
            }
        ],
        "settings_history": [
            {
                "settings_id": settings_id,
                "version": 1,
                "data": values,
                "diff": {},
                "changed_by": None,
                "created_at": iso(at - timedelta(days=3)),
            }
        ],
        "instruments": [
            {
                "id": uid(case, "inst"),
                "env": ENV,
                "inst_id": inst.inst_id,
                "inst_type": "SWAP",
                "inst_family": inst.inst_id.removesuffix("-SWAP"),
                "base_ccy": inst.base,
                "quote_ccy": "USDT",
                "settle_ccy": "USDT",
                "ct_val": "1",
                "lot_sz": "1",
                "min_sz": "1",
                "tick_sz": fmt(inst.tick),
                "max_lever": "20",
                "state": "live",
                "asset_class": inst.asset_class,
                "list_time": iso(at - timedelta(days=400)),
            }
        ],
        "candles": candles(world, "1H", timedelta(hours=1), 200, D("0.003"))
        + candles(world, "4H", timedelta(hours=4), 60, D("0.006")),
        "warroom_threads": [
            {
                "id": uid(case, "thread"),
                "env": ENV,
                "kind": "main",
                "key": f"eval-{case}"[:64],
                "title": "Main",
                "created_at": iso(at - timedelta(days=1)),
            }
        ],
        "proposals": [proposal_row(spec, world)],
    }
    proposal_id = uid(case, "proposal")
    messages = [
        message(
            spec,
            "proposal",
            "strategist",
            "proposal",
            f"**{spec.side.upper()} {inst.inst_id}** v1",
            {"type": "proposal", "proposal_id": proposal_id, "inst_id": inst.inst_id, "version": 1},
            at - timedelta(minutes=2),
        )
    ]
    if spec.rerate:
        r1 = review_card(spec, world, spec.problems, spec.vote, spec.fix, False)
        r1.update(proposal_id=proposal_id, version=1, inst_id=inst.inst_id, round=1, rerate_of=None)
        messages.append(
            message(
                spec,
                "review1",
                AGENT,
                "research_report",
                r1["strongest_objection"],
                r1,
                at - timedelta(minutes=1),
                reply_to=uid(case, "msg", "proposal"),
            )
        )
        if spec.rerate in ("stored", "to_rerate"):
            r2 = dict(r1, round=2, rerate_of=uid(case, "msg", "review1"))
            messages.append(
                message(
                    spec,
                    "review2",
                    AGENT,
                    "research_report",
                    "Re-rate.",
                    r2,
                    at - timedelta(seconds=30),
                    reply_to=uid(case, "msg", "review1"),
                )
            )
    data["warroom_messages"] = messages
    if spec.events:
        data["economic_events"] = [
            {
                "id": uid(case, "econ", name),
                "source": "forexfactory",
                "external_id": f"eval-{case}-{name}"[:191],
                "country": "US",
                "name": name,
                "importance": imp,
                "scheduled_at": iso(at + timedelta(minutes=minutes)),
                "created_at": iso(at - timedelta(days=2)),
                "updated_at": iso(at - timedelta(days=2)),
            }
            for name, imp, minutes in spec.events
        ]
    if spec.earnings_in_days is not None:
        data["earnings_events"] = [
            {
                "id": uid(case, "earnings"),
                "symbol": inst.base,
                "date": (at + timedelta(days=spec.earnings_in_days)).date().isoformat(),
                "time": "amc",
                "created_at": iso(at - timedelta(days=2)),
                "updated_at": iso(at - timedelta(days=2)),
            }
        ]
    return data


REVIEW_PROPS = ("valid_schema", "checklist_complete", "rating_follows_guide", "no_invented_numbers")


def properties(spec: Spec, rating: int) -> dict[str, Any]:
    if spec.rerate in ("stored", "to_rerate"):
        return {"rerated_once": {"expected": 0}}
    props: dict[str, Any] = {
        "valid_schema": {"model": "atlas_shared.devils_review:DevilsReview"},
        "checklist_complete": {},
        "rating_follows_guide": {"expected": rating},
        "no_invented_numbers": {},
    }
    if spec.category == "event":
        props["item_flagged"] = {
            "item": 1,
            "min_severity": "serious",
            "patterns": [spec.events[0][0] if spec.events else "earnings"],
            "precomputed": "events",
        }
    elif spec.category == "noise":
        props["item_flagged"] = {
            "item": 4,
            "min_severity": "serious",
            "patterns": [r"0\.8 x the median 4H range"],
            "precomputed": "stop_noise",
        }
    elif spec.category == "clean":
        props["no_false_alarm"] = {"max_rating": 2}
    elif spec.category == "rerate":
        props["rerated_once"] = {"expected": 1, "rerate_of": uid(spec.case_id, "msg", "review1")}
    return props


REPLY = "Moved the plan: the entry now waits for the event to pass and the size is halved. Stop unchanged."


def build(spec: Spec) -> None:
    world = World(spec)
    folder = HERE / spec.case_id
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "data").mkdir(parents=True)
    for table, rows in snapshot(spec, world).items():
        (folder / "data" / f"{table}.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    proposal_id = uid(spec.case_id, "proposal")
    if spec.rerate:
        review1 = uid(spec.case_id, "msg", "review1")
        target = uid(spec.case_id, "msg", "review2") if spec.rerate == "to_rerate" else review1
        count = {"once": 1, "twice": 2, "thrice": 3, "stored": 1, "to_rerate": 1}[spec.rerate]
        inputs: dict[str, Any] = {
            "mode": "rerate",
            "proposal_id": proposal_id,
            "replies": [{"reply_to": target, "content_md": REPLY} for _ in range(count)],
        }
        card = review_card(
            spec,
            world,
            spec.rerate_problems if spec.rerate_problems is not None else spec.problems,
            spec.rerate_vote or spec.vote,
            spec.fix,
            spec.rerate_fix_applied,
        )
        prompt = f"Re-rate {world.inst.inst_id} proposal v1 after the Strategist's reply"
    else:
        inputs = {
            "mode": "review",
            "proposal_id": proposal_id,
            "message_id": uid(spec.case_id, "msg", "proposal"),
        }
        card = review_card(spec, world, spec.problems, spec.vote, spec.fix, spec.fix_applied)
        prompt = f"Review {world.inst.inst_id} proposal v1 against the 10-item checklist"
    steps = [{"text": answer(card)}, {"result": {"tokens_in": 6400, "tokens_out": 720, "turns": 1}}]
    doc = {
        "agent": AGENT,
        "replay_as_of": iso(spec.at),
        "env": ENV,
        "description": (
            f"[{spec.category}] {world.inst.inst_id} {spec.side}: "
            f"risk {card['risk_rating']}, vote {card['vote']}"
        ),
        "prompt": prompt,
        "properties": properties(spec, card["risk_rating"]),
        "inputs": inputs,
        "fake": {"steps": steps},
    }
    header = (
        f"# ATL-167 category: {spec.category}. Generated by build_cases.py; the snapshot is the world "
        "the Devil's Advocate reviews at replay_as_of.\n"
    )
    (folder / "case.yaml").write_text(
        header + yaml.safe_dump(doc, sort_keys=False, width=110), encoding="utf-8"
    )


WAIT = "Wait until after {event}, then re-enter at the zone"
WIDEN = "Move the stop beyond 1.0 x the median 4H range and cut the size to keep 1R"

SPECS: list[Spec] = [
    # items_: all ten answered, minor problems on different items (rating 2, take).
    Spec("items_btc_minor_overconfidence", "items", "btc", day=1, problems={"overconfidence": "minor"}),
    Spec(
        "items_eth_minor_history_crowding",
        "items",
        "eth",
        day=2,
        problems={"history": "minor", "crowding": "minor"},
    ),
    Spec("items_tsla_minor_liquidity", "items", "tsla", day=3, problems={"liquidity": "minor"}),
    Spec(
        "items_sol_short_minor_priced_in", "items", "sol", side="sell", day=4, problems={"priced_in": "minor"}
    ),
    Spec(
        "items_xag_minor_data_quality",
        "items",
        "xag",
        day=5,
        problems={"data_quality": "minor", "overconfidence": "minor"},
    ),
    # guide_: ratings 1-5 follow the guide.
    Spec("guide_link_rating1", "guide", "link", day=6),
    Spec("guide_avax_rating2", "guide", "avax", day=7, problems={"crowding": "minor"}),
    Spec(
        "guide_nvda_rating3",
        "guide",
        "nvda",
        day=8,
        problems={"overconfidence": "serious"},
        vote="change",
        fix="Cut the probability to the analog base rate and halve the size",
        p="0.68",
    ),
    Spec(
        "guide_eth_rating4",
        "guide",
        "eth",
        day=9,
        noise=True,
        problems={"noise": "serious", "history": "serious"},
        vote="skip",
        confidence="0.7",
    ),
    Spec(
        "guide_xau_rating5",
        "guide",
        "xau",
        day=10,
        noise=True,
        events=[("US CPI", 5, 180)],
        problems={"event": "loss_gt_1r", "noise": "serious"},
        vote="skip",
        confidence="0.8",
        strongest=(
            "US CPI lands 180 minutes into the window with the stop inside 4H noise: a gap through "
            "the stop is likely."
        ),
    ),
    # event_: an event inside the window is flagged (rating 3, change with the wait fix).
    Spec(
        "event_xau_us_cpi",
        "event",
        "xau",
        day=11,
        events=[("US CPI", 5, 180)],
        problems={"event": "serious"},
        vote="change",
        fix=WAIT.format(event="US CPI"),
    ),
    Spec(
        "event_btc_fomc",
        "event",
        "btc",
        day=12,
        events=[("FOMC Rate Decision", 5, 600)],
        problems={"event": "serious"},
        vote="change",
        fix=WAIT.format(event="the FOMC decision"),
    ),
    Spec(
        "event_spy_nfp",
        "event",
        "spy",
        day=13,
        events=[("Non-Farm Payrolls", 5, 60)],
        problems={"event": "serious"},
        vote="change",
        fix=WAIT.format(event="Non-Farm Payrolls"),
    ),
    Spec(
        "event_eth_short_ppi",
        "event",
        "eth",
        side="sell",
        day=14,
        events=[("US PPI", 4, 420)],
        problems={"event": "serious"},
        vote="change",
        fix=WAIT.format(event="US PPI"),
    ),
    Spec(
        "event_tsla_earnings",
        "event",
        "tsla",
        day=15,
        earnings_in_days=0,
        problems={"earnings": "serious"},
        vote="change",
        fix="Wait until after TSLA earnings",
    ),
    # noise_: a stop inside 0.8 x the median 4H range is flagged (rating 3, change with the widen fix).
    Spec(
        "noise_btc_tight_stop",
        "noise",
        "btc",
        day=16,
        noise=True,
        problems={"noise": "serious"},
        vote="change",
        fix=WIDEN,
    ),
    Spec(
        "noise_xag_tight_stop",
        "noise",
        "xag",
        day=17,
        noise=True,
        problems={"noise": "serious"},
        vote="change",
        fix=WIDEN,
    ),
    Spec(
        "noise_sol_short_tight_stop",
        "noise",
        "sol",
        side="sell",
        day=18,
        noise=True,
        problems={"noise": "serious"},
        vote="change",
        fix=WIDEN,
    ),
    Spec(
        "noise_nvda_tight_stop",
        "noise",
        "nvda",
        day=19,
        noise=True,
        problems={"noise": "serious"},
        vote="change",
        fix=WIDEN,
    ),
    Spec(
        "noise_link_tight_stop",
        "noise",
        "link",
        day=20,
        noise=True,
        problems={"noise": "serious"},
        vote="change",
        fix=WIDEN,
    ),
    # clean_: false-alarm cases; the Devil's Advocate takes a clean proposal.
    Spec("clean_btc_long", "clean", "btc", day=21, confidence="0.8"),
    Spec("clean_eth_short", "clean", "eth", side="sell", day=22),
    Spec("clean_xau_long", "clean", "xau", day=23, problems={"crowding": "minor"}),
    Spec("clean_spy_long", "clean", "spy", day=24, confidence="0.7"),
    Spec("clean_avax_short", "clean", "avax", side="sell", day=25, problems={"overconfidence": "minor"}),
    Spec("clean_xag_long", "clean", "xag", day=26),
    # rerate_: one re-rate per reviewed version.
    Spec(
        "rerate_xau_once_fix_applied",
        "rerate",
        "xau",
        day=27,
        events=[("US CPI", 5, 180)],
        problems={"event": "serious"},
        vote="change",
        fix=WAIT.format(event="US CPI"),
        rerate="once",
        rerate_vote="take",
        rerate_fix_applied=True,
    ),
    Spec(
        "rerate_btc_second_reply_ignored",
        "rerate",
        "btc",
        day=28,
        noise=True,
        problems={"noise": "serious"},
        vote="change",
        fix=WIDEN,
        rerate="twice",
    ),
    Spec(
        "rerate_eth_third_reply_ignored",
        "rerate",
        "eth",
        day=29,
        problems={"overconfidence": "serious"},
        vote="change",
        fix="Cut the probability to the analog base rate",
        p="0.7",
        rerate="thrice",
        rerate_problems={"overconfidence": "minor"},
        rerate_vote="take",
        rerate_fix_applied=True,
    ),
    Spec(
        "rerate_sol_already_stored",
        "rerate",
        "sol",
        day=30,
        problems={"overconfidence": "serious"},
        vote="change",
        fix="Cut the probability",
        p="0.7",
        rerate="stored",
    ),
    Spec(
        "rerate_tsla_reply_to_rerate",
        "rerate",
        "tsla",
        day=30,
        hour=15,
        problems={"liquidity": "minor"},
        rerate="to_rerate",
    ),
]


def main() -> None:
    for spec in SPECS:
        build(spec)
    print(f"{len(SPECS)} {AGENT} cases written under {HERE}")


if __name__ == "__main__":
    main()
