"""The ``no_invented_numbers`` checker behind one small interface.

The harness only needs ``guard(text, card, tool_outputs) -> GuardReport``. The real guard is
``atlas_agents.numguard.check`` (ATL-91, brief §7.11); :func:`resolve` uses it when it is installed
and otherwise falls back to :func:`stub_guard`, a deliberately simple matcher (numbers in the text
and the card must equal, within 0.5 % or the rounding of the digits written, a number in that
turn's tool outputs; percentages may also equal 100 x a tool fraction).
``agents.evals.guard`` (auto | numguard | stub) picks one.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from atlas_shared.defaults import DEFAULTS


@dataclass(frozen=True)
class GuardReport:
    """``unmatched``: the to_meta() items of numbers with no tool source; ``checked``: numbers seen."""

    guard: str
    unmatched: list[dict[str, Any]] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.unmatched


NumberGuard = Callable[[str, Mapping[str, Any] | None, Sequence[Mapping[str, Any]]], GuardReport]

# -- the stub ---------------------------------------------------------------------------------------

_IGNORE: Final = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?"  # ISO dates and timestamps
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"  # clock times
    r"|\b[A-Z0-9]+(?:-[A-Z0-9]+)+\b"  # instrument ids
    r"|\b\d+(?:m|h|H|d|D|W|M)\b"  # bar / horizon labels
    r"|\b[A-Za-z]+\(\d+(?:,\s*\d+)*\)"  # indicator parameters, ATR(14)
    r"|\b[A-Za-z]+\d+\b"  # letter-glued, p10
    r"|^\s*\d+[.)]\s",  # list markers
    re.MULTILINE,
)
_NUMBER: Final = re.compile(r"(?<![\w.])[-+\u2212]?\d[\d,]*(?:\.\d+)?(?:\s?(%|bps?))?")
_MINUS: Final = "\u2212"  # U+2212, the typographic minus agents sometimes write
_REL: Final = 0.005
_ABS: Final = 1e-4


def _numbers_in(text: str) -> Iterator[tuple[str, float, str]]:
    cleaned = _IGNORE.sub(lambda m: " " * len(m.group(0)), text)
    for match in _NUMBER.finditer(cleaned):
        raw = match.group(0).strip()
        digits = raw.replace(",", "").replace(_MINUS, "-").removesuffix("%").removesuffix("bps")
        digits = digits.removesuffix("bp").strip()
        try:
            value = float(digits)
        except ValueError:
            continue
        if 1970 <= value <= 2100 and "." not in digits and not match.group(1):
            continue  # a standalone year
        yield raw, value, ("percent" if match.group(1) == "%" else "bps" if match.group(1) else "number")


def _leaves(node: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield from _leaves(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _leaves(value, f"{path}[{i}]")
    else:
        yield path, node


def _tool_values(outputs: Sequence[Mapping[str, Any]]) -> list[float]:
    values: list[float] = []
    for output in outputs:
        for _path, leaf in _leaves(output):
            if isinstance(leaf, bool):
                continue
            if isinstance(leaf, (int, float)):
                values.append(float(leaf))
            elif isinstance(leaf, str):
                try:
                    values.append(float(leaf))
                except ValueError:
                    values.extend(v for _r, v, _k in _numbers_in(leaf))
    return values


def _half_unit(raw: str) -> float:
    """Half the last written digit: "6.9" may be 6.944 rounded."""
    digits = re.sub(r"[^\d.]", "", raw)
    decimals = len(digits.partition(".")[2])
    return 0.5 * 10.0**-decimals if "." in digits else 0.0


def _matches(value: float, kind: str, raw: str, pool: Sequence[float]) -> bool:
    half = _half_unit(raw)
    candidates = [(value, half)]
    if kind == "percent":
        candidates.append((value / 100, half / 100))
    elif kind == "bps":
        candidates.append((value / 10_000, half / 10_000))
    for c, slack in candidates:
        for v in pool:
            if abs(abs(c) - abs(v)) <= max(_REL * abs(v), _ABS, slack):
                return True
    return False


def stub_guard(
    text: str, card: Mapping[str, Any] | None, tool_outputs: Sequence[Mapping[str, Any]]
) -> GuardReport:
    pool = _tool_values(tool_outputs)
    stated: list[dict[str, Any]] = [
        {"raw": raw, "value": value, "kind": kind, "location": "text", "span": None, "path": None}
        for raw, value, kind in _numbers_in(text)
    ]
    for path, leaf in _leaves(card or {}):
        if isinstance(leaf, (int, float)) and not isinstance(leaf, bool):
            stated.append(
                {
                    "raw": json.dumps(leaf),
                    "value": float(leaf),
                    "kind": "number",
                    "location": "card",
                    "span": None,
                    "path": path,
                }
            )
        elif isinstance(leaf, str):
            stated.extend(
                {"raw": raw, "value": value, "kind": kind, "location": "card", "span": None, "path": path}
                for raw, value, kind in _numbers_in(leaf)
            )
    unmatched = [s for s in stated if not _matches(s["value"], s["kind"], str(s["raw"]), pool)]
    return GuardReport(guard="stub", unmatched=unmatched, checked=len(stated))


# -- the real guard, when it is installed ------------------------------------------------------------


def _numguard() -> NumberGuard | None:
    try:
        module = importlib.import_module("atlas_agents.numguard")
    except ModuleNotFoundError:
        return None
    check: Callable[..., Any] = module.check

    def guard(
        text: str, card: Mapping[str, Any] | None, tool_outputs: Sequence[Mapping[str, Any]]
    ) -> GuardReport:
        meta: dict[str, Any] = check(text, card, tool_outputs).to_meta()
        return GuardReport(
            guard="numguard", unmatched=list(meta["unmatched_numbers"]), checked=int(meta["checked"])
        )

    return guard


def resolve(name: str | None = None) -> NumberGuard:
    """``auto`` (default from agents.evals.guard), ``numguard`` or ``stub``."""
    choice = name or str(DEFAULTS["agents"]["evals"]["guard"])
    if choice == "stub":
        return stub_guard
    real = _numguard()
    if real is not None:
        return real
    if choice == "numguard":
        raise RuntimeError("agents.evals.guard is numguard but atlas_agents.numguard is not installed")
    return stub_guard
