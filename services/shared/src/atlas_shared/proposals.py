"""Proposal schemas (brief §6.3 ``TradeProposal``, §7.8 position actions; ATL-153).

Shared so the agents' ``warroom.submit_proposal`` tool, the Risk Engine and the Executor read the
same shape. Prices, sizes and percentages are Decimal. Unknown fields are refused.

* :class:`TradeProposal` — an entry, written only by the Strategist. ``stop`` is REQUIRED (rule 3).
* :class:`PositionAction` — ``amend_stop | amend_targets | partial_close | close`` on an open
  position, written only by the Portfolio Manager.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProposalKind = Literal["entry", "amend_stop", "amend_targets", "partial_close", "close"]
POSITION_ACTION_KINDS: tuple[str, ...] = ("amend_stop", "amend_targets", "partial_close", "close")
Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "post_only"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TakeProfit(_Model):
    """One take-profit: the price and the % of the position to close there."""

    price: Decimal = Field(gt=0)
    pct: Decimal = Field(gt=0, le=100)


class EvidenceRef(_Model):
    """A pointer to the evidence a proposal rests on: a research_report / warroom message id, a news
    item or cluster id, or a statistic id."""

    type: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=200)


def _check_targets(take_profits: tuple[TakeProfit, ...] | list[TakeProfit]) -> None:
    total = sum((tp.pct for tp in take_profits), Decimal(0))
    if total > 100:
        raise ValueError(f"take_profits pct adds up to {total}; it cannot exceed 100")


class TradeProposal(_Model):
    """Brief §6.3 ``TradeProposal`` (an entry)."""

    inst_id: str = Field(min_length=1, max_length=64)
    side: Side
    order_type: OrderType
    entry: Decimal | None = Field(default=None, gt=0)
    entry_zone: tuple[Decimal, Decimal] | None = None
    stop: Decimal = Field(gt=0)
    """REQUIRED (rule 3): every order has a stop-loss."""
    take_profits: list[TakeProfit] = Field(min_length=1, max_length=3)
    time_stop_minutes: int | None = Field(default=None, gt=0)
    size_suggestion_pct_equity: Decimal = Field(gt=0, le=100)
    leverage_suggestion: int = Field(ge=1)
    expected_r_multiple: Decimal
    probability_estimate: Decimal = Field(ge=0, le=1)
    rationale_md: str = Field(min_length=1, max_length=8000)
    invalidation: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _consistent(self) -> TradeProposal:
        if self.entry_zone is not None:
            low, high = self.entry_zone
            if low <= 0 or high < low:
                raise ValueError("entry_zone must be [low, high] with 0 < low <= high")
        if self.order_type != "market" and self.entry is None and self.entry_zone is None:
            raise ValueError(f"a {self.order_type} order needs entry or entry_zone")
        ref = self.reference_price()
        if ref is not None:
            if self.side == "buy" and self.stop >= ref:
                raise ValueError(f"a buy's stop ({self.stop}) must be below the entry ({ref})")
            if self.side == "sell" and self.stop <= ref:
                raise ValueError(f"a sell's stop ({self.stop}) must be above the entry ({ref})")
        _check_targets(self.take_profits)
        return self

    def reference_price(self) -> Decimal | None:
        """The entry price, else the entry-zone midpoint, else None (a market order with neither)."""
        if self.entry is not None:
            return self.entry
        if self.entry_zone is not None:
            return (self.entry_zone[0] + self.entry_zone[1]) / 2
        return None

    @property
    def direction(self) -> Literal["long", "short"]:
        return "long" if self.side == "buy" else "short"


class PositionAction(_Model):
    """A Portfolio Manager action on an open position (brief §7.8). ``stop`` is required for
    amend_stop, ``take_profits`` for amend_targets, ``close_pct`` (of the position) for
    partial_close; close takes none of them."""

    inst_id: str = Field(min_length=1, max_length=64)
    stop: Decimal | None = Field(default=None, gt=0)
    take_profits: list[TakeProfit] = Field(default_factory=list, max_length=3)
    close_pct: Decimal | None = Field(default=None, gt=0, lt=100)
    rule: str = Field(default="", max_length=200)
    """The management rule that fired (e.g. ``breakeven_at_1r``, ``time_stop``)."""
    rationale_md: str = Field(min_length=1, max_length=8000)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=50)


def validate_position_action(kind: str, action: PositionAction) -> list[str]:
    """The kind-specific required fields ``action`` lacks (empty = valid)."""
    problems: list[str] = []
    if kind == "amend_stop" and action.stop is None:
        problems.append("amend_stop needs stop")
    if kind == "amend_targets" and not action.take_profits:
        problems.append("amend_targets needs take_profits")
    if kind == "partial_close" and action.close_pct is None:
        problems.append("partial_close needs close_pct")
    if action.take_profits:
        try:
            _check_targets(action.take_profits)
        except ValueError as exc:
            problems.append(str(exc))
    return problems


__all__ = [
    "POSITION_ACTION_KINDS",
    "EvidenceRef",
    "OrderType",
    "PositionAction",
    "ProposalKind",
    "Side",
    "TakeProfit",
    "TradeProposal",
    "validate_position_action",
]
