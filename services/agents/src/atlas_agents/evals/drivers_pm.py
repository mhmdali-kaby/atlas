"""Portfolio Manager eval driver (ATL-171, brief §7.8).

Runs one PM tick (or the 23:30 daily report) through the real PM code against a replayed day and puts
in ``Transcript.meta`` what the rules computed, every block the model wrote, what code refused or
submitted itself, the proposals saved, the War Room messages and each position's side, stop and mark.
The scoring lives in ``properties_pm``. Importing this module registers the driver in
``drivers.DRIVERS``; ``runner`` imports it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any, Final, cast

from atlas_shared.events import EventEnvelope

from atlas_agents.evals import properties as props
from atlas_agents.evals.case import EvalCase
from atlas_agents.evals.drivers import DRIVERS, _RecordingHost
from atlas_agents.evals.transcript import transcript
from atlas_agents.registry import AgentHost
from atlas_agents.session import RunResult
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools import ToolContext

_PM: Final = "portfolio_manager"


@dataclasses.dataclass
class _PMRun:
    prompt: str
    reviewer: Any
    result: RunResult | None = None


class _PMHost(_RecordingHost):
    """The supervisor as the PM's AgentHost, remembering every run (prompt, reviewer, result)."""

    def __init__(self, inner: Supervisor) -> None:
        super().__init__(inner)
        self.runs: list[_PMRun] = []

    async def run(self, agent: str, prompt: str, **kwargs: Any) -> RunResult:
        entry = _PMRun(prompt=prompt, reviewer=kwargs.get("reviewer"))
        self.runs.append(entry)
        entry.result = await self._inner.run(agent, prompt, **kwargs)
        return entry.result


