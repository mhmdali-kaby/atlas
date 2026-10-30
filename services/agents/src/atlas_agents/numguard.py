"""Numeric hallucination guard (brief §7.11, ATL-91).

After an agent turn, every number the agent stated — in the message text and in its card — is
checked against the numeric leaves of that turn's tool outputs (the ``{as_of, source, data}``
envelopes of ATL-84). A stated number matches when it is, within tolerance:

* a tool number (``tool_value``);
* for a percentage, 100 x a tool number (``percent_of_ratio``: "4.1%" from 0.04132);
* for bps, 10,000 x a tool number (``bps_of_ratio``);
* the difference or ratio of two tool numbers (``difference`` / ``ratio``).

Tolerances live in ``agents.numguard`` (config/defaults.yaml): ``rel_tolerance`` of the tool value,
an ``abs_floor`` for small values, and, when ``rounding_aware``, half a unit of the last digit the
agent wrote ("4.1%" covers 4.05-4.15; "1.2M" covers 1.15M-1.25M). A positive stated number matches
a tool value by magnitude ("fell 2.3%" against -0.023); a negative one must match the sign too.

Ignored, never checked: dates, times, ISO timestamps, years (1970-2100 standing alone), list
markers ("1." / "2)" at the start of a line), instrument ids (BTC-USDT-SWAP), bar and horizon
labels (1H, 4H, 24h, 15m) and indicator parameters (ATR(14), MACD(12,26,9)).

The guard is pure: no I/O. Comparisons use floats — this is a tolerance check, never money maths.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from atlas_shared.defaults import DEFAULTS

Kind = Literal["number", "percent", "bps"]
Location = Literal["text", "card"]

_MONTH: Final = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
_UNIT_AFTER: Final = r"(?:\s?(?:%|bps?\b|ATR\b)|[kKMBR]\b|[x\u00d7])"

# Spans of text that contain digits but are not numbers to check.
_IGNORE: Final[tuple[re.Pattern[str], ...]] = (
    # ISO dates and timestamps: 2026-10-29, 2026-10-29T07:30:00.123+00:00, 2026-10-29 07:30Z
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"),
    # Slash dates: 29/10/2026, 10/29, 2026/10/29
    re.compile(r"(?<![\w.])\d{1,4}/\d{1,2}(?:/\d{1,4})?(?![\w.])"),
    # Clock times: 07:30, 7:30:15, 7:30 pm
    re.compile(r"(?<![\w.])\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\s?[AaPp]\.?[Mm]\.?)?"),
    # Day + month (+ year): 29 Oct 2026, 1st November, 29 Oct, 2026
    re.compile(rf"(?<![\w.])\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:,?\s+\d{{4}})?(?![\w])"),
    # Month + day / year: Oct 29, Oct 29th 2026, October 2026
    re.compile(rf"\b{_MONTH}\s+\d{{1,4}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?(?![\w])"),
    # Years standing alone (not money, not a percentage, not part of a larger number)
    re.compile(rf"(?<![\w.,$\-\u2212+])(?:19[7-9]\d|20\d\d|2100)(?![\w.]|,\d|{_UNIT_AFTER})"),
    # List markers at the start of a line: "1. ", "2) ", "- 3. "
    re.compile(r"(?m)^[ \t>]*(?:[-*+][ \t]+)?\d{1,3}[.)](?=[ \t])"),
    # Instrument ids: BTC-USDT-SWAP, BTC-USD-251226, BTC-USD-251226-60000-C, XAU-USDT
    re.compile(r"\b(?=[A-Z0-9]*[A-Z])[A-Z0-9]+(?:-[A-Z0-9]+)+\b"),
    # Bar and horizon labels: 1m, 15m, 1H, 4H, 1D, 1W, 24h, 7d (not $15m, which is money)
    re.compile(r"(?<![\w.$,])\d+(?:[mhHdDW])\b"),
    # Indicator parameters: ATR(14), MACD(12,26,9), GARCH(1,1), Bollinger(20, 2)
    re.compile(r"(?<=[A-Za-z])\(\s*\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)*\s*\)"),
)

_NUMBER: Final = re.compile(
    r"(?<![\w.,])"
    r"(?P<sign>[-\u2212+](?=\$?\.?\d))?"
    r"(?P<cur>\$)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?P<suf>[kKMB](?![A-Za-z])|\s?(?:bn|mn)(?![A-Za-z]))?"
    r"(?P<unit>\s?%|\s?bps?(?![A-Za-z])|R(?![A-Za-z])|\s?[x\u00d7](?:\s?ATR)?(?![A-Za-z])|\s?ATR(?![A-Za-z]))?"
    r"(?:(?<=\d)(?![A-Za-z_\d])|(?<!\d))"
)

_SUFFIX: Final[dict[str, Decimal]] = {
    "k": Decimal(1_000),
    "K": Decimal(1_000),
    "M": Decimal(1_000_000),
    "mn": Decimal(1_000_000),
    "B": Decimal(1_000_000_000),
    "bn": Decimal(1_000_000_000),
}

_DECIMAL_STRING: Final = re.compile(r"^\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*$")


@dataclass(frozen=True)
class NumGuardConfig:
    rel_tolerance: float
    abs_floor: float
    rounding_aware: bool
    derive_pairs: bool
    max_pair_leaves: int

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> NumGuardConfig:
        return cls(
            rel_tolerance=float(Decimal(str(config["rel_tolerance"]))),
            abs_floor=float(Decimal(str(config["abs_floor"]))),
            rounding_aware=bool(config["rounding_aware"]),
            derive_pairs=bool(config["derive_pairs"]),
            max_pair_leaves=int(config["max_pair_leaves"]),
        )


def load_config(defaults: Mapping[str, Any] = DEFAULTS) -> NumGuardConfig:
    """``agents.numguard`` from the generated defaults (config/defaults.yaml)."""
    return NumGuardConfig.from_mapping(defaults["agents"]["numguard"])


@dataclass(frozen=True)
class StatedNumber:
    """One number an agent stated.

    ``value`` is in the units written: "4.1%" is 4.1 with kind ``percent``; "$1.2M" is 1,200,000.
    ``span`` is the character range in the message text, or in the card string at ``path``; a
    numeric card leaf has ``span`` None. ``half_unit`` is half the last written digit's place.
    """

    raw: str
    value: float
    kind: Kind
    location: Location
    span: tuple[int, int] | None
    path: str | None
    half_unit: float
    matched_by: str | None = None
    tool_value: float | None = None

    def meta(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "value": self.value,
            "kind": self.kind,
            "location": self.location,
            "span": list(self.span) if self.span is not None else None,
            "path": self.path,
        }


@dataclass(frozen=True)
class GuardResult:
    matched: tuple[StatedNumber, ...]
    unmatched: tuple[StatedNumber, ...]

    @property
    def checked(self) -> int:
        return len(self.matched) + len(self.unmatched)

    @property
    def ok(self) -> bool:
        return not self.unmatched

    def to_meta(self) -> dict[str, Any]:
        """The shape attached to a War Room message's card/meta."""
        return {
            "unmatched_numbers": [n.meta() for n in self.unmatched],
            "checked": self.checked,
        }


