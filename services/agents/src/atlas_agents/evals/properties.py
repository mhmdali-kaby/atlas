"""What an agent's answer to a case is scored on (brief §7.11).

A :class:`Transcript` is what one run produced. Each property is a function
``(transcript, params, ctx) -> (passed, detail)``; ``case.yaml`` names them under ``properties``:

* ``valid_schema: {model: HistoryVerdict}`` — the card validates against that pydantic model (a name
  in :data:`MODELS`, or ``package.module:Class``).
* ``no_invented_numbers: {guard: auto|numguard|stub}`` — the number guard finds no number in the text
  or card that is missing from the run's tool outputs.
* ``verdict_in: {values: [...], field: verdict}`` — the card's verdict is one of ``values``.
* ``no_trade: {expected: true}`` — no ``warroom.submit_proposal`` call and no card that is a trade
  (``side`` buy/sell or ``direction`` long/short). ``expected: false`` asserts a trade instead.
* ``mentions_n: {min: 20, max: .., equals: ..}`` — the answer states its sample size: the card's
  ``n``, else ``n = 123`` / ``123 cases`` in the text, within the optional bounds.
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
    report = check(t.text, t.card, t.tool_outputs)
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


PROPERTIES: Final[dict[str, Property]] = {
    "valid_schema": valid_schema,
    "no_invented_numbers": no_invented_numbers,
    "verdict_in": verdict_in,
    "no_trade": no_trade,
    "mentions_n": mentions_n,
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
