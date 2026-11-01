"""How an eval case runs its agent (ATL-112).

The default driver sends the case prompt once through the supervisor, as the Historian's cases do.
An agent whose real work is a code pipeline around the model registers its own driver here, so the
eval scores what the owner would see:

* ``market_scout`` runs :func:`atlas_agents.agents.market_scout.run_watchlist` on the replayed day.
  Code builds the shortlist from the snapshot's ``feature_table`` research report, the teammates'
  replies come from the case (``inputs.replies``; an agent left out times out), the model answers
  the code-built prompt, and the code rules turn the answer into the watchlist card. The transcript
  is that card plus its War Room text, checked against the run's tool outputs, the inputs the
  prompt showed and the replies (the same pool the production number guard uses). A pick's rank
  and conviction (and conviction_before_cap) are the Scout's own judgments, not data it could have
  invented, so they join the pool too. The case prompt is only a label.

* ``technical_analyst`` runs :func:`atlas_agents.technical_analyst.analyse` for ``inputs.inst_id``
  (ATL-137): code builds the TA table from the snapshot, refuses on stale candles, the model answers
  the code-built prompt, and the code rules check the card. The transcript is the posted card (its
  ``meta`` moved to ``Transcript.meta``) and text plus anything the agent posted itself; the tool
  outputs are the run's plus the TA table and the numbers code derived (R per target, expected R,
  the corrections' figures) and the model's own quality_score. A stale-data refusal is scored as an
  ``ok`` run with ``meta.model_ran = False`` and the data alert as its card.

* ``strategist`` (ATL-166) runs :func:`atlas_agents.strategist.run.plan` for ``inputs.inst_id`` (or,
  with ``inputs.mode: revise``, sends the case prompt once so the model can revise
  ``inputs.proposal_id``). The transcript card is the TradeProposal part of the published proposal
  card; the code corrections, the analog anchor and the revised parent go to ``Transcript.meta``.
  The tool outputs are the run's plus the inputs code gathered and the Strategist's own
  judgments (its probability and size). A code "no trade" (no usable TechnicalSetup) is an ``ok``
  run with ``meta.model_ran = False``.

* ``derivatives_sentiment`` (ATL-138) runs :func:`atlas_agents.derivatives.report.analyse` for
  ``inputs.inst_id``: code computes the positioning features from the snapshot, the model answers the
  code-built prompt and the code rules check the card. The transcript card is the posted
  PositioningReport (``meta`` moved to ``Transcript.meta``); the tool outputs are the run's plus the
  features the model saw and the analyst's own judgments (crowding, squeeze risks, sentiment).

``inputs.replies`` in ``case.yaml``::

    inputs:
      replies:
        news_intelligence: {kind: text, content_md: "...", card: null}
        historian: {kind: research_report, content_md: "...", card: {...}}
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, cast

from atlas_shared.db import owner_day
from atlas_shared.events import EventEnvelope

from atlas_agents.evals import properties as props
from atlas_agents.evals.case import EvalCase
from atlas_agents.evals.transcript import transcript
from atlas_agents.registry import AgentHost
from atlas_agents.session import RunResult
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools import ToolContext
from atlas_agents.tools.ta_table import SOURCE as TA_TABLE_SOURCE
from atlas_agents.tools.ta_table import build_ta_table

Driver = Callable[[Supervisor, EvalCase], Awaitable[tuple[RunResult | None, props.Transcript]]]


async def default_driver(supervisor: Supervisor, case: EvalCase) -> tuple[RunResult | None, props.Transcript]:
    result = await supervisor.run(
        case.agent,
        case.prompt,
        trigger="eval",
        task=f"eval {case.case_id}",
        correlation_id=f"eval-{case.case_id}",
    )
    return result, transcript(result, case.prompt)


# -- market_scout ------------------------------------------------------------------------------------

# Card keys set by the runtime (ids, clocks, versions), not by the model or the rules being scored.
_RUNTIME_KEYS = frozenset(
    {
        "watchlist_id",
        "version",
        "date",
        "created_at",
        "edit_window_ends_at",
        "meta",
        "evidence",
        "feature_table_report_id",
        "feature_table_as_of",
        "correlation_id",
        "previous_version",
        "trigger",
    }
)


class _RecordingHost:
    """The supervisor as an AgentHost, remembering the Scout prompt and reviewer of the run."""

    def __init__(self, inner: Supervisor) -> None:
        self._inner = inner
        self.prompt: str | None = None
        self.reviewer: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def run(self, agent: str, prompt: str, **kwargs: Any) -> RunResult:
        self.prompt = prompt
        self.reviewer = kwargs.get("reviewer")
        return await self._inner.run(agent, prompt, **kwargs)


def _scripted_replies(case: EvalCase) -> Callable[[ToolContext, Mapping[str, str]], dict[str, Any]]:
    from atlas_agents.scout.teammates import Reply

    scripted: Mapping[str, Any] = case.inputs.get("replies") or {}

    def replies(ctx: ToolContext, asked: Mapping[str, str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for agent, mention_id in asked.items():
            body = scripted.get(agent)
            if not isinstance(body, Mapping):
                continue  # not scripted: the teammate timed out
            out[agent] = Reply(
                agent=agent,
                mention_id=mention_id,
                message_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{case.case_id}/{agent}")),
                replied_at=ctx.now(),
                kind=str(body.get("kind", "text")),
                content_md=str(body.get("content_md", "")),
                card=body.get("card") if isinstance(body.get("card"), Mapping) else None,
            )
        return out

    return replies


async def market_scout_driver(
    supervisor: Supervisor, case: EvalCase
) -> tuple[RunResult | None, props.Transcript]:
    from atlas_agents.agents import market_scout

    host = _RecordingHost(supervisor)
    assert case.replay_as_of is not None
    result = await market_scout.run_watchlist(
        cast(AgentHost, host),
        trigger="eval",
        correlation_id=f"eval-{case.case_id}",
        day=owner_day(case.replay_as_of),
        replies_from=_scripted_replies(case),
    )
    if result is None:
        return None, props.Transcript(
            status="error", error="the Scout model was not run (empty shortlist)", prompt=case.prompt
        )
    base = transcript(result, host.prompt or case.prompt)
    message = result.message or {}
    raw_card = message.get("card")
    card = (
        {k: v for k, v in raw_card.items() if k not in _RUNTIME_KEYS}
        if isinstance(raw_card, Mapping)
        else None
    )
    outputs = list(base.tool_outputs)
    inputs = getattr(host.reviewer, "inputs", None)
    if inputs is not None:
        outputs.append(inputs.tool_output())
        for reply in inputs.replies.values():
            outputs.append(
                {
                    "as_of": None,
                    "source": f"warroom:{reply.agent}",
                    "data": {"content_md": reply.content_md, "card": reply.card},
                }
            )
    if card is not None:
        judged = [
            {k: pick.get(k) for k in ("rank", "conviction", "conviction_before_cap") if k in pick}
            for pick in card.get("picks") or []
            if isinstance(pick, Mapping)
        ]
        outputs.append({"as_of": None, "source": "agent:market_scout judgments", "data": judged})
    return result, props.Transcript(
        status=result.status,
        text=str(message.get("content_md", "")),
        card=card,
        tool_calls=base.tool_calls,
        tool_outputs=outputs,
        error=result.error,
        prompt=base.prompt,
    )


# -- technical_analyst -------------------------------------------------------------------------------


def _derived(card: Mapping[str, Any], corrections: list[Any]) -> dict[str, Any]:
    """Numbers code computed from the card (or the model judged), for the number guard's pool."""
    zone = card.get("entry_zone")
    return {
        "as_of": None,
        "source": "code:technical_analyst checks",
        "data": {
            "entry_mid": [str((Decimal(str(zone[0])) + Decimal(str(zone[1]))) / 2)] if zone else [],
            "r_multiples": [t.get("r_multiple") for t in card.get("targets") or [] if isinstance(t, Mapping)],
            "expected_r": card.get("expected_r"),
            "est_slippage_bps": card.get("est_slippage_bps"),
            "quality_score": card.get("quality_score"),
            "corrections": corrections,
        },
    }


