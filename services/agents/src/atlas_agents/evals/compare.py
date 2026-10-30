"""A/B two eval results files on the same cases (brief §9.4): pass-rate change per case and overall."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Row:
    case_id: str
    a: float | None
    b: float | None

    @property
    def delta(self) -> float | None:
        return None if self.a is None or self.b is None else round(self.b - self.a, 2)


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def compare(a: dict[str, Any], b: dict[str, Any]) -> tuple[list[Row], Row]:
    rates_a = {c["case_id"]: float(c["pass_rate"]) for c in a["cases"]}
    rates_b = {c["case_id"]: float(c["pass_rate"]) for c in b["cases"]}
    ids = sorted(set(rates_a) | set(rates_b))
    rows = [Row(i, rates_a.get(i), rates_b.get(i)) for i in ids]
    overall = Row("OVERALL", float(a["overall"]["pass_rate"]), float(b["overall"]["pass_rate"]))
    return rows, overall


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:6.2f}%"


def _fmt_delta(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:+7.2f}"


def render(path_a: Path, path_b: Path) -> str:
    a, b = _load(path_a), _load(path_b)
    rows, overall = compare(a, b)
    width = max([len(r.case_id) for r in [*rows, overall]] + [4])
    label_a = a.get("label") or path_a.name
    label_b = b.get("label") or path_b.name
    lines = [
        f"A: {label_a} ({a.get('agent')}, {a.get('mode')})",
        f"B: {label_b} ({b.get('agent')}, {b.get('mode')})",
        f"{'case':<{width}}  {'A':>7}  {'B':>7}  {'change':>7}",
    ]
    for row in [*rows, overall]:
        lines.append(f"{row.case_id:<{width}}  {_fmt(row.a)}  {_fmt(row.b)}  {_fmt_delta(row.delta)}")
    return "\n".join(lines)
