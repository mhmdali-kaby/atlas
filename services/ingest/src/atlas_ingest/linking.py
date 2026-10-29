"""Entity linking: headline text → OKX instrument families and asset classes (brief §7.2 step 3, ATL-62).

Deterministic code, no LLM (decision #243). A :class:`Linker` is built from alias rows (the
``instrument_aliases`` table, or :func:`atlas_ingest.aliases.build_alias_rows` in memory), the active
env's instrument families and the curated context rules in ``config/news_aliases.yaml``.

How an alias is matched is read from the alias text itself, so a DB row carries its own rule:

* all lowercase, or a caseless script → case-insensitive whole word (``bitcoin``, ``الذهب``);
* any uppercase letter → exact case or all capitals, whole word (``Apple``/``APPLE``, ``SOL``); a
  hyphen followed by an uppercase letter or digit ends no match, so ``OP-ED`` is not ``OP``. An
  uppercase ticker also matches as a ``$cashtag`` in any case;
* a leading ``$`` → cashtag only (``$ONE``);
* a trailing ``*`` → word prefix, for inflected scripts (``биткоин*`` matches ``биткоине``);
* only CJK / kana / hangul → substring, since those scripts do not separate words.

Arabic aliases that start with the article ``ال`` may also carry up to two of the prefixes و ف ب ل ك.
Text and aliases are NFKC-normalised; Arabic diacritics and tatweel are removed and alef forms folded.
Entity offsets refer to the normalised field text.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Final, Literal

import yaml  # type: ignore[import-untyped]  # no stubs in the workspace; parsed into pydantic
from pydantic import BaseModel, ConfigDict, Field

INSTRUMENT: Final = "instrument_family"
ASSET_CLASS: Final = "asset_class"
TargetKind = Literal["instrument_family", "asset_class"]
Mode = Literal["word", "exact", "cashtag", "prefix", "substring"]

CURATED_PRIORITY = 10
FAMILY_PRIORITY = 5
TICKER_PRIORITY = 0

_ARABIC_MARKS = re.compile("[ً-ٰٟـ]")
_ALEF = str.maketrans(
    dict.fromkeys("\u0623\u0625\u0622\u0671", "\u0627")
)  # hamza/madda/wasla alef forms -> bare alef
# Curly and modifier apostrophes/quotes -> ASCII.
_QUOTES = str.maketrans(
    {**dict.fromkeys("\u2018\u2019\u201b\u02bc", "'"), **dict.fromkeys("\u201c\u201d", '"')}
)
_CJK_CHARS = "぀-ヿ㐀-䶿一-鿿가-힯豈-﫿ᄀ-ᇿ"
_CJK_ONLY = re.compile(f"^[{_CJK_CHARS}]+$")
_TICKER = re.compile(r"^[A-Z][A-Z0-9]*$")
_ARABIC_ARTICLE = "ال"  # ال
_ARABIC_PREFIXES = "وفبلك"  # و ف ب ل ك
_CASHTAG = re.compile(r"(?<![\w$])\$([A-Za-z][A-Za-z0-9]{0,14})(?!\w)")


def normalize_text(text: str) -> str:
    """NFKC, straight quotes, Arabic diacritics/tatweel removed and alef forms folded. Case is kept."""
    out = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return _ARABIC_MARKS.sub("", out).translate(_ALEF)


def normalize_alias(alias: str) -> str:
    """The ``instrument_aliases.alias_normalized`` value: normalised, trimmed and case-folded."""
    return normalize_text(alias).strip().casefold()


def alias_mode(alias: str) -> tuple[Mode, str]:
    """(match mode, lookup key) for an alias, following the rules in the module docstring."""
    a = normalize_text(alias).strip()
    if a.startswith("$"):
        return "cashtag", a[1:].upper()
    prefix = a.endswith("*")
    core = a.rstrip("*").strip()
    if _CJK_ONLY.match(core):
        return "substring", core
    if prefix:
        return "prefix", core.casefold()
    if any(ch.isupper() for ch in core):
        return "exact", core
    return "word", core.casefold()


def alias_core(alias: str) -> str:
    """Normalised, case-folded alias with its ``$`` / ``*`` markers removed (what context rules name)."""
    return normalize_alias(alias).lstrip("$").rstrip("*").strip()


# --- curated config -----------------------------------------------------------------------------


class AssetClassAliases(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    aliases: list[str]
    classes: list[str]


class ContextRule(BaseModel):
    """Drop a link when the text shows it is not the asset (``unless``) or lacks support (``require_any``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    target: str = Field(description="base symbol, e.g. XAU")
    aliases: list[str] = Field(default_factory=list)
    unless: list[str] = Field(default_factory=list)
    require_any: list[str] = Field(default_factory=list)


class CuratedAliases(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instruments: dict[str, list[str]] = Field(default_factory=dict)
    asset_classes: list[AssetClassAliases] = Field(default_factory=list)
    context_rules: list[ContextRule] = Field(default_factory=list)
    ticker_stoplist: list[str] = Field(default_factory=list)


def config_dir() -> Path:
    """``ATLAS_CONFIG_DIR`` when set, else the repository's ``config/``."""
    override = os.environ.get("ATLAS_CONFIG_DIR", "")
    if override:
        return Path(override)
    # services/ingest/src/atlas_ingest/linking.py -> parents[4] is the repository root.
    return Path(__file__).resolve().parents[4] / "config"


@cache
def _load_curated(path: Path) -> CuratedAliases:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return CuratedAliases.model_validate(data)


def load_curated(path: Path | None = None) -> CuratedAliases:
    return _load_curated((path or config_dir() / "news_aliases.yaml").resolve())


# --- results ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AliasRow:
    """One ``instrument_aliases`` row. ``target`` is an instrument family (``BTC-USDT``) or an asset class."""

    alias: str
    target_kind: TargetKind
    target: str
    priority: int = 0

    @property
    def alias_normalized(self) -> str:
        return normalize_alias(self.alias)


@dataclass(frozen=True)
class Entity:
    text: str
    alias: str
    target_kind: TargetKind
    target: str
    field: Literal["title", "summary"]
    start: int
    end: int


@dataclass(frozen=True)
class LinkResult:
    instruments: tuple[str, ...]
    asset_classes: tuple[str, ...]
    entities: tuple[Entity, ...]


@dataclass(frozen=True)
class _Match:
    start: int
    end: int
    text: str
    rows: tuple[AliasRow, ...]

    @property
    def priority(self) -> int:
        return max(r.priority for r in self.rows)


def _alternation(keys: Iterable[str]) -> str:
    return "|".join(re.escape(k) for k in sorted(set(keys), key=lambda k: (-len(k), k)))


def _terms_pattern(terms: Sequence[str]) -> re.Pattern[str] | None:
    parts = []
    for term in sorted({normalize_alias(t) for t in terms if t.strip()}, key=lambda t: (-len(t), t)):
        parts.append(re.escape(term) if _CJK_ONLY.match(term) else r"(?<!\w)" + re.escape(term))
    return re.compile("|".join(parts)) if parts else None


@dataclass(frozen=True)
class _CompiledRule:
    target: str
    aliases: frozenset[str]
    unless: re.Pattern[str] | None
    require_any: re.Pattern[str] | None

    def drops(self, base: str, row: AliasRow, text_cf: str) -> bool:
        if base != self.target or (self.aliases and alias_core(row.alias) not in self.aliases):
            return False
        if self.unless is not None and self.unless.search(text_cf):
            return True
        return self.require_any is not None and not self.require_any.search(text_cf)


def family_base(family: str) -> str:
    return family.split("-", 1)[0]


class Linker:
    """Matches alias rows in text. Instrument rows whose family is not in ``families`` are ignored,
    so an alias for an instrument missing in the active env links nothing."""

    def __init__(
        self,
        rows: Iterable[AliasRow],
        families: Mapping[str, str],
        rules: Sequence[ContextRule] = (),
    ) -> None:
        self.families = dict(families)
        lookup: dict[Mode, dict[str, list[AliasRow]]] = {
            "word": {},
            "exact": {},
            "cashtag": {},
            "prefix": {},
            "substring": {},
        }
        for row in rows:
            if row.target_kind == INSTRUMENT and row.target not in self.families:
                continue
            mode, key = alias_mode(row.alias)
            keys = [key]
            if mode == "exact" and key.upper() != key:
                # "Sui" also matches "SUI": the unique key (alias_normalized, target) holds one case.
                keys.append(key.upper())
            for k in keys:
                lookup[mode].setdefault(k, []).append(row)
                if mode == "exact" and _TICKER.match(k):
                    lookup["cashtag"].setdefault(k, []).append(row)
        self._lookup = {m: {k: tuple(v) for k, v in d.items()} for m, d in lookup.items()}

        self._patterns: list[tuple[Mode, re.Pattern[str]]] = []
        word_keys = self._lookup["word"]
        if word_keys:
            alts = []
            for key in sorted(word_keys, key=lambda k: (-len(k), k)):
                pre = f"[{_ARABIC_PREFIXES}]{{0,2}}" if key.startswith(_ARABIC_ARTICLE) else ""
                alts.append(pre + re.escape(key))
            self._patterns.append(("word", re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)", re.I)))
        if self._lookup["exact"]:
            alt = _alternation(self._lookup["exact"])
            self._patterns.append(("exact", re.compile(r"(?<![\w$-])(?:" + alt + r")(?!\w|-[A-Z0-9])")))
        if self._lookup["prefix"]:
            alt = _alternation(self._lookup["prefix"])
            self._patterns.append(("prefix", re.compile(r"(?<!\w)(" + alt + r")\w*", re.I)))
        if self._lookup["substring"]:
            self._patterns.append(("substring", re.compile(_alternation(self._lookup["substring"]))))

        self._rules = [
            _CompiledRule(
                target=r.target,
                aliases=frozenset(alias_core(a) for a in r.aliases),
                unless=_terms_pattern(r.unless),
                require_any=_terms_pattern(r.require_any),
            )
            for r in rules
        ]

    def _key(self, mode: Mode, m: re.Match[str]) -> tuple[AliasRow, ...]:
        table = self._lookup[mode]
        if mode == "word":
            key = m.group(0).casefold()
            hit = table.get(key)
            # Strip Arabic clitic prefixes (و ف ب ل ك) until the article-led alias is found.
            while hit is None and key[:1] and key[0] in _ARABIC_PREFIXES:
                key = key[1:]
                hit = table.get(key)
            return hit or ()
        if mode == "prefix":
            return table.get(m.group(1).casefold(), ())
        return table.get(m.group(0), ())

    def _scan(self, text: str) -> list[_Match]:
        found: list[_Match] = []
        for mode, pattern in self._patterns:
            for m in pattern.finditer(text):
                rows = self._key(mode, m)
                if rows:
                    found.append(_Match(m.start(), m.end(), m.group(0), rows))
        for m in _CASHTAG.finditer(text):
            rows = self._lookup["cashtag"].get(m.group(1).upper(), ())
            if rows:
                found.append(_Match(m.start(), m.end(), m.group(0), rows))
        # Longest span wins an overlap ("Bitcoin Cash" over "Bitcoin"), then priority, then position.
        found.sort(key=lambda x: (-(x.end - x.start), -x.priority, x.start))
        kept: list[_Match] = []
        for cand in found:
            if all(cand.end <= k.start or cand.start >= k.end for k in kept):
                kept.append(cand)
        return sorted(kept, key=lambda x: x.start)

    def link(self, title: str, summary: str = "") -> LinkResult:
        fields: list[tuple[Literal["title", "summary"], str]] = [
            ("title", normalize_text(title)),
            ("summary", normalize_text(summary)),
        ]
        text_cf = "\n".join(t for _, t in fields).casefold()
        entities: list[Entity] = []
        seen: set[tuple[str, int, str]] = set()
        for field_name, text in fields:
            for match in self._scan(text):
                for row in match.rows:
                    if (field_name, match.start, row.target) in seen:
                        continue
                    if row.target_kind == INSTRUMENT and any(
                        rule.drops(family_base(row.target), row, text_cf) for rule in self._rules
                    ):
                        continue
                    seen.add((field_name, match.start, row.target))
                    entities.append(
                        Entity(
                            text=match.text,
                            alias=row.alias,
                            target_kind=row.target_kind,
                            target=row.target,
                            field=field_name,
                            start=match.start,
                            end=match.end,
                        )
                    )
        instruments = sorted({e.target for e in entities if e.target_kind == INSTRUMENT})
        classes = {self.families[i] for i in instruments} | {
            e.target for e in entities if e.target_kind == ASSET_CLASS
        }
        return LinkResult(tuple(instruments), tuple(sorted(classes)), tuple(entities))


# --- process-wide default -----------------------------------------------------------------------

_default: Linker | None = None


def set_default_linker(linker: Linker | None) -> None:
    """Install the linker :func:`link` uses (the pipeline does this after each ``aliases sync``)."""
    global _default
    _default = linker


def default_linker() -> Linker:
    """The installed linker, else one loaded from ``instrument_aliases`` for the active env."""
    global _default
    if _default is None:
        from atlas_shared.db import make_engine

        from atlas_ingest import aliases

        _default = aliases.load_linker(make_engine(), aliases.active_env())
    return _default


def link(title: str, summary: str = "") -> LinkResult:
    return default_linker().link(title, summary)
