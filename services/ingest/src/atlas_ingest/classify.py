"""Rule-based headline enrichment: event type, sentiment and importance (brief §7.2 step 4, #243).

Pure functions — no DB, no network, no LLM. The rules live in the repository's ``config/``:

* ``config/news_event_rules.yaml`` — ordered regex rules for :func:`event_type`;
* ``config/news_sentiment_lexicon.yaml`` — the lexicon for :func:`sentiment`;
* ``config/defaults.yaml`` ``news.importance`` (generated into ``atlas_shared.defaults``) — the
  weights for :func:`importance`.

The config directory is found by walking up from this file to the first directory holding
``config/news_event_rules.yaml``; ``ATLAS_CONFIG_DIR`` overrides it.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from functools import cache
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from atlas_shared.defaults import DEFAULTS

from atlas_ingest.evaluation.fixture import EVENT_TYPES, HeadlineRow

EVENT_RULES_FILE = "news_event_rules.yaml"
LEXICON_FILE = "news_sentiment_lexicon.yaml"
FALLBACK_EVENT_TYPE = "other"
_THREE_PLACES = Decimal("0.001")


# --------------------------------------------------------------------------------------- config


def config_dir() -> Path:
    override = os.environ.get("ATLAS_CONFIG_DIR")
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config"
        if (candidate / EVENT_RULES_FILE).is_file():
            return candidate
    raise FileNotFoundError(f"config/{EVENT_RULES_FILE} not found above {__file__}")


def _load_yaml(name: str) -> dict[str, Any]:
    with (config_dir() / name).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{name}: expected a mapping at the top level")
    return data


@dataclass(frozen=True)
class EventRule:
    type: str
    patterns: tuple[re.Pattern[str], ...]

    def matches(self, text: str) -> bool:
        return any(p.search(text) for p in self.patterns)


@cache
def event_rules() -> tuple[EventRule, ...]:
    raw = _load_yaml(EVENT_RULES_FILE).get("rules")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{EVENT_RULES_FILE}: `rules` must be a non-empty list")
    rules: list[EventRule] = []
    for i, entry in enumerate(raw):
        etype = entry.get("type")
        if etype not in EVENT_TYPES:
            raise ValueError(f"{EVENT_RULES_FILE}: rule {i} has unknown type {etype!r}")
        patterns = tuple(re.compile(p, re.IGNORECASE) for p in entry.get("patterns") or [])
        if not patterns:
            raise ValueError(f"{EVENT_RULES_FILE}: rule {i} ({etype}) has no patterns")
        rules.append(EventRule(etype, patterns))
    return tuple(rules)


# ----------------------------------------------------------------------------------- event type


def event_type(title: str, summary: str = "") -> str:
    """One of the 17 brief event types. The title decides; the summary only breaks a no-match."""
    for text in (title, summary):
        if not text:
            continue
        for rule in event_rules():
            if rule.matches(text):
                return rule.type
    return FALLBACK_EVENT_TYPE


def classify_headline(row: HeadlineRow) -> str:
    """Adapter for the ATL-58 evaluation report (registered as the ``classifier`` entry point)."""
    return event_type(row.title, row.summary)


# ------------------------------------------------------------------------------------ sentiment


@dataclass(frozen=True)
class Lexicon:
    words: Mapping[str, Decimal]  # signed weight
    negators: frozenset[str]
    intensifiers: Mapping[str, Decimal]
    negation_window: int
    negation_scale: Decimal
    alpha: Decimal


@cache
def lexicon() -> Lexicon:
    raw = _load_yaml(LEXICON_FILE)
    words: dict[str, Decimal] = {}
    for word, weight in (raw.get("positive") or {}).items():
        words[str(word).lower()] = Decimal(str(weight))
    for word, weight in (raw.get("negative") or {}).items():
        words[str(word).lower()] = -Decimal(str(weight))
    return Lexicon(
        words=words,
        negators=frozenset(str(w).lower() for w in raw.get("negators") or []),
        intensifiers={str(k).lower(): Decimal(str(v)) for k, v in (raw.get("intensifiers") or {}).items()},
        negation_window=int(raw.get("negation_window", 3)),
        negation_scale=Decimal(str(raw.get("negation_scale", "0.75"))),
        alpha=Decimal(str(raw.get("alpha", "4"))),
    )


_RIGHT_QUOTE = chr(0x2019)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'\-]*|n't", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    # Typographic apostrophes (U+2019) become ASCII so a curly "doesn't" is still a negator.
    for tok in _TOKEN.findall(text.lower().replace(_RIGHT_QUOTE, "'")):
        # "doesn't" stays whole (it is a negator); a trailing "'s" is dropped.
        if tok.endswith("'s"):
            tok = tok[:-2]
        out.append(tok)
    return out


def headline_sentiment(text: str) -> Decimal:
    """Lexicon score of one text, in -1..1, rounded to 3 places."""
    lex = lexicon()
    toks = _tokens(text)
    total = Decimal(0)
    for i, tok in enumerate(toks):
        weight = lex.words.get(tok)
        if weight is None:
            continue
        # An intensifier right before or right after ("sharply rises", "rises sharply").
        for j in (i - 1, i + 1):
            if 0 <= j < len(toks) and toks[j] in lex.intensifiers:
                weight *= lex.intensifiers[toks[j]]
                break
        window = toks[max(0, i - lex.negation_window) : i]
        if any(t in lex.negators or t.endswith("n't") for t in window):
            weight = -weight * lex.negation_scale
        total += weight
    if total == 0:
        return Decimal("0.000")
    squashed = total / (total * total + lex.alpha).sqrt()
    result = squashed.quantize(_THREE_PLACES, rounding=ROUND_HALF_EVEN)
    # Rounding can only reach ±1.000 at the edge; the contract is a closed -1..1 range.
    return max(Decimal("-1.000"), min(Decimal("1.000"), result))


def sentiment(title: str, summary: str, instruments: Iterable[str]) -> dict[str, Decimal]:
    """{instrument: score in -1..1}. Headline-level rules, so every linked instrument gets the same
    score; instrument-specific sentiment comes with News Intelligence's judgement in patch E."""
    score = headline_sentiment(f"{title}. {summary}" if summary else title)
    return {inst: score for inst in dict.fromkeys(instruments)}


