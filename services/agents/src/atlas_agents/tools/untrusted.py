"""Quoting external text for the model (brief rule 7, §6.1).

News bodies, web pages, social posts and OKX announcements reach an agent only through
``untrusted(text, source)``: the text is wrapped in ``<untrusted_source source="…">…</untrusted_source>``
with every ``untrusted_source`` tag inside it escaped, so the text cannot close the quote early and
speak as the system. A rule-based detector (patterns in ``agents.tools.injection_patterns``) reports
instruction-like text; the caller raises the War Room alert, because only it knows the item and the
agent.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from typing import Any, Final

from atlas_shared.defaults import DEFAULTS

TAG: Final = "untrusted_source"
# Any opening or closing untrusted_source tag, however it is spaced or cased.
_TAG_RE: Final = re.compile(r"<(\s*/?\s*)(untrusted_source)", re.IGNORECASE)
# Zero-width and bidi-control characters that can hide a pattern from a plain regex.
_INVISIBLE: Final = re.compile("[​-‏‪-‮⁠-⁤﻿]")
_SNIPPET_MAX: Final = 80


@dataclass(frozen=True)
class UntrustedText:
    """The quoted text to hand to the model, plus what the detector matched in the original."""

    text: str
    source: str
    flags: tuple[str, ...]

    @property
    def flagged(self) -> bool:
        return bool(self.flags)


@lru_cache(maxsize=8)
def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def default_patterns(defaults: Mapping[str, Any] = DEFAULTS) -> tuple[str, ...]:
    return tuple(str(p) for p in defaults["agents"]["tools"]["injection_patterns"])


def _normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", folded))


def detect_instructions(text: str, patterns: Iterable[str] | None = None) -> tuple[str, ...]:
    """The distinct instruction-like snippets found in ``text`` (empty when it looks clean)."""
    compiled = _compile(tuple(patterns) if patterns is not None else default_patterns())
    clean = _normalise(text)
    hits: dict[str, None] = {}
    for pattern in compiled:
        for match in pattern.finditer(clean):
            hits.setdefault(match.group(0)[:_SNIPPET_MAX], None)
    return tuple(hits)


def escape_tags(text: str) -> str:
    """Neutralise every ``untrusted_source`` tag in ``text`` (``<`` becomes ``&lt;``)."""
    return _TAG_RE.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}", text)


def untrusted(text: str, source: str, *, patterns: Iterable[str] | None = None) -> UntrustedText:
    """Wrap ``text`` from ``source`` (a feed name or URL) for the model and report injection hits."""
    flags = detect_instructions(text, patterns)
    attr = escape(_normalise(source), quote=True)
    body = escape_tags(text)
    return UntrustedText(text=f'<{TAG} source="{attr}">{body}</{TAG}>', source=source, flags=flags)
