"""What an agent's answer to a case is scored on (brief §7.11).

A :class:`Transcript` is what one run produced. Each property is a function
``(transcript, params, ctx) -> (passed, detail)``; ``case.yaml`` names them under ``properties``:

* ``valid_schema: {model: HistoryVerdict}`` — the card validates against that pydantic model (a name
  in :data:`MODELS`, or ``package.module:Class``).
* ``no_invented_numbers: {guard: auto|numguard|stub, from_prompt: false}`` — the number guard finds no
  number in the text or card that is missing from the run's tool outputs. ``from_prompt: true`` also
  counts the case prompt as a source, as production does for the asker's own text (the mention) and
  for the 07:30 regime table handed to the Historian in its prompt (ATL-93).
* ``verdict_in: {values: [...], field: verdict}`` — the card's verdict is one of ``values``.
* ``no_trade: {expected: true}`` — no ``warroom.submit_proposal`` call and no card that is a trade
  (``side`` buy/sell or ``direction`` long/short). ``expected: false`` asserts a trade instead.
* ``mentions_n: {min: 20, max: .., equals: ..}`` — the answer states its sample size: the card's
  ``n``, else ``n = 123`` / ``123 cases`` in the text, within the optional bounds.

Watchlist cards (Market Scout, ATL-112); ``verdict_in: {field: status, values: [...]}`` checks the
card status:

* ``valid_watchlist: {}`` — a ``watchlist`` card whose every pick is a valid ``WatchlistPick``, ranked
  1..n with unique instruments.
* ``rejected_listed: {count: 3}`` — exactly ``count`` rejected candidates, each with a reason and none
  of them picked.
* ``picks_count: {min: 0, max: 5}`` — the number of picks.
* ``no_trade_day: {max_picks: 1, max_conviction: 0.5}`` — a poor day: at most ``max_picks`` picks, none
  above ``max_conviction``.
* ``max_conviction: {max: 0.5}`` — no pick above it.
* ``picked: {inst_ids: [...]}`` / ``not_picked: {inst_ids: [...]}`` — on / off the final list.
* ``max_correlated: {groups: [[a, b, c]], max: 2}`` — at most ``max`` picks from each group.
* ``asset_classes: {min: 2}`` — picks span at least ``min`` asset classes.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from atlas_agents import schemas
from atlas_agents.evals import guard as guards

POST_TOOLS: Final = ("mcp__warroom__post_message", "mcp__warroom__reply")
PROPOSAL_TOOL: Final = "mcp__warroom__submit_proposal"

MODELS: Final[dict[str, type[BaseModel]]] = {
    "HistoryVerdict": schemas.HistoryVerdict,
    "RegimeTable": schemas.RegimeTable,
}


@dataclass
class Transcript:
    """One run's output, as the properties see it."""

    status: str
    text: str = ""
    card: dict[str, Any] | None = None
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    prompt: str = ""


@dataclass(frozen=True)
class PropertyResult:
    name: str
    passed: bool
    detail: str

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


Outcome = tuple[bool, str]
Property = Callable[[Transcript, Mapping[str, Any]], Outcome]

_JSON_FENCE: Final = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_N_TEXT: Final = re.compile(r"\bn\s*[=:]\s*(\d[\d,]*)\b|\b(\d[\d,]*)\s+(?:cases|analogs|events|samples)\b")


def card_from(text: str, calls: list[tuple[str, dict[str, Any]]]) -> dict[str, Any] | None:
    """The last card posted with warroom.post_message/reply, else the last ```json block in the text."""
    for name, args in reversed(calls):
        if name in POST_TOOLS and isinstance(args.get("card"), dict):
            card: dict[str, Any] = args["card"]
            return card
    for block in reversed(_JSON_FENCE.findall(text)):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _model(name: str) -> type[BaseModel]:
    if name in MODELS:
        return MODELS[name]
    module, _, attr = name.partition(":")
    if not attr:
        raise LookupError(f"unknown schema model [{name}]; known: {sorted(MODELS)} or package.module:Class")
    found = getattr(importlib.import_module(module), attr)
    if not (isinstance(found, type) and issubclass(found, BaseModel)):
        raise LookupError(f"[{name}] is not a pydantic model")
    return found


