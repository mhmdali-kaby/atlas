"""Market Scout's watchlist: what the model must answer and the rules code enforces on it (ATL-108).

Brief §7.1 steps 4-6 and its failure behaviour, decision #247 (2)(3). Everything here is pure (no
database, no bus) so each rule is unit-testable:

* :class:`ScoutAnswer` is the model's answer: a 3-line backdrop, 0..max picks
  (:class:`~atlas_agents.schemas.WatchlistPick`), the 3 strongest rejected candidates and, with no
  pick, the reason. :func:`answer_problems` lists what makes an answer unacceptable for this run
  (too many picks, too few rejected candidates, ...): those go back to the model once.
* :func:`correlated_groups`: groups of picks and open positions linked by 30-day correlation above
  the threshold. A group with more than ``max_correlated_picks`` members that holds a pick breaks
  step 4 ("avoid more than 2 picks with a 30-day correlation > 0.8"). The run refuses it once;
  :func:`trim_correlated` then drops the lowest-ranked picks of each such group.
* :func:`enforce` applies, in this order: drop instruments that are blocklisted, removed by the
  owner today, or not in the shortlist; trim correlated groups; carry today's owner pins; cap every
  conviction at ``partial_conviction_cap`` when the evidence is partial; renumber ranks 1..n. Every
  change is listed in ``corrections``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict, Field

from atlas_agents.schemas import WatchlistPick

CARD_TYPE: Final = "watchlist"
BACKDROP_LINES: Final = 3
REJECTED_COUNT: Final = 3

WatchlistStatus = Literal["published", "partial_evidence", "empty"]
TeammateStatus = Literal["replied", "timed_out", "not_asked"]


def publish_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    """``agents.scout`` (the pipeline keys: wait_seconds, stale_minutes, partial_conviction_cap...)."""
    config: Mapping[str, Any] = defaults["agents"]["scout"]
    return config


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RejectedCandidate(_Model):
    inst_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1000)


class ScoutAnswer(_Model):
    """The fenced JSON block that ends Market Scout's answer."""

    type: Literal["watchlist"] = CARD_TYPE
    backdrop: list[str] = Field(min_length=1)
    picks: list[WatchlistPick] = Field(default_factory=list)
    rejected: list[RejectedCandidate] = Field(default_factory=list)
    no_pick_reason: str | None = None


def answer_problems(answer: ScoutAnswer, *, max_picks: int, shortlist_ids: Sequence[str]) -> list[str]:
    """Why ``answer`` cannot be accepted as it is (empty when it can)."""
    problems: list[str] = []
    lines = [line for line in answer.backdrop if line.strip()]
    if len(lines) != BACKDROP_LINES:
        problems.append(f"backdrop must be exactly {BACKDROP_LINES} non-empty lines (got {len(lines)})")
    if len(answer.picks) > max_picks:
        problems.append(f"at most {max_picks} picks today (got {len(answer.picks)})")
    ids = [p.inst_id for p in answer.picks]
    if len(set(ids)) != len(ids):
        problems.append("each instrument may be picked once")
    ranks = [p.rank for p in answer.picks]
    if len(set(ranks)) != len(ranks):
        problems.append("pick ranks must be unique")
    picked = set(ids)
    available = [i for i in shortlist_ids if i not in picked]
    wanted = min(REJECTED_COUNT, len(available))
    rejected = [r.inst_id for r in answer.rejected]
    if len(rejected) != wanted:
        problems.append(f"list exactly {wanted} rejected candidates from the shortlist (got {len(rejected)})")
    unknown = [i for i in rejected if i not in shortlist_ids or i in picked]
    if unknown:
        problems.append(
            f"rejected candidates must be shortlist instruments you did not pick: {', '.join(unknown)}"
        )
    if not answer.picks and not (answer.no_pick_reason or "").strip():
        problems.append("with no picks, give no_pick_reason")
    return problems


# -- correlation (step 4) ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    corr: float


def correlated_groups(members: Iterable[str], pairs: Iterable[Pair], threshold: float) -> list[list[str]]:
    """Connected groups (size >= 2) of ``members`` linked by a pair with corr strictly above
    ``threshold``, each sorted, largest first."""
    nodes = list(dict.fromkeys(members))
    index = set(nodes)
    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        if p.a in index and p.b in index and p.a != p.b and p.corr > threshold:
            ra, rb = find(p.a), find(p.b)
            if ra != rb:
                parent[rb] = ra
    groups: dict[str, list[str]] = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    found = [sorted(g) for g in groups.values() if len(g) >= 2]
    found.sort(key=lambda g: (-len(g), g))
    return found


def correlation_violations(
    pick_ids: Sequence[str],
    held_ids: Sequence[str],
    pairs: Iterable[Pair],
    threshold: float,
    max_members: int,
) -> list[list[str]]:
    """The correlated groups of picks + open positions with more than ``max_members`` members that
    contain at least one pick."""
    picks = set(pick_ids)
    return [
        g
        for g in correlated_groups([*pick_ids, *held_ids], list(pairs), threshold)
        if len(g) > max_members and picks.intersection(g)
    ]