# --- extraction -------------------------------------------------------------------------------


def _ignored_spans(text: str) -> list[tuple[int, int]]:
    spans = [m.span() for pattern in _IGNORE for m in pattern.finditer(text)]
    return sorted(spans)


def _overlaps(span: tuple[int, int], ignored: Sequence[tuple[int, int]]) -> bool:
    start, end = span
    return any(s < end and start < e for s, e in ignored)


def _decimals(digits: str) -> int:
    return len(digits.split(".", 1)[1]) if "." in digits else 0


def extract_numbers(text: str, *, location: Location = "text", path: str | None = None) -> list[StatedNumber]:
    """Every checkable number in ``text``, in order, with ignored spans removed."""
    ignored = _ignored_spans(text)
    found: list[StatedNumber] = []
    for m in _NUMBER.finditer(text):
        if _overlaps(m.span("num"), ignored):
            continue
        digits = m["num"].replace(",", "")
        try:
            value = Decimal(digits)
        except InvalidOperation:  # pragma: no cover - the regex only admits digits
            continue
        multiplier = _SUFFIX.get((m["suf"] or "").strip(), Decimal(1))
        value *= multiplier
        if m["sign"] in ("-", "\u2212"):
            value = -value
        unit = (m["unit"] or "").strip()
        kind: Kind = "percent" if unit == "%" else "bps" if unit.startswith("bp") else "number"
        half = Decimal("0.5") * Decimal(10) ** -_decimals(digits) * multiplier
        found.append(
            StatedNumber(
                raw=m.group(0).strip(),
                value=float(value),
                kind=kind,
                location=location,
                span=m.span(),
                path=path,
                half_unit=float(half),
            )
        )
    return found