class _EvalRisk:
    """trading_core's /internal/risk/evaluate for an eval: stores an approved risk check and moves
    the proposal to risk_checked, so the PM's routing runs on to the owner as in production without
    a network call. What happens after the Risk Engine is not what these cases score."""

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self.calls: list[str] = []

    async def risk_evaluate(self, proposal_id: str) -> dict[str, Any]:
        from atlas_shared.db import Proposal, RiskCheck
        from sqlalchemy import update

        self.calls.append(proposal_id)
        rid = uuid.uuid5(uuid.NAMESPACE_URL, f"eval-risk/{proposal_id}")
        now = self.ctx.now()
        with self.ctx.session_factory() as session:
            session.add(
                RiskCheck(
                    id=rid,
                    env=self.ctx.env,
                    proposal_id=uuid.UUID(proposal_id),
                    settings_version=1,
                    result="approved",
                    checks=[],
                    decision="awaiting_owner",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.execute(
                update(Proposal).where(Proposal.id == uuid.UUID(proposal_id)).values(status="risk_checked")
            )
            session.commit()
        return {
            "ok": True,
            "proposal_id": proposal_id,
            "risk_check_id": str(rid),
            "result": "approved",
            "decision": "awaiting_owner",
            "reason": "position action (eval)",
            "checks": [],
        }


def _plain(value: Any) -> str | None:
    return None if value is None else format(value, "f")


def _pm_action(action: Any) -> dict[str, Any]:
    from atlas_agents.pm import guard as pm_guard

    return {
        "kind": action.kind,
        "rule": pm_guard.rule_label(action),
        "stop": _plain(action.stop),
        "close_pct": _plain(action.close_pct),
    }


def _model_blocks(result: RunResult | None) -> list[dict[str, Any]]:
    """Every JSON block the model wrote in the run, in order (one per attempt)."""
    from claude_agent_sdk import AssistantMessage, TextBlock

    from atlas_agents.historian import split_card

    out: list[dict[str, Any]] = []
    for message in result.messages if result is not None else []:
        if not isinstance(message, AssistantMessage):
            continue
        for block in message.content:
            if isinstance(block, TextBlock):
                _content, card, _error = split_card(block.text)
                if card is not None:
                    out.append(card)
    return out


def _structure_plan(position: Any, now: datetime, raw: Mapping[str, Any]) -> Any:
    """:func:`manage.plan_for` with the Technical Analyst's structure report from the case
    (``inputs.structure``). TechnicalSetup has no intact/weakening/broken field yet (ATL-168's gap),
    so production cannot load one; the invalidation rule is driven from the case instead."""
    from atlas_agents.pm import manage
    from atlas_agents.pm.rules import PROPOSAL_KINDS, StructureReport, evaluate_full

    as_of = now - timedelta(minutes=int(raw.get("age_minutes", 0)))
    structure = StructureReport(status=raw["status"], as_of=as_of, source_id=raw.get("source_id"))
    ev = evaluate_full(
        position.state,
        position.plan,
        position.atr_1h,
        position.last_swing,
        position.events,
        position.funding,
        now,
        position.settings,
        structure,
    )
    actions = tuple(
        a for a in ev.actions if a.kind in PROPOSAL_KINDS and a.kind not in position.pending_kinds
    )
    advisories = tuple(a for a in ev.actions if a.kind not in PROPOSAL_KINDS)
    return manage.Plan(position=position, evaluation=ev, actions=actions, advisories=advisories)


def _pm_proposals(supervisor: Supervisor, case: EvalCase) -> list[dict[str, Any]]:
    """The Portfolio Manager proposals the run saved (the snapshot's own rows left out)."""
    from atlas_shared.db import Proposal
    from sqlalchemy import select

    known = {str(r.get("id")) for r in case.snapshot().get("proposals", [])}
    ctx = supervisor.tool_context(_PM)
    with ctx.session_factory() as session:
        rows = session.scalars(
            select(Proposal)
            .where(Proposal.env == ctx.env, Proposal.author == _PM)
            .order_by(Proposal.created_at, Proposal.id)
        ).all()
        return [
            {
                "id": str(r.id),
                "kind": r.kind,
                "rule": r.invalidation,
                "stop": _plain(r.stop),
                "close_pct": _plain(r.close_pct),
                "status": r.status,
                "inst_id": r.inst_id,
            }
            for r in rows
            if str(r.id) not in known
        ]


async def _pm_report(
    supervisor: Supervisor, case: EvalCase, host: _PMHost
) -> tuple[RunResult | None, props.Transcript]:
    from atlas_agents.pm import daily_report
    from atlas_agents.registry import ScheduleFire

    ctx = supervisor.tool_context(_PM)
    day = date.fromisoformat(str(case.inputs["day"]))
    fire = ScheduleFire(name=daily_report.JOB, at=ctx.now(), day=day, correlation_id=f"eval-{case.case_id}")
    result = await daily_report.run(cast(AgentHost, host), fire)
    run = host.runs[-1] if host.runs else None
    reviewer = run.reviewer if run is not None else None
    if result is None or run is None or not isinstance(reviewer, daily_report.ReportReviewer):
        return None, props.Transcript(
            status="error", error="the daily report did not run", prompt=case.prompt
        )
    code_card = reviewer.report.card
    posted = dict(reviewer.card) if reviewer.card is not None else None
    posted_meta = posted.pop("meta", None) if posted is not None else None
    meta: dict[str, Any] = {
        "model_ran": True,
        "mode": "report",
        "code_card": code_card,
        "model_blocks": _model_blocks(result),
        "posted_meta": posted_meta,
    }
    base = transcript(result, run.prompt)
    return result, props.Transcript(
        status=result.status,
        text=reviewer.content or "",
        card=posted,
        tool_calls=base.tool_calls,
        tool_outputs=[
            *base.tool_outputs,
            {"as_of": code_card.get("as_of"), "source": "atlas.daily_report_code", "data": code_card},
        ],
        error=result.error,
        prompt=run.prompt,
        meta=meta,
    )


async def portfolio_manager_driver(
    supervisor: Supervisor, case: EvalCase
) -> tuple[RunResult | None, props.Transcript]:
    """The Portfolio Manager's production paths on the replayed account (ATL-171):

    * ``inputs.mode: check`` (default) — one 5-minute :func:`atlas_agents.pm.manage.tick`: code loads
      the positions and runs the rules; no rule firing means no model call and no message. With
      ``inputs.structure`` (``{status, age_minutes}``) the case supplies the Technical Analyst's
      structure report the DB cannot hold yet, and each position runs through :func:`manage.manage`.
    * ``alert`` — ``inputs.alert`` (a news.alert payload) goes to the PM's news hook as the bus would
      deliver it, and the run it starts is awaited.
    * ``report`` — the 23:30 daily report for ``inputs.day``.

    The transcript text is the model's own text. ``meta`` holds what the rules computed
    (``computed``, ``fired``), every JSON block the model wrote (``model_blocks``), what code
    refused or had to submit itself, the proposals saved, the War Room messages, and each position's
    side, stop and mark the actions are judged against."""
    from atlas_agents.agents import portfolio_manager as hooks
    from atlas_agents.pm import manage

    mode = str(case.inputs.get("mode", "check"))
    recorder = getattr(supervisor, "_publish", None)
    published: list[Any] = getattr(recorder, "events", [])
    since = len(published)
    host = _PMHost(supervisor)
    ctx = supervisor.tool_context(_PM)
    risk = _EvalRisk(ctx)
    manage.reset()
    manage.set_risk_client(cast(Any, risk))
    plans: list[Any] = []
    try:
        if mode == "report":
            return await _pm_report(supervisor, case, host)
        held, _usage = await asyncio.to_thread(manage._load, cast(AgentHost, host))
        now = ctx.now()
        structure = case.inputs.get("structure")
        plans = [
            _structure_plan(p, now, structure) if isinstance(structure, Mapping) else manage.plan_for(p, now)
            for p in held
        ]
        if mode == "alert":
            alert = EventEnvelope(
                type=hooks.NEWS_ALERT,
                ts=now,
                env=case.env,
                source="ingest",
                correlation_id=f"eval-{case.case_id}",
                payload=dict(case.inputs["alert"]),
            )
            await hooks.on_news_alert(cast(AgentHost, host), alert)
            await manage.settled()
        elif isinstance(structure, Mapping):
            for plan in plans:
                if not plan.actions:
                    continue  # as tick: no proposal rule, no model call
                await manage.manage(
                    cast(AgentHost, host),
                    plan,
                    why="; ".join(f"{a.rule}: {a.reason}" for a in plan.actions) or "structure report",
                    trigger="schedule",
                    correlation_id=f"eval-{case.case_id}:{plan.position.inst_id}:rule",
                )
        else:
            await manage.tick(cast(AgentHost, host), correlation_prefix=f"eval-{case.case_id}")
    finally:
        manage.set_risk_client(None)
        manage.reset()

    messages = [
        {"kind": evt.payload.get("kind"), "content_md": evt.payload.get("content_md")}
        for stream, evt in published[since:]
        if stream == "atlas.warroom" and evt.type == "warroom.message"
    ]
    blocks: list[dict[str, Any]] = []
    refusals: list[str] = []
    texts: list[str] = []
    calls: list[tuple[str, dict[str, Any]]] = []
    outputs: list[dict[str, Any]] = []
    status, error = "ok", None
    for run in host.runs:
        blocks.extend(_model_blocks(run.result))
        refusals.extend(getattr(run.reviewer, "refusals", None) or [])
        if run.result is None:
            continue
        base = transcript(run.result, run.prompt)
        texts.append(base.text)
        calls.extend(base.tool_calls)
        outputs.extend(base.tool_outputs)
        if run.result.status != "ok":
            status, error = run.result.status, run.result.error
    meta: dict[str, Any] = {
        "mode": mode,
        "model_ran": bool(host.runs),
        "computed": [_pm_action(a) for p in plans for a in p.actions],
        "fired": sorted({a.rule for p in plans for a in (*p.actions, *p.advisories)}),
        "dropped": [{"rule": d.rule, "reason": d.reason} for p in plans for d in p.evaluation.dropped],
        "model_blocks": blocks,
        "refusals": refusals,
        "code_submitted": sum(1 for m in messages if "(submitted by code" in str(m.get("content_md"))),
        "proposals": await asyncio.to_thread(_pm_proposals, supervisor, case),
        "messages": messages,
        "positions": [
            {
                "inst_id": p.position.inst_id,
                "side": p.position.side,
                "current_stop": _plain(p.position.state.stop),
                "mark": _plain(p.position.state.mark_price),
                "r_multiple": _plain(p.evaluation.r_multiple),
            }
            for p in plans
        ],
        "risk_calls": len(risk.calls),
    }
    synthetic = RunResult(run_id=uuid.uuid5(uuid.NAMESPACE_URL, f"eval/{case.case_id}"), status="ok")
    last = next((r.result for r in reversed(host.runs) if r.result is not None), None) or synthetic
    return last, props.Transcript(
        status=status,
        text="\n\n".join(t for t in texts if t),
        card=blocks[-1] if blocks else None,
        tool_calls=calls,
        tool_outputs=outputs,
        error=error,
        prompt=host.runs[-1].prompt if host.runs else case.prompt,
        meta=meta,
    )


DRIVERS[_PM] = portfolio_manager_driver

__all__ = ["portfolio_manager_driver"]