async def technical_analyst_driver(
    supervisor: Supervisor, case: EvalCase
) -> tuple[RunResult | None, props.Transcript]:
    from atlas_agents import technical_analyst as ta_agent

    inst_id = str(case.inputs["inst_id"])
    why = str(case.inputs.get("why") or f"Eval {case.case_id}: analyse {inst_id}.")
    host = _RecordingHost(supervisor)
    result = await ta_agent.analyse(
        cast(AgentHost, host), inst_id, trigger="eval", correlation_id=f"eval-{case.case_id}", why=why
    )
    if result is None:
        if host.prompt is not None:
            return None, props.Transcript(
                status="error", error="the run returned nothing", prompt=case.prompt
            )
        table, _ = await build_ta_table(supervisor.tool_context(ta_agent.SLUG), inst_id)
        if not table.get("stale"):
            return None, props.Transcript(
                status="error", error=f"{inst_id} was not analysed", prompt=case.prompt
            )
        alert = ta_agent.data_alert(inst_id, table, why)
        refused = RunResult(run_id=uuid.uuid5(uuid.NAMESPACE_URL, f"eval/{case.case_id}"), status="ok")
        return refused, props.Transcript(
            status="ok",
            text=str(alert["content_md"]),
            card=dict(alert["card"]),
            tool_outputs=[{"as_of": table.get("data_as_of"), "source": TA_TABLE_SOURCE, "data": table}],
            prompt=case.prompt,
            meta={"model_ran": False},
        )
    base = transcript(result, host.prompt or case.prompt)
    message = result.message or {}
    raw_card = message.get("card")
    card = dict(raw_card) if isinstance(raw_card, Mapping) else None
    meta: dict[str, Any] = {"model_ran": True}
    outputs = list(base.tool_outputs)
    reviewer = host.reviewer
    if isinstance(reviewer, ta_agent.SetupReviewer):
        outputs.append(
            {"as_of": reviewer.table.get("data_as_of"), "source": TA_TABLE_SOURCE, "data": reviewer.table}
        )
    if card is not None:
        card_meta = card.pop("meta", None) or {}
        meta["corrections"] = list(card_meta.get("corrections") or [])
        meta["unmatched_numbers"] = list(card_meta.get("unmatched_numbers") or [])
        outputs.append(_derived(card, meta["corrections"]))
    posted = [
        str(args.get("content_md", ""))
        for name, args in base.tool_calls
        if name in props.POST_TOOLS and args.get("content_md")
    ]
    text = "\n\n".join([str(message.get("content_md", "")), *posted]).strip()
    return result, props.Transcript(
        status=result.status,
        text=text,
        card=card,
        tool_calls=base.tool_calls,
        tool_outputs=outputs,
        error=result.error,
        prompt=base.prompt,
        meta=meta,
    )