def _walk(node: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(node, Mapping):
        for key, child in node.items():
            yield from _walk(child, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list | tuple):
        for i, child in enumerate(node):
            yield from _walk(child, f"{path}[{i}]")
    else:
        yield path, node


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def _card_numbers(card: Mapping[str, Any]) -> list[StatedNumber]:
    found: list[StatedNumber] = []
    for path, leaf in _walk(card):
        if _is_number(leaf):
            exact = Decimal(str(leaf))
            if not exact.is_finite():
                continue
            exponent = exact.as_tuple().exponent
            places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
            found.append(
                StatedNumber(
                    raw=str(leaf),
                    value=float(exact),
                    kind="number",
                    location="card",
                    span=None,
                    path=path,
                    half_unit=float(Decimal("0.5") * Decimal(10) ** -places),
                )
            )
        elif isinstance(leaf, str):
            found.extend(extract_numbers(leaf, location="card", path=path))
    return found


def tool_numbers(tool_outputs: Sequence[Mapping[str, Any]]) -> list[float]:
    """Distinct numeric leaves of the tool outputs, in first-seen order.

    Numbers, decimal strings ("0.0005") and numbers written inside string leaves (a news headline's
    "3.2%", a note's "n=34") all count; timestamps and ids inside strings are ignored as in text.
    """
    seen: dict[float, None] = {}
    for output in tool_outputs:
        for _, leaf in _walk(output):
            if _is_number(leaf):
                value = float(leaf)
                if value == value and abs(value) != float("inf"):
                    seen.setdefault(value, None)
            elif isinstance(leaf, str):
                if _DECIMAL_STRING.match(leaf):
                    seen.setdefault(float(Decimal(leaf.strip())), None)
                else:
                    for n in extract_numbers(leaf):
                        seen.setdefault(n.value, None)
    return list(seen)


# --- matching ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Pool:
    name: str
    signed: list[float]
    magnitude: list[float]

    @classmethod
    def of(cls, name: str, values: Sequence[float]) -> _Pool:
        return cls(name, sorted(values), sorted(abs(v) for v in values))

    def nearest(self, target: float, window: float, by_magnitude: bool) -> Iterator[float]:
        values = self.magnitude if by_magnitude else self.signed
        lo = bisect.bisect_left(values, target - window)
        hi = bisect.bisect_right(values, target + window)
        return iter(values[lo:hi])


def _pools(leaves: Sequence[float], config: NumGuardConfig) -> dict[str, _Pool]:
    pools = {
        "tool_value": _Pool.of("tool_value", leaves),
        "percent_of_ratio": _Pool.of("percent_of_ratio", [v * 100 for v in leaves]),
        "bps_of_ratio": _Pool.of("bps_of_ratio", [v * 10_000 for v in leaves]),
    }
    if config.derive_pairs:
        paired = leaves[: config.max_pair_leaves]
        diffs: list[float] = []
        ratios: list[float] = []
        for i, a in enumerate(paired):
            for j, b in enumerate(paired):
                if i == j:
                    continue
                diffs.append(a - b)
                if b != 0:
                    ratios.append(a / b)
        pools["difference"] = _Pool.of("difference", diffs)
        pools["ratio"] = _Pool.of("ratio", ratios)
    return pools


_ORDER: Final[dict[Kind, tuple[str, ...]]] = {
    "number": ("tool_value", "difference", "ratio"),
    "percent": ("tool_value", "percent_of_ratio", "difference", "ratio"),
    "bps": ("tool_value", "bps_of_ratio", "difference", "ratio"),
}


def _match(
    number: StatedNumber, pools: Mapping[str, _Pool], config: NumGuardConfig
) -> tuple[str, float] | None:
    by_magnitude = number.value >= 0
    target = abs(number.value) if by_magnitude else number.value
    floor = max(config.abs_floor, number.half_unit if config.rounding_aware else 0.0)
    rel = config.rel_tolerance
    window = max(floor, rel * abs(target) / (1 - rel) if rel < 1 else abs(target)) * (1 + 1e-9)
    for name in _ORDER[number.kind]:
        pool = pools.get(name)
        if pool is None:
            continue
        for candidate in pool.nearest(target, window, by_magnitude):
            tolerance = max(floor, rel * abs(candidate))
            if abs(target - candidate) <= tolerance * (1 + 1e-9) + 1e-12:
                return name, candidate
    return None


def check(
    text: str,
    card: Mapping[str, Any] | None,
    tool_outputs: Sequence[Mapping[str, Any]],
    *,
    config: NumGuardConfig | Mapping[str, Any] | None = None,
) -> GuardResult:
    """Check every number in ``text`` and ``card`` against this turn's ``tool_outputs``.

    ``config`` defaults to ``agents.numguard`` from config/defaults.yaml.
    """
    if config is None:
        settings = load_config()
    elif isinstance(config, NumGuardConfig):
        settings = config
    else:
        settings = NumGuardConfig.from_mapping(config)

    stated = extract_numbers(text) + (_card_numbers(card) if card else [])
    pools = _pools(tool_numbers(tool_outputs), settings)
    matched: list[StatedNumber] = []
    unmatched: list[StatedNumber] = []
    for number in stated:
        hit = _match(number, pools, settings)
        if hit is None:
            unmatched.append(number)
        else:
            matched.append(
                StatedNumber(
                    raw=number.raw,
                    value=number.value,
                    kind=number.kind,
                    location=number.location,
                    span=number.span,
                    path=number.path,
                    half_unit=number.half_unit,
                    matched_by=hit[0],
                    tool_value=hit[1],
                )
            )
    return GuardResult(matched=tuple(matched), unmatched=tuple(unmatched))
