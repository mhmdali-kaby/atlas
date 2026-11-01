"""The Portfolio Manager's daily score (ATL-197, brief §7.8 Scoring, decision #253 K).

Three numbers per env and Baghdad day, written by the 00:15 scoring job:

* **R captured vs MFE** over the trades closed that day: the PM's post-mortem journal entries
  (ATL-194) whose ``data.actual.closed_at`` is on the day. ``r_captured`` = Σ realised_r / Σ mfe_r
  over the trades with ``mfe_r > 0`` (the average R kept divided by the average best excursion of
  the same trades); ``avg_realised_r`` and ``avg_mfe_r`` are reported beside it.
* **Losers within 1.1R**: of the trades whose ``data.outcome`` is ``loss`` (with a realised R), the
  share closed at a loss no larger than ``loser_limit_r`` (realised_r >= -1.1). This is the row's
  ``hit_rate``; ``n`` is the closed trades with a realised R.
* **Rule compliance**: of the PM's proposals created that day, the share that (a) name a rule code
  emits for that proposal kind (:data:`RULE_KINDS`, from :mod:`atlas_agents.pm.rules`) and (b) for an
  ``amend_stop``, move the stop strictly tighter than the stop before it: the position's previous
  live ``amend_stop`` proposal after its entry, else the entry proposal's stop. A proposal whose entry
  cannot be found is listed as ``unverified`` and left out of the share. The PM's guard refuses both
  faults at submit time, so anything below 100 % means a path around it, and the job posts one War
  Room ``system_alert`` (and ``system.alert`` on atlas.system) per new set of violations; a re-run
  with the same violations posts nothing.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from atlas_shared.db import AgentScore
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from atlas_agents import scoring
from atlas_agents.pm.rules import Side, is_tighter
from atlas_agents.scorecards import mean, rate
from atlas_agents.tools import ToolContext
from atlas_agents.tools.warroom import WARROOM_STREAM, message_envelope

log = logging.getLogger(__name__)

AGENT: Final = "portfolio_manager"
ALERT_CODE: Final = "pm_rule_compliance"
SYSTEM_STREAM: Final = "atlas.system"

RULE_KINDS: Final[Mapping[str, frozenset[str]]] = {
    "amend_stop": frozenset({"breakeven", "tp1_trail", "two_r_trail"}),
    "partial_close": frozenset({"tp1_partial", "event_reduce"}),
    "close": frozenset({"time_stop", "invalidation", "event_reduce"}),
}
"""Proposal kind -> the rules in :mod:`atlas_agents.pm.rules` that emit it (no rule emits
amend_targets, so a PM amend_targets is never compliant)."""

_DEAD_STATUSES: Final = ("rejected", "cancelled", "expired")
"""A PM amend_stop in one of these never moved the stop, so it is not "the stop before"."""


def config() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["agents"]["scoring"]["portfolio_manager"]
    return cfg


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


# -- pure maths -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosedTrade:
    journal_id: str
    inst_id: str | None
    outcome: str | None
    realised_r: float | None
    mfe_r: float | None


def trade_stats(trades: Sequence[ClosedTrade], loser_limit_r: float) -> dict[str, Any]:
    """R captured vs MFE and the losers-within-limit share (see the module docstring)."""
    with_r = [t for t in trades if t.realised_r is not None]
    captured = [t for t in with_r if t.mfe_r is not None and t.mfe_r > 0]
    sum_mfe = sum(t.mfe_r for t in captured if t.mfe_r is not None)
    sum_real = sum(t.realised_r for t in captured if t.realised_r is not None)
    losers = [t for t in with_r if t.outcome == "loss"]
    within = [t for t in losers if t.realised_r is not None and t.realised_r >= -loser_limit_r]
    return {
        "trades_n": len(with_r),
        "r_captured": sum_real / sum_mfe if captured and sum_mfe else None,
        "r_captured_n": len(captured),
        "avg_realised_r": mean([t.realised_r for t in captured if t.realised_r is not None]),
        "avg_mfe_r": mean([t.mfe_r for t in captured if t.mfe_r is not None]),
        "losers_n": len(losers),
        "losers_within_n": len(within),
        "losers_within_rate": rate(len(within), len(losers)),
        "loser_limit_r": loser_limit_r,
    }


@dataclass(frozen=True)
class PmAction:
    proposal_id: str
    inst_id: str
    kind: str
    rule: str
    stop: Decimal | None
    side: str | None
    """long|short of the position (from its entry proposal); None when no entry was found."""
    previous_stop: Decimal | None


def _plain(value: Decimal | None) -> str | None:
    """``Decimal("100.10000")`` -> ``"100.1"`` (no exponent)."""
    return None if value is None else format(value.normalize(), "f")


def rule_base(label: str) -> str:
    """``event_reduce:<event id>`` -> ``event_reduce``."""
    return label.split(":", 1)[0].strip()


def check_action(action: PmAction) -> tuple[str, str | None]:
    """``("ok"|"violation"|"unverified", reason)`` for one PM proposal."""
    allowed = RULE_KINDS.get(action.kind, frozenset())
    if rule_base(action.rule) not in allowed:
        return "violation", f"rule {action.rule or '(none)'!r} is not one code emits for {action.kind}"
    if action.kind != "amend_stop":
        return "ok", None
    if action.stop is None:
        return "violation", "amend_stop without a stop"
    if action.side not in ("long", "short") or action.previous_stop is None:
        return "unverified", "no entry proposal with a stop for this position"
    side: Side = "long" if action.side == "long" else "short"
    if not is_tighter(side, action.stop, action.previous_stop):
        return "violation", (
            f"stop {_plain(action.stop)} is not tighter than the previous stop "
            f"{_plain(action.previous_stop)} on the {action.side}"
        )
    return "ok", None


def compliance(actions: Sequence[PmAction]) -> dict[str, Any]:
    checked: list[dict[str, Any]] = []
    for action in actions:
        status, reason = check_action(action)
        checked.append(
            {
                "proposal_id": action.proposal_id,
                "inst_id": action.inst_id,
                "kind": action.kind,
                "rule": action.rule,
                "stop": _plain(action.stop),
                "previous_stop": _plain(action.previous_stop),
                "side": action.side,
                "status": status,
                "reason": reason,
            }
        )
    scored = [c for c in checked if c["status"] != "unverified"]
    ok = sum(1 for c in scored if c["status"] == "ok")
    return {
        "compliance_rate": rate(ok, len(scored)),
        "compliance_n": len(scored),
        "compliance_ok": ok,
        "violations": [c for c in checked if c["status"] == "violation"],
        "unverified_n": sum(1 for c in checked if c["status"] == "unverified"),
        "actions": checked,
    }


# -- reads ------------------------------------------------------------------------------------------

_TRADES = text(
    "SELECT id, inst_id, data FROM journal_entries "
    "WHERE env = :env AND agent = :agent AND data ? 'position_ref' "
    "  AND (data->'actual'->>'closed_at')::timestamptz >= :start "
    "  AND (data->'actual'->>'closed_at')::timestamptz < :end "
    "ORDER BY (data->'actual'->>'closed_at')::timestamptz, id"
)

_ACTIONS = text(
    "SELECT id, inst_id, kind, invalidation, stop, evidence, created_at FROM proposals "
    "WHERE env = :env AND author = :agent AND kind <> 'entry' AND created_at >= :start AND created_at < :end "
    "ORDER BY created_at, id"
)

_ENTRY_BY_ID = text(
    "SELECT id, side, stop, created_at FROM proposals WHERE env = :env AND id = :id AND kind = 'entry'"
)

_LATEST_ENTRY = text(
    "SELECT id, side, stop, created_at FROM proposals "
    "WHERE env = :env AND inst_id = :inst AND kind = 'entry' AND created_at < :before "
    "  AND status IN ('sent', 'partially_filled', 'filled', 'closed') "
    "ORDER BY created_at DESC, id DESC LIMIT 1"
)

_PREVIOUS_AMEND = text(
    "SELECT stop FROM proposals "
    "WHERE env = :env AND inst_id = :inst AND author = :agent AND kind = 'amend_stop' AND stop IS NOT NULL "
    "  AND created_at > :after AND created_at < :before AND id <> :id "
    "  AND status <> ALL(:dead) "
    "ORDER BY created_at DESC, id DESC LIMIT 1"
)


def closed_trades(session: Session, env: str, day: date) -> list[ClosedTrade]:
    start, end = scoring.day_bounds(day)
    rows = session.execute(_TRADES, {"env": env, "agent": AGENT, "start": start, "end": end}).all()
    out: list[ClosedTrade] = []
    for jid, inst_id, data in rows:
        data = data or {}
        actual = data.get("actual") or {}
        out.append(
            ClosedTrade(
                journal_id=str(jid),
                inst_id=inst_id,
                outcome=data.get("outcome"),
                realised_r=_num(actual.get("realised_r")),
                mfe_r=_num(actual.get("mfe_r")),
            )
        )
    return out


def _entry_ref(evidence: Any) -> str | None:
    for ref in evidence or []:
        if isinstance(ref, Mapping) and ref.get("type") == "proposal" and ref.get("id"):
            return str(ref["id"])
    return None


def pm_actions(session: Session, env: str, day: date) -> list[PmAction]:
    start, end = scoring.day_bounds(day)
    out: list[PmAction] = []
    rows = session.execute(_ACTIONS, {"env": env, "agent": AGENT, "start": start, "end": end}).all()
    for pid, inst_id, kind, rule, stop, evidence, created_at in rows:
        entry = None
        ref = _entry_ref(evidence)
        if ref is not None:
            try:
                entry = session.execute(_ENTRY_BY_ID, {"env": env, "id": uuid.UUID(ref)}).first()
            except ValueError:
                entry = None
        if entry is None:
            entry = session.execute(
                _LATEST_ENTRY, {"env": env, "inst": inst_id, "before": created_at}
            ).first()
        side: str | None = None
        previous: Decimal | None = None
        if entry is not None:
            _, entry_side, entry_stop, entry_at = entry
            side = {"buy": "long", "sell": "short"}.get(str(entry_side))
            previous = session.execute(
                _PREVIOUS_AMEND,
                {
                    "env": env,
                    "inst": inst_id,
                    "agent": AGENT,
                    "after": entry_at,
                    "before": created_at,
                    "id": pid,
                    "dead": list(_DEAD_STATUSES),
                },
            ).scalar()
            if previous is None:
                previous = entry_stop
        out.append(
            PmAction(
                proposal_id=str(pid),
                inst_id=inst_id,
                kind=kind,
                rule=rule or "",
                stop=stop,
                side=side,
                previous_stop=previous,
            )
        )
    return out


def _alerted(session: Session, env: str, day: date) -> list[str]:
    data = session.execute(
        select(AgentScore.data).where(AgentScore.agent == AGENT, AgentScore.env == env, AgentScore.day == day)
    ).scalar()
    if not isinstance(data, Mapping):
        return []
    alert = data.get("compliance_alert")
    if not isinstance(alert, Mapping):
        return []
    return sorted(str(v) for v in alert.get("proposal_ids", []))


def alert_text(day: date, violations: Sequence[Mapping[str, Any]], rate_value: float | None) -> str:
    pct = f"{rate_value * 100:.1f}%" if rate_value is not None else "n/a"
    lines = [f"**Portfolio Manager rule compliance on {day.isoformat()} was {pct}, below 100%.**"]
    for v in violations:
        lines.append(f"- {v['kind']} on {v['inst_id']} (proposal {v['proposal_id']}): {v['reason']}")
    return "\n".join(lines)


def _post_alert(ctx: ToolContext, day: date, stats: Mapping[str, Any]) -> None:
    violations = list(stats["violations"])
    detail = alert_text(day, violations, stats["compliance_rate"])
    card = {
        "code": ALERT_CODE,
        "day": day.isoformat(),
        "compliance_rate": stats["compliance_rate"],
        "violations": violations,
    }
    ctx.publish(
        SYSTEM_STREAM,
        EventEnvelope(
            type="system.alert",
            ts=ctx.now(),
            env=ctx.env,
            source=f"agent:{AGENT}",
            correlation_id=f"scoring:{AGENT}:{day.isoformat()}",
            payload={"detail": detail, **card},
        ),
    )
    ctx.publish(WARROOM_STREAM, message_envelope(ctx, kind="system_alert", content_md=detail, card=card))


# -- the job ----------------------------------------------------------------------------------------


def score_day(ctx: ToolContext, day: date) -> dict[str, Any] | None:
    """Score the Portfolio Manager on Baghdad ``day``; the row's data, or None (skipped)."""
    now = ctx.now()
    limit = float(Decimal(str(config()["loser_limit_r"])))
    with ctx.session_factory() as session:
        trades = closed_trades(session, ctx.env, day)
        actions = pm_actions(session, ctx.env, day)
        if not trades and not actions:
            log.info("scoring: no %s trades or proposals on %s (%s); skipped", AGENT, day, ctx.env)
            return None
        t_stats = trade_stats(trades, limit)
        c_stats = compliance(actions)
        violation_ids = sorted(str(v["proposal_id"]) for v in c_stats["violations"])
        already = _alerted(session, ctx.env, day)
        alert_needed = bool(violation_ids) and violation_ids != already
        data: dict[str, Any] = {
            **t_stats,
            **c_stats,
            "trades": [
                {
                    "journal_id": t.journal_id,
                    "inst_id": t.inst_id,
                    "outcome": t.outcome,
                    "realised_r": t.realised_r,
                    "mfe_r": t.mfe_r,
                }
                for t in trades
            ],
            "metrics": {
                "r_captured": t_stats["r_captured"],
                "losers_within_rate": t_stats["losers_within_rate"],
                "compliance_rate": c_stats["compliance_rate"],
            },
        }
        if violation_ids:
            data["compliance_alert"] = {
                "proposal_ids": violation_ids,
                "posted_at": now.isoformat() if alert_needed else None,
            }
            if not alert_needed:
                previous = session.execute(
                    select(AgentScore.data).where(
                        AgentScore.agent == AGENT, AgentScore.env == ctx.env, AgentScore.day == day
                    )
                ).scalar()
                if isinstance(previous, Mapping) and isinstance(previous.get("compliance_alert"), Mapping):
                    data["compliance_alert"] = dict(previous["compliance_alert"])
        scoring.upsert_score(
            session,
            agent=AGENT,
            env=ctx.env,
            day=day,
            n=t_stats["trades_n"],
            hit_rate=t_stats["losers_within_rate"],
            data=data,
            now=now,
        )
        session.commit()
    if alert_needed and ctx.replay_as_of is None:
        _post_alert(ctx, day, c_stats)
    return data


__all__ = [
    "AGENT",
    "ALERT_CODE",
    "RULE_KINDS",
    "ClosedTrade",
    "PmAction",
    "check_action",
    "compliance",
    "score_day",
    "trade_stats",
]