# -- strategist --------------------------------------------------------------------------------------

# The proposal card's fields that are a TradeProposal; the rest (ids, versions, statuses, clocks, the
# code checklist and the evidence ids) is the runtime's and goes to Transcript.meta.
_PROPOSAL_FIELDS = (
    "inst_id",
    "side",
    "order_type",
    "entry",
    "entry_zone",
    "stop",
    "take_profits",
    "time_stop_minutes",
    "size_suggestion_pct_equity",
    "leverage_suggestion",
    "expected_r_multiple",
    "probability_estimate",
    "rationale_md",
    "invalidation",
)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


def _proposal_cards(recorder: Any, since: int) -> list[dict[str, Any]]:
    """Proposal cards published on atlas.warroom after event ``since`` of the case recorder."""
    events = list(getattr(recorder, "events", []))[since:]
    return [
        dict(env.payload["card"])
        for stream, env in events
        if stream == "atlas.warroom"
        and env.payload.get("kind") == "proposal"
        and isinstance(env.payload.get("card"), Mapping)
    ]


def _parent(supervisor: Supervisor, proposal_id: str) -> dict[str, Any] | None:
    from atlas_shared.db import Proposal

    try:
        wanted = uuid.UUID(proposal_id)
    except ValueError:
        return None
    with supervisor.tool_context("strategist").session_factory() as session:
        row = session.get(Proposal, wanted)
        if row is None:
            return None
        return {
            "proposal_id": str(row.id),
            "side": row.side,
            "stop": None if row.stop is None else format(row.stop.normalize(), "f"),
            "entry": None if row.entry is None else format(row.entry.normalize(), "f"),
            "entry_zone": row.entry_zone,
            "version": row.version,
        }


