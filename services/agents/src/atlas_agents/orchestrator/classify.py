"""Code classification of an owner War Room message (brief §7.9 "Owner-message routing"; ATL-161).

:func:`classify` returns ``instruction``, ``question``, ``approval``, ``limit_change_request`` or
``chat`` from keyword rules. ``limit_change_request`` and ``approval`` are decisive: they get a fixed
answer and never reach the model, so no model text can change a limit or approve a trade. A message
that reads as a question (a trailing ? / ؟, or a question word first) is never a decisive approval,
and is a limit change only when it names a value (``20x``, ``6%``, ``500 USDT``). The other three
kinds are a first guess the model may correct (:mod:`atlas_agents.orchestrator.owner`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from atlas_shared.defaults import DEFAULTS

SLUG: Final = "orchestrator"
Kind = Literal["instruction", "question", "approval", "limit_change_request", "chat"]
KINDS: Final[tuple[Kind, ...]] = ("instruction", "question", "approval", "limit_change_request", "chat")
DECISIVE: Final = frozenset({"approval", "limit_change_request"})

LIMIT_REPLY: Final = (
    "Only the owner can change that in Settings → {section} ({setting}). Agents cannot change "
    "limits, so nothing was changed."
)
APPROVAL_REPLY: Final = (
    "Approvals count only from the Approve / Reject buttons on a proposal card. Nothing was "
    "approved or rejected from chat, and silence is never taken as approval."
)


@dataclass(frozen=True)
class LimitRef:
    setting: str
    section: str


# (pattern, the setting's name in Settings, the Settings section) — brief §14.3-§14.11.
_LIMITS: Final[tuple[tuple[re.Pattern[str], LimitRef], ...]] = tuple(
    (re.compile(pattern, re.IGNORECASE), LimitRef(setting, section))
    for pattern, setting, section in (
        (r"\blever(?:age)?\b|رافعة", "Max leverage", "Risk"),
        (r"\b(?:loss|risk) per trade\b", "Max loss per trade", "Risk"),
        (r"\bdaily loss\b|\bloss per day\b", "Max daily loss", "Risk"),
        (r"\bweekly loss\b|\bloss per week\b", "Max weekly loss", "Risk"),
        (r"\bposition size\b|\bmax(?:imum)? position\b", "Max position size", "Risk"),
        (r"\bopen positions\b", "Max open positions", "Risk"),
        (r"\bcorrelated exposure\b", "Max correlated exposure", "Risk"),
        (
            r"\b(?:share|exposure) (?:in|per) (?:one |an? )?instrument\b",
            "Max share in one instrument",
            "Risk",
        ),
        (r"\bmin(?:imum)? (?:expected )?r\b", "Min expected R", "Risk"),
        (r"\bconsecutive losses\b", "Max consecutive losses before pause", "Risk"),
        (
            r"\brisk profile\b|\b(?:conservative|balanced|aggressive) (?:profile|mode)\b",
            "Risk profile",
            "Risk",
        ),
        (r"\b(?:bot )?capital cap\b|\bbot capital\b", "Bot capital cap", "Money limits"),
        (
            r"\bmax(?:imum)? per trade\b|\busdt? per trade\b|\bper trade (?:usdt?|budget)\b",
            "Max per trade",
            "Money limits",
        ),
        (r"\btrades per day\b", "Max trades per day", "Money limits"),
        (r"\bexposure per day\b|\bdaily exposure\b", "Max new exposure per day", "Money limits"),
        (r"\bautonomy\b|\bfull auto\b|\bauto[- ]?trad", "Autonomy level", "Autonomy"),
        (r"\bapproval timeout\b", "Approval timeout", "Autonomy"),
        (r"\bblock ?list\b|\bwhite ?list\b", "Whitelist / blocklist", "Markets"),
        (r"\bmin(?:imum)? (?:24h )?volume\b", "Min 24h volume", "Markets"),
        (r"\bmax(?:imum)? spread\b", "Max spread", "Markets"),
        (r"\binstruments per day\b", "Max instruments per day", "Markets"),
        (
            r"\b(?:agent|token|turn)s? budget\b|\bvote threshold\b",
            "Agent budgets and vote threshold",
            "Agents",
        ),
        (r"\b(?:trade|go|switch(?: to)?) live\b|\blive trading\b", "Demo/Live switch", "Environment"),
    )
)
_CHANGE_VERB: Final = (
    r"\b(?:set|change|raise|lower|increase|decrease|bump|reduce|cut|make|allow|use|double|halve|lift|"
    r"remove|disable|enable|turn|switch|adjust|update|up|go)\b|غير|ارفع|زد|خفض|اجعل"
)
_VALUE: Final = r"\d+(?:\.\d+)?\s*(?:x|%|usdt?|\$)"
_CHANGE: Final = re.compile(rf"{_CHANGE_VERB}|{_VALUE}", re.IGNORECASE)
"""A statement asking for a change: a change verb or a value."""
_CHANGE_TO_VALUE: Final = re.compile(rf"(?:{_CHANGE_VERB}).*?{_VALUE}", re.IGNORECASE | re.DOTALL)
"""A question asking for a change: a change verb followed by a value ("can you set it to 20x?")."""
_APPROVAL: Final = re.compile(
    r"^\s*(?:approve[ds]?|reject(?:ed)?|go ahead|confirm(?:ed)?|yes,? (?:take|do|place) it|"
    r"ok(?:ay)?,? (?:take|place) it)\b|\bapprove\b|موافق|وافق|ارفض",
    re.IGNORECASE,
)
_QUESTION_START: Final = re.compile(
    r"^\s*(?:what|why|how|when|where|who|which|is|are|was|were|do|does|did|can|could|should|would|"
    r"will|any|ماذا|لماذا|كيف|متى|هل|ما)\b",
    re.IGNORECASE,
)
_INSTRUCTION_START: Final = re.compile(
    r"^\s*(?:please|check|look|analy[sz]e|research|focus|watch|skip|avoid|close|tighten|plan|find|"
    r"give|show|tell|ask|stop|prepare|review|monitor|compare|send|run|post|explain|summari[sz]e|"
    r"keep|drop|add|consider|wait|hold|cancel|study|scan|get|have|let)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Classification:
    kind: Kind
    decisive: bool
    limit: LimitRef | None = None
    agents: tuple[str, ...] = ()
    """Agents the message names (by name, handle or alias, without @)."""


def named_agents(content: str, defaults: Mapping[str, Any] = DEFAULTS) -> tuple[str, ...]:
    """Slugs of the agents ``content`` names (name, handle, alias or slug, as whole words)."""
    agents: Mapping[str, Mapping[str, Any]] = defaults["warroom"]["agents"]
    found: list[str] = []
    for slug, meta in agents.items():
        if slug == SLUG:
            continue
        words = {
            slug.replace("_", " "),
            str(meta["name"]),
            str(meta["handle"]),
            *map(str, meta.get("aliases") or []),
        }
        for word in words:
            if re.search(rf"(?<![\w@]){re.escape(word)}(?:'s)?(?!\w)", content, re.IGNORECASE):
                found.append(slug)
                break
    return tuple(found)


def is_question(content: str) -> bool:
    """A trailing ? / ؟, or a question word first."""
    body = content.strip()
    return body.endswith(("?", "؟")) or _QUESTION_START.search(body) is not None


def classify(content: str) -> Classification:
    """The code classification of an owner message (see the module docstring)."""
    body = content.strip()
    agents = named_agents(body)
    question = is_question(body)
    asks_change = _CHANGE_TO_VALUE.search(body) if question else _CHANGE.search(body)
    if asks_change:
        for pattern, ref in _LIMITS:
            if pattern.search(body):
                return Classification("limit_change_request", True, ref, agents)
    if question:
        return Classification("question", False, None, agents)
    if _APPROVAL.search(body):
        return Classification("approval", True, None, agents)
    if _INSTRUCTION_START.search(body) or agents:
        return Classification("instruction", False, None, agents)
    return Classification("chat", False, None, agents)


def limit_reply(ref: LimitRef | None) -> str:
    if ref is None:
        return LIMIT_REPLY.format(section="Risk / Money limits / Markets", setting="the limit you named")
    return LIMIT_REPLY.format(section=ref.section, setting=ref.setting)


__all__ = [
    "APPROVAL_REPLY",
    "DECISIVE",
    "KINDS",
    "LIMIT_REPLY",
    "SLUG",
    "Classification",
    "Kind",
    "LimitRef",
    "classify",
    "is_question",
    "limit_reply",
    "named_agents",
]
