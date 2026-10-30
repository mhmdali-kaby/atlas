"""What the Portfolio Manager may submit in a run (brief §7.8, ATL-169).

The PM's position checks compute the rule actions in code (:mod:`atlas_agents.pm.rules`) before the
model runs, and register them here under the run's correlation id. ``warroom.submit_proposal`` (the
model's tool call) and the PM reviewer (the model's answer block) both go through :func:`check`,
which refuses, in this order:

* ``widens_stop`` — an amend_stop that is not strictly tighter than the stop resting on OKX now, or
  that sits on the wrong side of the mark (stops only move in the profit direction);
* ``not_computed_action`` — anything that is not exactly one of the computed actions (kind, rule,
  stop, close_pct), including any submission in a run where no rule fired.

An accepted submission is recorded, so the same action is never saved twice in one run.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, Literal

from atlas_shared.proposals import PositionAction

from atlas_agents.pm.rules import RuleAction, is_tighter
from atlas_agents.tools.framework import ToolContext, ToolError

PORTFOLIO_MANAGER: Final = "portfolio_manager"


def rule_label(action: RuleAction) -> str:
    """The rule name a proposal carries: the event rule's one-shot key (``event_reduce:<id>``) so a
    reduction for one event is proposed once; every other rule's name."""
    key = action.data.get("applied_key")
    return str(key) if isinstance(key, str) and key else action.rule


@dataclass
class Expected:
    """The actions code computed for one position in one run."""

    inst_id: str
    side: Literal["long", "short"]
    current_stop: Decimal | None
    mark: Decimal
    actions: tuple[RuleAction, ...]
    submitted: dict[str, str] = field(default_factory=dict)
    """rule label -> proposal id of what this run saved."""

    def pending(self) -> list[RuleAction]:
        return [a for a in self.actions if rule_label(a) not in self.submitted]


_lock = threading.Lock()
_expected: dict[tuple[str, str], Expected] = {}


@contextmanager
def expecting(env: str, correlation_id: str, expected: Expected) -> Iterator[Expected]:
    """Register ``expected`` for the run with ``correlation_id`` while the block runs."""
    key = (env, correlation_id)
    with _lock:
        _expected[key] = expected
    try:
        yield expected
    finally:
        with _lock:
            _expected.pop(key, None)


def _current(ctx: ToolContext) -> Expected | None:
    corr = ctx.run.correlation_id or ctx.correlation_id
    if not corr:
        return None
    with _lock:
        return _expected.get((ctx.env, corr))


def _same(a: Decimal | None, b: Decimal | None) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and a == b)


def _matches(action: RuleAction, kind: str, model: PositionAction) -> bool:
    return (
        action.kind == kind
        and model.rule in (action.rule, rule_label(action))
        and model.inst_id == action.inst_id
        and _same(model.stop, action.stop)
        and _same(model.close_pct, action.close_pct)
    )


def match(expected: Expected | None, kind: str, model: PositionAction) -> RuleAction:
    """The computed action ``model`` is, or ToolError (``widens_stop`` / ``not_computed_action`` /
    ``already_submitted``)."""
    if expected is None or not expected.actions:
        raise ToolError(
            "not_computed_action",
            "no management rule fired for this position in this run; the Portfolio Manager submits only "
            "the action code computed",
        )
    if model.inst_id != expected.inst_id:
        raise ToolError("not_computed_action", f"this run manages {expected.inst_id}, not {model.inst_id}")
    if kind == "amend_stop" and model.stop is not None:
        safe = model.stop < expected.mark if expected.side == "long" else model.stop > expected.mark
        if not safe or not is_tighter(expected.side, model.stop, expected.current_stop):
            raise ToolError(
                "widens_stop",
                f"stop {model.stop} is not tighter than the current stop {expected.current_stop} on the "
                f"{expected.side} {expected.inst_id} (mark {expected.mark}); stops only move in the profit "
                "direction",
            )
    for action in expected.actions:
        if _matches(action, kind, model):
            if rule_label(action) in expected.submitted:
                raise ToolError(
                    "already_submitted",
                    f"{action.rule} was already submitted in this run "
                    f"(proposal {expected.submitted[rule_label(action)]})",
                )
            return action
    wanted = "; ".join(_describe(a) for a in expected.actions)
    raise ToolError(
        "not_computed_action",
        f"{kind} {model.rule or '(no rule)'} is not the action code computed; submit exactly: {wanted}",
    )


def check(ctx: ToolContext, kind: str, model: PositionAction) -> RuleAction | None:
    """For a Portfolio Manager submission through ``warroom.submit_proposal``: the computed action it
    is (ToolError otherwise). None for any other agent."""
    if ctx.agent != PORTFOLIO_MANAGER:
        return None
    return match(_current(ctx), kind, model)


def record(ctx: ToolContext, action: RuleAction, proposal_id: str) -> None:
    expected = _current(ctx)
    if expected is not None:
        with _lock:
            expected.submitted[rule_label(action)] = proposal_id


def _describe(action: RuleAction) -> str:
    parts = [f"kind {action.kind}", f"rule {rule_label(action)}"]
    if action.stop is not None:
        parts.append(f"stop {action.stop}")
    if action.close_pct is not None:
        parts.append(f"close_pct {action.close_pct}")
    return ", ".join(parts)


def describe(actions: Sequence[RuleAction]) -> list[str]:
    return [_describe(a) for a in actions]


__all__ = ["Expected", "check", "describe", "expecting", "match", "record", "rule_label"]