def _strategist_card(raw: Mapping[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    card_meta = dict(raw.get("meta") or {})
    meta["corrections"] = list(card_meta.get("corrections") or [])
    meta["analog_hit_rate"] = card_meta.get("analog_hit_rate")
    meta["sizing"] = card_meta.get("sizing")
    meta["proposal"] = {
        k: raw.get(k) for k in ("proposal_id", "version", "parent_id", "status", "expectancy_r", "checklist")
    }
    meta["summary_md"] = raw.get("summary_md")
    return {k: raw.get(k) for k in _PROPOSAL_FIELDS if raw.get(k) is not None}


def _judgments(card: Mapping[str, Any], corrections: list[Any], expectancy: Any) -> dict[str, Any]:
    """The Strategist's own judgments (its probability and any size it cut to) and what code
    derived from the card, for the number guard's pool, as market_scout's driver does for ranks."""
    zone = card.get("entry_zone")
    model_p = [
        c.get("model")
        for c in corrections
        if isinstance(c, Mapping) and c.get("rule") == "probability_clamped"
    ]
    return {
        "as_of": None,
        "source": "agent:strategist judgments",
        "data": {
            "probability_estimate": card.get("probability_estimate"),
            "probability_model": model_p,
            "size_suggestion_pct_equity": card.get("size_suggestion_pct_equity"),
            "expectancy_r": expectancy,
            "entry_mid": [str((Decimal(str(zone[0])) + Decimal(str(zone[1]))) / 2)] if zone else [],
            "corrections": corrections,
        },
    }


async def strategist_driver(
    supervisor: Supervisor, case: EvalCase
) -> tuple[RunResult | None, props.Transcript]:
    """``inputs.mode`` ``plan`` (default): :func:`atlas_agents.strategist.run.plan` for
    ``inputs.inst_id`` — code gathers the inputs, a missing/none/stale TechnicalSetup is a code
    "no trade" with no model run, else the model answers and code submits its decision block.
    ``revise``: the case prompt (a Devil's Advocate objection on ``inputs.proposal_id``, a v1 in the
    snapshot) goes to the Strategist once, and the model may revise through submit_proposal.

    The transcript card is the TradeProposal part of the proposal card the run published (its
    runtime fields, code corrections and analog anchor in ``Transcript.meta``); for a revision
    ``meta.parent`` is the revised proposal. The tool outputs are the run's plus the inputs code
    gathered and the Strategist's own judgments."""
    from atlas_agents.strategist import inputs as inp
    from atlas_agents.strategist import run as strategist_run

    inst_id = str(case.inputs["inst_id"])
    mode = str(case.inputs.get("mode", "plan"))
    why = str(case.inputs.get("why") or f"Eval {case.case_id}: plan {inst_id}.")
    recorder = getattr(supervisor, "_publish", None)
    since = len(getattr(recorder, "events", []))
    ctx = supervisor.tool_context(strategist_run.SLUG)
    with ctx.session_factory() as session:
        gathered = inp.gather(session, ctx, inst_id)
    inputs_output = {
        "as_of": gathered.as_of.isoformat(),
        "source": "code:strategist inputs",
        "data": gathered.to_json(),
    }
    meta: dict[str, Any] = {"model_ran": True, "mode": mode}

    host = _RecordingHost(supervisor)
    if mode == "revise":
        parent_id = str(case.inputs["proposal_id"])
        meta["parent"] = _parent(supervisor, parent_id)
        result: RunResult | None = await supervisor.run(
            strategist_run.SLUG,
            case.prompt,
            trigger="eval",
            task=f"eval {case.case_id}",
            correlation_id=f"eval-{case.case_id}",
        )
    else:
        result = await strategist_run.plan(
            cast(AgentHost, host), inst_id, trigger="eval", correlation_id=f"eval-{case.case_id}", why=why
        )
    if result is None:
        problem = strategist_run.setup_problem(gathered)
        if host.prompt is not None or problem is None:
            return None, props.Transcript(
                status="error", error="the run returned nothing", prompt=case.prompt
            )
        body = strategist_run.no_trade_message(inst_id, [problem], reply_to=None, thread_id=None)
        refused = RunResult(run_id=uuid.uuid5(uuid.NAMESPACE_URL, f"eval/{case.case_id}"), status="ok")
        meta["model_ran"] = False
        return refused, props.Transcript(
            status="ok",
            text=str(body["content_md"]),
            card=None,
            tool_outputs=[inputs_output],
            prompt=case.prompt,
            meta=meta,
        )

    base = transcript(result, host.prompt or case.prompt)
    message = result.message or {}
    outputs = [*base.tool_outputs, inputs_output]
    cards = _proposal_cards(recorder, since)
    card: dict[str, Any] | None = None
    if cards:
        raw = cards[-1]
        card = _strategist_card(raw, meta)
        outputs.append(_judgments(card, meta["corrections"], raw.get("expectancy_r")))
        if mode == "plan" and raw.get("parent_id"):
            meta["parent"] = _parent(supervisor, str(raw["parent_id"]))
    posted = [
        str(args.get("content_md", ""))
        for name, args in base.tool_calls
        if name in props.POST_TOOLS and args.get("content_md")
    ]
    parts = [
        str(message.get("content_md", "")) if mode == "plan" else base.text,
        *(posted if mode == "plan" else []),
    ]
    if meta.get("summary_md"):
        parts.append(str(meta["summary_md"]))
    text = _UUID.sub("<id>", "\n\n".join(p for p in parts if p).strip())
    return result, props.Transcript(
        status=result.status,
        text=text,
        card=card,
        tool_calls=base.tool_calls,
        tool_outputs=outputs,
        error=result.error,
        prompt=base.prompt,
        meta=meta,
    )


# -- derivatives_sentiment ---------------------------------------------------------------------------


def _positioning_judgments(card: Mapping[str, Any]) -> dict[str, Any]:
    """The analyst's own judgments on the card (crowding, squeeze risks, sentiment): numbers the
    model chose, not data it could invent, so they join the number guard's pool."""
    return {
        "as_of": None,
        "source": "agent:derivatives_sentiment judgments",
        "data": {
            "crowding_score": card.get("crowding_score"),
            "squeeze_risk": card.get("squeeze_risk"),
            "sentiment_score": card.get("sentiment_score"),
        },
    }


async def derivatives_sentiment_driver(
    supervisor: Supervisor, case: EvalCase
) -> tuple[RunResult | None, props.Transcript]:
    """``inputs.inst_id`` goes through the production :func:`atlas_agents.derivatives.report.analyse`
    (ATL-138): code computes the features from the snapshot, the model answers the code-built
    prompt and :class:`ReportReviewer` applies the code rules. The transcript card is the posted
    PositioningReport (its ``meta`` moved to ``Transcript.meta``); the text is the posted
    ``content_md`` (code notes included) plus anything the agent posted itself; the tool outputs are
    the run's plus the features the model saw and the analyst's own judgments."""
    from atlas_agents.derivatives import report

    inst_id = str(case.inputs["inst_id"])
    why = str(case.inputs.get("why") or f"Eval {case.case_id}: positioning of {inst_id}.")
    host = _RecordingHost(supervisor)
    result = await report.analyse(
        cast(AgentHost, host), inst_id, trigger="eval", correlation_id=f"eval-{case.case_id}", why=why
    )
    if result is None:
        return None, props.Transcript(status="error", error=f"{inst_id} was not analysed", prompt=case.prompt)
    base = transcript(result, host.prompt or case.prompt)
    message = result.message or {}
    raw_card = message.get("card")
    card = dict(raw_card) if isinstance(raw_card, Mapping) else None
    meta: dict[str, Any] = {"model_ran": True}
    outputs = list(base.tool_outputs)
    reviewer = host.reviewer
    if isinstance(reviewer, report.ReportReviewer):
        features = reviewer.features
        outputs.append(
            {
                "as_of": features.data_as_of.isoformat(),
                "source": props.POSITIONING_FEATURES,
                "data": features.model_dump(mode="json"),
            }
        )
    if card is not None:
        card_meta = card.pop("meta", None) or {}
        meta["corrections"] = list(card_meta.get("corrections") or [])
        meta["unmatched_numbers"] = list(card_meta.get("unmatched_numbers") or [])
        outputs.append(_positioning_judgments(card))
    posted = [
        str(args.get("content_md", ""))
        for name, args in base.tool_calls
        if name in props.POST_TOOLS and args.get("content_md")
    ]
    meta["posted"] = posted
    text = "\n\n".join([str(message.get("content_md", "")), *posted]).strip()
    return result, props.Transcript(
        status=result.status,
        text=text,
        card=card,
        tool_calls=base.tool_calls,
        tool_outputs=outputs,
        error=result.error,
        prompt=base.prompt,
        meta=meta,
    )


# -- portfolio_manager -------------------------------------------------------------------------------

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


DRIVERS: dict[str, Driver] = {
    "market_scout": market_scout_driver,
    "technical_analyst": technical_analyst_driver,
    "strategist": strategist_driver,
    "derivatives_sentiment": derivatives_sentiment_driver,
    "portfolio_manager": portfolio_manager_driver,
}


def driver_for(agent: str) -> Driver:
    return DRIVERS.get(agent, default_driver)


__all__ = [
    "DRIVERS",
    "Driver",
    "default_driver",
    "derivatives_sentiment_driver",
    "driver_for",
    "market_scout_driver",
    "portfolio_manager_driver",
    "technical_analyst_driver",
]