# ----------------------------------------------------------------------------------- importance


def _importance_cfg() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["news"]["importance"]
    return cfg


def is_tier1_source(source: str) -> bool:
    tier1 = {str(s).lower() for s in _importance_cfg()["tier1_sources"]}
    return source.strip().lower() in tier1


def importance(
    event_type: str,
    source: str,
    linked_count: int,
    sentiment_abs: Decimal | float | int,
    novelty: Decimal | float | int | None,
) -> int:
    """1..5 by the formula documented above ``news.importance`` in config/defaults.yaml."""
    cfg = _importance_cfg()
    score = int(cfg["base"].get(event_type, cfg["default_base"]))
    if is_tier1_source(source):
        score += int(cfg["tier1_bonus"])
    sent = _as_decimal(sentiment_abs)
    if sent is not None and abs(sent) >= Decimal(cfg["strong_sentiment"]):
        score += int(cfg["strong_sentiment_bonus"])
    if linked_count >= int(cfg["multi_instrument_min"]):
        score += int(cfg["multi_instrument_bonus"])
    elif linked_count <= 0 and event_type not in cfg["market_wide_types"]:
        score -= int(cfg["unlinked_penalty"])
    nov = _as_decimal(novelty)
    if nov is not None:
        if nov >= Decimal(cfg["high_novelty"]):
            score += int(cfg["high_novelty_bonus"])
        elif nov < Decimal(cfg["low_novelty"]):
            score -= int(cfg["low_novelty_penalty"])
    return max(int(cfg["min"]), min(int(cfg["max"]), score))


def _as_decimal(value: Decimal | float | int | None) -> Decimal | None:
    """NaN and None mean "unknown" and add nothing."""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return Decimal(str(value))
    if isinstance(value, int):
        return Decimal(value)
    if value.is_nan() or value.is_infinite():
        return None
    return value