def valid_schema(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    model = _model(str(params["model"]))
    if t.card is None:
        return False, "no card in the answer"
    try:
        model.model_validate(t.card)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "(root)"
        return False, f"{model.__name__}: {exc.error_count()} errors, first {where}: {first['msg']}"
    return True, f"card is a valid {model.__name__}"


def no_invented_numbers(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    check = guards.resolve(params.get("guard"))
    outputs = list(t.tool_outputs)
    if params.get("from_prompt"):
        outputs.append({"as_of": None, "source": "case.prompt", "data": t.prompt})
    report = check(t.text, t.card, outputs)
    if report.ok:
        return True, f"{report.guard}: {report.checked} numbers, all traced to tool outputs"
    raws = ", ".join(str(u.get("raw")) for u in report.unmatched[:10])
    return False, f"{report.guard}: {len(report.unmatched)} of {report.checked} numbers unmatched: {raws}"


def verdict_in(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    key = str(params.get("field", "verdict"))
    allowed = [str(v) for v in params["values"]]
    value = (t.card or {}).get(key)
    if value in allowed:
        return True, f"{key} = {value}"
    return False, f"{key} = {value!r}, expected one of {allowed}"


def _is_trade(t: Transcript) -> str | None:
    if any(name == PROPOSAL_TOOL for name, _ in t.tool_calls):
        return "called warroom.submit_proposal"
    card = t.card or {}
    if card.get("side") in ("buy", "sell"):
        return f"card side = {card['side']}"
    if card.get("direction") in ("long", "short"):
        return f"card direction = {card['direction']}"
    return None


def no_trade(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    expected = bool(params.get("expected", True))
    trade = _is_trade(t)
    if expected:
        return (trade is None), ("no trade proposed" if trade is None else f"proposed a trade: {trade}")
    return (trade is not None), (f"proposed a trade: {trade}" if trade else "expected a trade, none proposed")


def _stated_n(t: Transcript) -> int | None:
    n = (t.card or {}).get("n")
    if isinstance(n, int) and not isinstance(n, bool):
        return n
    match = _N_TEXT.search(t.text)
    if match:
        return int((match.group(1) or match.group(2)).replace(",", ""))
    return None


def mentions_n(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    n = _stated_n(t)
    if n is None:
        return False, "the answer does not state its sample size n"
    if "equals" in params and n != int(params["equals"]):
        return False, f"n = {n}, expected {params['equals']}"
    if "min" in params and n < int(params["min"]):
        return False, f"n = {n} < min {params['min']}"
    if "max" in params and n > int(params["max"]):
        return False, f"n = {n} > max {params['max']}"
    return True, f"n = {n}"


# -- watchlist cards (ATL-112) ----------------------------------------------------------------------


def _picks(t: Transcript) -> list[dict[str, Any]]:
    picks = (t.card or {}).get("picks")
    if not isinstance(picks, list):
        raise ValueError("the card has no picks list")
    return [p for p in picks if isinstance(p, dict)]


def _picked_ids(t: Transcript) -> list[str]:
    return [str(p.get("inst_id")) for p in _picks(t)]


def _conviction(pick: Mapping[str, Any]) -> float:
    return float(pick.get("conviction", 0.0))


def valid_watchlist(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    card = t.card
    if card is None:
        return False, "no card in the answer"
    if card.get("type") != "watchlist":
        return False, f"card type = {card.get('type')!r}, expected 'watchlist'"
    picks = _picks(t)
    for pick in picks:
        try:
            schemas.WatchlistPick.model_validate({k: v for k, v in pick.items() if k in _PICK_FIELDS})
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"]) or "(root)"
            return (
                False,
                f"pick {pick.get('inst_id')}: {exc.error_count()} errors, first {where}: {first['msg']}",
            )
    ranks = [int(p["rank"]) for p in picks]
    if ranks != list(range(1, len(picks) + 1)):
        return False, f"ranks {ranks} are not 1..{len(picks)}"
    ids = _picked_ids(t)
    if len(set(ids)) != len(ids):
        return False, f"an instrument is picked twice: {ids}"
    return True, f"{len(picks)} valid WatchlistPick(s), status {card.get('status')}"


_PICK_FIELDS: Final = frozenset(schemas.WatchlistPick.model_fields)


def rejected_listed(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    count = int(params.get("count", 3))
    rejected = (t.card or {}).get("rejected")
    if not isinstance(rejected, list):
        return False, "the card has no rejected list"
    ids = [str(r.get("inst_id")) for r in rejected if isinstance(r, dict)]
    if len(ids) != count:
        return False, f"{len(ids)} rejected candidates listed, expected {count}"
    if any(not str(r.get("reason") or "").strip() for r in rejected if isinstance(r, dict)):
        return False, "a rejected candidate has no reason"
    both = sorted(set(ids) & set(_picked_ids(t)))
    if both:
        return False, f"picked and rejected at once: {both}"
    return True, f"{count} rejected with reasons: {', '.join(ids)}"


def picks_count(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    n = len(_picks(t))
    if "min" in params and n < int(params["min"]):
        return False, f"{n} picks < min {params['min']}"
    if "max" in params and n > int(params["max"]):
        return False, f"{n} picks > max {params['max']}"
    return True, f"{n} picks"


def max_conviction(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    limit = float(params["max"])
    over = [f"{p.get('inst_id')} {_conviction(p):g}" for p in _picks(t) if _conviction(p) > limit]
    if over:
        return False, f"conviction above {limit:g}: {', '.join(over)}"
    return True, f"every conviction <= {limit:g}"


def no_trade_day(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    most = int(params.get("max_picks", 1))
    limit = float(params.get("max_conviction", 0.5))
    picks = _picks(t)
    if len(picks) > most:
        return False, f"a no-trade day: {len(picks)} picks, expected at most {most}"
    passed, detail = max_conviction(t, {"max": limit})
    if not passed:
        return False, f"a no-trade day: {detail}"
    return True, f"{len(picks)} picks, every conviction <= {limit:g}"


def picked(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    wanted = [str(i) for i in params["inst_ids"]]
    missing = [i for i in wanted if i not in _picked_ids(t)]
    if missing:
        return False, f"not on the watchlist: {missing}"
    return True, f"on the watchlist: {wanted}"


def not_picked(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    banned = [str(i) for i in params["inst_ids"]]
    found = [i for i in banned if i in _picked_ids(t)]
    if found:
        return False, f"picked but must not be: {found}"
    return True, f"not picked: {banned}"


def max_correlated(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    most = int(params.get("max", 2))
    ids = set(_picked_ids(t))
    for group in params["groups"]:
        inside = sorted(ids & {str(i) for i in group})
        if len(inside) > most:
            return False, f"{len(inside)} picks from one correlated group: {inside} (max {most})"
    return True, f"at most {most} picks per correlated group"


def asset_classes(t: Transcript, params: Mapping[str, Any]) -> Outcome:
    classes = sorted({str(p.get("asset_class")) for p in _picks(t)})
    least = int(params.get("min", 2))
    if len(classes) < least:
        return False, f"picks span {len(classes)} asset class(es) {classes}, expected at least {least}"
    return True, f"asset classes: {classes}"


PROPERTIES: Final[dict[str, Property]] = {
    "valid_schema": valid_schema,
    "no_invented_numbers": no_invented_numbers,
    "verdict_in": verdict_in,
    "no_trade": no_trade,
    "mentions_n": mentions_n,
    "valid_watchlist": valid_watchlist,
    "rejected_listed": rejected_listed,
    "picks_count": picks_count,
    "no_trade_day": no_trade_day,
    "max_conviction": max_conviction,
    "picked": picked,
    "not_picked": not_picked,
    "max_correlated": max_correlated,
    "asset_classes": asset_classes,
}


def score(t: Transcript, wanted: Mapping[str, Mapping[str, Any]]) -> list[PropertyResult]:
    """Score every property; a property that raises, or an unknown one, is a failure, never a crash."""
    results: list[PropertyResult] = []
    for name, params in wanted.items():
        fn = PROPERTIES.get(name)
        if fn is None:
            results.append(PropertyResult(name, False, f"unknown property; known: {sorted(PROPERTIES)}"))
            continue
        if t.status != "ok":
            results.append(PropertyResult(name, False, f"run {t.status}: {t.error or 'no answer'}"))
            continue
        try:
            passed, detail = fn(t, params)
        except Exception as exc:  # a malformed parameter fails this property, not the whole run
            passed, detail = False, f"{type(exc).__name__}: {exc}"
        results.append(PropertyResult(name, passed, detail))
    return results
