"""The Devil's Advocate's review card (brief §7.7, ATL-159), shared so the vote (ATL-154), the
Orchestrator and the War Room read the card the Devil's Advocate writes.

A review answers every one of the ten §7.7 checklist items, in order, as "ok" or a problem with a
number, names the strongest objection, rates the risk 1-5, gives the fix and the vote.

``precomputed``, ``corrections`` and ``meta`` are written by code after the model
(atlas_agents.devils_advocate.enforce_review), never trusted from the model. ``risk_rating`` is
an integer so the vote's ``da_rating`` gate (ATL-154) can read it.
"""

from __future__ import annotations

import re
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ItemName = Literal[
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
]
CHECKLIST: Final[tuple[ItemName, ...]] = (
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
"""The ten §7.7 items in brief order: item n is CHECKLIST[n - 1]."""

Severity = Literal["none", "minor", "serious", "loss_gt_1r"]
Vote = Literal["take", "change", "skip"]
CARD_TYPE: Final = "da_review"

_DIGIT: Final = re.compile(r"\d")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewItem(_Model):
    """One checklist item: "ok" (severity none), or a problem whose detail carries a number.

    severity: minor, serious, or loss_gt_1r (the item would plausibly cause a loss larger than 1R:
    a gap through the stop, an event inside the window with a tight stop)."""

    item: int = Field(ge=1, le=10)
    name: ItemName
    status: Literal["ok", "problem"]
    severity: Severity = "none"
    detail: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def _consistent(self) -> ReviewItem:
        if CHECKLIST[self.item - 1] != self.name:
            raise ValueError(f"item {self.item} is {CHECKLIST[self.item - 1]}, not {self.name}")
        if self.status == "ok" and self.severity != "none":
            raise ValueError(f"item {self.item} ({self.name}) is ok, so its severity must be none")
        if self.status == "problem":
            if self.severity == "none":
                raise ValueError(f"item {self.item} ({self.name}) is a problem: give its severity")
            if not _DIGIT.search(self.detail):
                raise ValueError(f"item {self.item} ({self.name}) is a problem: state it with a number")
        return self


class DevilsReview(_Model):
    """The ``da_review`` card. ``round`` 1 is the review of a new or revised proposal version; round
    2 is the one re-rate after the Strategist's reply (``rerate_of`` = the reviewed message's id)."""

    type: Literal["da_review"] = "da_review"
    proposal_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    inst_id: str = Field(min_length=1)
    round: int = Field(default=1, ge=1, le=2)
    rerate_of: str | None = None
    items: tuple[ReviewItem, ...]
    strongest_objection: str = Field(min_length=1, max_length=600)
    risk_rating: int = Field(ge=1, le=5)
    fix: str | None = Field(default=None, max_length=1000)
    fix_applied: bool = False
    vote: Vote
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="", max_length=2000)
    precomputed: dict[str, Any] | None = None
    corrections: tuple[dict[str, Any], ...] = ()
    meta: dict[str, Any] | None = None

    @field_validator("items")
    @classmethod
    def _all_ten(cls, items: tuple[ReviewItem, ...]) -> tuple[ReviewItem, ...]:
        seen = [i.item for i in items]
        duplicates = sorted({n for n in seen if seen.count(n) > 1})
        if duplicates:
            raise ValueError(f"checklist items answered twice: {', '.join(map(str, duplicates))}")
        missing = [n for n in range(1, 11) if n not in seen]
        if missing:
            names = ", ".join(f"{n} ({CHECKLIST[n - 1]})" for n in missing)
            raise ValueError(f"checklist items missing: {names}; answer all 10, even if just OK")
        return tuple(sorted(items, key=lambda i: i.item))

    def problems(self, severity: Severity) -> list[ReviewItem]:
        return [i for i in self.items if i.severity == severity]


def guide_rating(items: tuple[ReviewItem, ...] | list[ReviewItem]) -> int:
    """The §7.7 rating guide: 5 if any item is loss_gt_1r; 4 if two or more are serious; 3 if one
    is; 2 if only minor problems; 1 if none."""
    severities = [i.severity for i in items]
    if "loss_gt_1r" in severities:
        return 5
    serious = severities.count("serious")
    if serious >= 2:
        return 4
    if serious == 1:
        return 3
    if "minor" in severities:
        return 2
    return 1


__all__ = [
    "CARD_TYPE",
    "CHECKLIST",
    "DevilsReview",
    "ItemName",
    "ReviewItem",
    "Severity",
    "Vote",
    "guide_rating",
]