def trim_correlated(
    picks: Sequence[WatchlistPick],
    held_ids: Sequence[str],
    pairs: Sequence[Pair],
    threshold: float,
    max_members: int,
    keep: Iterable[str] = (),
) -> tuple[list[WatchlistPick], list[str]]:
    """Drop the lowest-ranked pick (never one in ``keep``, the owner's pins) of a violating group
    until no group violates. Returns (kept picks, dropped inst_ids)."""
    protected = set(keep)
    kept = sorted(picks, key=lambda p: p.rank)
    dropped: list[str] = []
    while True:
        groups = correlation_violations([p.inst_id for p in kept], held_ids, pairs, threshold, max_members)
        if not groups:
            return kept, dropped
        members = set(groups[0])
        victims = [p for p in kept if p.inst_id in members and p.inst_id not in protected]
        if not victims:
            return kept, dropped
        victim = victims[-1]
        kept = [p for p in kept if p is not victim]
        dropped.append(victim.inst_id)


# -- enforcement ------------------------------------------------------------------------------------


class Correction(_Model):
    rule: Literal[
        "blocked", "removed_by_owner", "not_in_shortlist", "correlation", "conviction_cap", "pinned"
    ]
    inst_id: str
    detail: str


class TeammateReply(_Model):
    agent: str
    status: TeammateStatus
    mention_id: str | None = None  # the mention's envelope id
    message_id: str | None = None  # the reply's warroom_messages id
    replied_at: datetime | None = None
    note: str | None = None


class Evidence(_Model):
    feature_table_as_of: datetime
    feature_table_age_minutes: float
    feature_table_stale: bool
    teammates: list[TeammateReply]

    @property
    def partial(self) -> bool:
        return self.feature_table_stale or any(t.status != "replied" for t in self.teammates)

    def partial_reasons(self, stale_minutes: int) -> list[str]:
        reasons: list[str] = []
        if self.feature_table_stale:
            reasons.append(
                f"feature table is {self.feature_table_age_minutes:.0f} min old (limit {stale_minutes} min)"
            )
        for t in self.teammates:
            if t.status == "timed_out":
                reasons.append(f"{t.agent} did not reply in time")
            elif t.status == "not_asked":
                reasons.append(f"{t.agent} was not asked ({t.note or 'no mention'})")
        return reasons


@dataclass
class Enforced:
    picks: list[WatchlistPick]
    pinned: list[str]
    corrections: list[Correction] = field(default_factory=list)


def enforce(
    picks: Sequence[WatchlistPick],
    *,
    blocklist: Iterable[str],
    removed: Iterable[str],
    shortlist_ids: Iterable[str],
    pinned_items: Sequence[WatchlistPick],
    held_ids: Sequence[str],
    pairs: Sequence[Pair],
    threshold: float,
    max_members: int,
    partial: bool,
    cap: float,
) -> Enforced:
    """The picks as they are saved (see the module docstring for the order of the rules)."""
    blocked = {b.upper() for b in blocklist}
    gone = set(removed)
    allowed = set(shortlist_ids)
    pin_ids = [p.inst_id for p in pinned_items if p.inst_id.upper() not in blocked]
    corrections: list[Correction] = []
    kept: list[WatchlistPick] = []
    for pick in sorted(picks, key=lambda p: p.rank):
        if pick.inst_id.upper() in blocked:
            corrections.append(Correction(rule="blocked", inst_id=pick.inst_id, detail="on the blocklist"))
        elif pick.inst_id in gone and pick.inst_id not in pin_ids:
            corrections.append(
                Correction(rule="removed_by_owner", inst_id=pick.inst_id, detail="the owner removed it today")
            )
        elif pick.inst_id not in allowed and pick.inst_id not in pin_ids:
            corrections.append(
                Correction(rule="not_in_shortlist", inst_id=pick.inst_id, detail="not in today's shortlist")
            )
        else:
            kept.append(pick)
    kept, dropped = trim_correlated(kept, held_ids, pairs, threshold, max_members, keep=pin_ids)
    for inst_id in dropped:
        corrections.append(
            Correction(
                rule="correlation",
                inst_id=inst_id,
                detail=f"more than {max_members} picks/open positions with 30-day correlation > {threshold}",
            )
        )
    present = {p.inst_id for p in kept}
    for item in pinned_items:
        if item.inst_id in pin_ids and item.inst_id not in present:
            kept.append(item.model_copy(update={"rank": 10_000 + len(kept)}))
            corrections.append(
                Correction(
                    rule="pinned", inst_id=item.inst_id, detail="owner pin carried from today's watchlist"
                )
            )
    if partial:
        capped: list[WatchlistPick] = []
        for pick in kept:
            if pick.conviction > cap:
                corrections.append(
                    Correction(
                        rule="conviction_cap",
                        inst_id=pick.inst_id,
                        detail=f"conviction {pick.conviction:g} capped at {cap:g} (partial evidence)",
                    )
                )
                pick = pick.model_copy(update={"conviction": cap})
            capped.append(pick)
        kept = capped
    ordered = sorted(kept, key=lambda p: p.rank)
    final = [p.model_copy(update={"rank": i}) for i, p in enumerate(ordered, start=1)]
    return Enforced(
        picks=final, pinned=[p.inst_id for p in final if p.inst_id in pin_ids], corrections=corrections
    )


def status_for(picks: Sequence[WatchlistPick], partial: bool) -> WatchlistStatus:
    if not picks:
        return "empty"
    return "partial_evidence" if partial else "published"


__all__ = [
    "BACKDROP_LINES",
    "CARD_TYPE",
    "REJECTED_COUNT",
    "Correction",
    "Enforced",
    "Evidence",
    "Pair",
    "RejectedCandidate",
    "ScoutAnswer",
    "TeammateReply",
    "answer_problems",
    "correlated_groups",
    "correlation_violations",
    "enforce",
    "publish_config",
    "status_for",
    "trim_correlated",
]
