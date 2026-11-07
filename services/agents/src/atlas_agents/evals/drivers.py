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
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from typing import Any, cast

from atlas_shared.db import owner_day

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


# -- devils_advocate ---------------------------------------------------------------------------------


def _warroom_payloads(recorder: Any, since: int, author: str) -> list[dict[str, Any]]:
    """``warroom.message`` payloads by ``author`` published on atlas.warroom after event ``since``."""
    events = list(getattr(recorder, "events", []))[since:]
    return [
        dict(env.payload)
        for stream, env in events
        if stream == "atlas.warroom" and env.type == "warroom.message" and env.payload.get("author") == author
    ]


class _DevilsHost(_RecordingHost):
    """Records every card rejection of the Devil's Advocate's reviewer, so a case whose card fails
    validation reports why (``checklist items missing: 4 (stop_quality)``) and not only that the
    scripted run had no second answer for the retry."""

    def __init__(self, inner: Supervisor) -> None:
        super().__init__(inner)
        self.rejections: list[str] = []
        self.results: list[RunResult] = []

    async def run(self, agent: str, prompt: str, **kwargs: Any) -> RunResult:
        inner_reviewer = kwargs.get("reviewer")
        if inner_reviewer is not None:
            rejections = self.rejections

            async def recording(text: str, messages: list[Any], attempt: int) -> Any:
                verdict = await inner_reviewer(text, messages, attempt)
                if verdict.failure is not None:
                    rejections.append(str(verdict.failure))
                elif verdict.follow_up is not None:
                    first = str(verdict.follow_up).split("\n", 1)[0]
                    rejections.append(first.removeprefix("Your DevilsReview card was not accepted: "))
                return verdict

            kwargs["reviewer"] = recording
        self.prompt = prompt
        self.reviewer = inner_reviewer
        result = await self._inner.run(agent, prompt, **kwargs)
        self.results.append(result)
        return result


def _da_judgments(card: Mapping[str, Any]) -> dict[str, Any]:
    """The Devil's Advocate's own judgments (item numbers, severities' rating, confidence) for the
    number guard's pool, as the other drivers add their agent's ranks and probabilities."""
    return {
        "as_of": None,
        "source": "agent:devils_advocate judgments",
        "data": {
            "items": list(range(1, 11)),
            "risk_rating": card.get("risk_rating"),
            "rating_scale": 5,
            "confidence": card.get("confidence"),
            "version": card.get("version"),
            "round": card.get("round"),
        },
    }


async def devils_advocate_driver(
    supervisor: Supervisor, case: EvalCase
) -> tuple[RunResult | None, props.Transcript]:
    """``inputs.mode`` ``review`` (default): :func:`atlas_agents.devils_advocate.review` of
    ``inputs.proposal_id`` (a proposal in the snapshot, its War Room card ``inputs.message_id``):
    code builds the precomputed table, the model answers, code validates the card, applies the
    rating guide and vote rules, casts the vote and posts the card.

    ``rerate``: the Strategist's reply goes through the production hook
    :func:`atlas_agents.agents.devils_advocate.on_strategist_reply` once for every entry of
    ``inputs.replies`` (``{reply_to, content_md}``; ``reply_to`` is a stored War Room message row
    id), and the driver waits for the background re-rates. ``meta.rerate_cards`` counts the round-2
    cards published; the transcript card is the first of them.

    The transcript card is the posted ``da_review`` without its runtime parts: ``precomputed``,
    ``corrections`` and ``meta`` go to ``Transcript.meta``. The tool outputs are the run's plus the
    precomputed table, the proposal the model saw and its own judgments."""
    from atlas_shared.events import EventEnvelope

    from atlas_agents import devils_advocate as da
    from atlas_agents.agents import devils_advocate as da_hooks

    mode = str(case.inputs.get("mode", "review"))
    recorder = getattr(supervisor, "_publish", None)
    since = len(getattr(recorder, "events", []))
    host = _DevilsHost(supervisor)
    meta: dict[str, Any] = {"mode": mode}
    result: RunResult | None = None
    if mode == "rerate":
        ctx = supervisor.tool_context(da.SLUG)
        for reply in case.inputs.get("replies") or []:
            reviewed = da.message_row(ctx, str(reply["reply_to"]))
            card_ = (reviewed or {}).get("card") or {}
            # The hook's in-process guard is per (env, proposal); a re-run of this case starts clean.
            da_hooks._rerated.discard((supervisor.env, str(card_.get("proposal_id"))))
        for index, reply in enumerate(case.inputs.get("replies") or []):
            envelope = EventEnvelope(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"eval/{case.case_id}/reply/{index}"),
                type="warroom.message",
                ts=ctx.now(),
                env=supervisor.env,
                source="agent:strategist",
                correlation_id=f"eval-{case.case_id}",
                payload={
                    "author_type": "agent",
                    "author": da.STRATEGIST,
                    "kind": "text",
                    "content_md": str(reply.get("content_md", "")),
                    "reply_to": str(reply["reply_to"]),
                },
            )
            await da_hooks.on_strategist_reply(cast(AgentHost, host), envelope)
            await da_hooks.settled()
    else:
        result = await da.review(
            cast(AgentHost, host),
            str(case.inputs["proposal_id"]),
            message_id=case.inputs.get("message_id"),
            correlation_id=f"eval-{case.case_id}",
            trigger="eval",
        )
        if result is None:
            return None, props.Transcript(
                status="error",
                error="the proposal was not reviewed (unknown or not open)",
                prompt=case.prompt,
            )
    posted = _warroom_payloads(recorder, since, da.SLUG)
    # A run's final War Room message is the RunResult's ``message`` (the supervisor posts it).
    posted += [dict(r.message, author=da.SLUG) for r in host.results if r.status == "ok" and r.message]
    cards = [
        dict(p["card"])
        for p in posted
        if isinstance(p.get("card"), Mapping) and p["card"].get("type") == "da_review"
    ]
    meta["model_runs"] = len(host.results)
    meta["rejections"] = list(host.rejections)
    meta["rerate_cards"] = sum(1 for c in cards if c.get("round") == 2)
    meta["review_cards"] = len(cards)
    meta["posted"] = posted
    if mode == "rerate":
        # The hooks run in the background: the case's run is the first re-rate, or none at all
        # (an ``ok`` run with ``meta.model_runs = 0``).
        result = (
            host.results[0]
            if host.results
            else RunResult(run_id=uuid.uuid5(uuid.NAMESPACE_URL, f"eval/{case.case_id}"), status="ok")
        )
    assert result is not None
    if result.status != "ok" and host.rejections:
        result.error = f"{result.error or result.status}; card rejected: {' | '.join(host.rejections)}"
    base = transcript(result, host.prompt or case.prompt)
    outputs = list(base.tool_outputs)
    inputs = getattr(host.reviewer, "inputs", None)
    row = getattr(host.reviewer, "row", None)
    if isinstance(inputs, Mapping):
        outputs.append({"as_of": inputs.get("as_of"), "source": da.SOURCE, "data": dict(inputs)})
    if row is not None:
        outputs.append({"as_of": None, "source": "postgres:proposals", "data": da._proposal_view(row)})
    card: dict[str, Any] | None = None
    text = ""
    if cards:
        raw = cards[0]
        meta["corrections"] = list(raw.pop("corrections", None) or [])
        meta["precomputed"] = raw.pop("precomputed", None)
        meta["card_meta"] = raw.pop("meta", None)
        card = raw
        outputs.append(_da_judgments(card))
        text = str(
            next(p for p in posted if p.get("card") and p["card"].get("type") == "da_review")["content_md"]
        )
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


# -- orchestrator ------------------------------------------------------------------------------------


def _settings_version(supervisor: Supervisor) -> int | None:
    from sqlalchemy import text as sql

    with supervisor.tool_context("orchestrator").session_factory() as session:
        found = session.execute(
            sql("SELECT version FROM settings WHERE env = :env"), {"env": supervisor.env}
        ).first()
    return None if found is None else int(found[0])


def _proposal_statuses(supervisor: Supervisor) -> dict[str, str]:
    from atlas_shared.db import Proposal
    from sqlalchemy import select

    with supervisor.tool_context("orchestrator").session_factory() as session:
        rows = session.execute(
            select(Proposal.id, Proposal.status).where(Proposal.env == supervisor.env)
        ).all()
    return {str(r[0]): str(r[1]) for r in rows}


_LINKS_LINE = re.compile(r"(?m)^Cards: \[.*$")


async def orchestrator_driver(
    supervisor: Supervisor, case: EvalCase
) -> tuple[RunResult | None, props.Transcript]:
    """``inputs.mode`` ``owner`` (default): ``inputs.message`` is an owner War Room message (its row
    ``inputs.message_row`` and thread ``inputs.thread_id`` in the snapshot). It takes the production
    steps the hook takes outside replay (``take_owner_message`` returns early while replaying):
    ``/why <id>`` goes to :func:`atlas_agents.orchestrator.why.answer_why`; anything else to
    :func:`atlas_agents.orchestrator.owner.handle_owner_message` (code classification and the
    acknowledgement) and, for a non-decisive kind, :func:`owner.route` (the model's routing run).

    ``loop``: ``inputs.replies`` (``{author, author_type, content_md, reply_to, card}``) are fed in
    order to :func:`atlas_agents.orchestrator.loops.observe` as War Room messages of
    ``inputs.thread_id``; a hit goes to :func:`loops.stop`, as the hook does.

    The transcript text is everything the Orchestrator posted and the card its last card.
    ``Transcript.meta`` holds the posted messages, the code and final classification, the routing
    the model gave (``routing``), the assignments, and the settings version and proposal statuses
    before and after the run (nothing the Orchestrator does may change them)."""
    from atlas_shared.events import EventEnvelope

    from atlas_agents.orchestrator import classify as cls
    from atlas_agents.orchestrator import loops, owner, why

    slug = "orchestrator"
    mode = str(case.inputs.get("mode", "owner"))
    recorder = getattr(supervisor, "_publish", None)
    since = len(getattr(recorder, "events", []))
    ctx = supervisor.tool_context(slug)
    host = _RecordingHost(supervisor)
    meta: dict[str, Any] = {
        "mode": mode,
        "settings_version_before": _settings_version(supervisor),
        "proposal_statuses_before": _proposal_statuses(supervisor),
    }
    result: RunResult | None = None
    outputs: list[dict[str, Any]] = []
    thread_id = case.inputs.get("thread_id")
    if mode == "loop":
        loops.reset()
        try:
            for index, reply in enumerate(case.inputs.get("replies") or []):
                payload = {
                    "author_type": str(reply.get("author_type", "agent")),
                    "author": str(reply["author"]),
                    "kind": str(reply.get("kind", "text")),
                    "content_md": str(reply.get("content_md", "")),
                    "thread_id": thread_id,
                    "reply_to": reply.get("reply_to"),
                    "card": reply.get("card"),
                }
                envelope = EventEnvelope(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, f"eval/{case.case_id}/msg/{index}"),
                    type="warroom.message",
                    ts=ctx.now(),
                    env=supervisor.env,
                    source=f"agent:{payload['author']}",
                    correlation_id=f"eval-{case.case_id}",
                    payload=payload,
                )
                hit = loops.observe(envelope)
                if hit is not None:
                    meta["loop_proposal_id"] = await loops.stop(cast(AgentHost, host), hit)
        finally:
            loops.reset()
    else:
        content = str(case.inputs["message"])
        message = owner.OwnerMessage(
            event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"eval/{case.case_id}/owner")),
            env=supervisor.env,
            content=content,
            ts=ctx.now(),
            row_id=case.inputs.get("message_row"),
            thread_id=thread_id,
            correlation_id=f"eval-{case.case_id}",
        )
        target = why.why_target(content)
        if target is not None:
            meta["why_target"] = target
            evidence = await asyncio.to_thread(why.why_evidence, ctx, target) if target else None
            if evidence is not None:
                outputs.append(
                    {
                        "as_of": None,
                        "source": "code:orchestrator why evidence",
                        "data": {"facts": evidence.facts, "messages": evidence.messages},
                    }
                )
            result = await why.answer_why(cast(AgentHost, host), message, target)
        elif not content.lstrip().startswith("/"):
            guess = await owner.handle_owner_message(cast(AgentHost, host), message)
            meta["code_classification"] = guess.kind
            meta["classification"] = guess.kind
            if guess.kind not in cls.DECISIVE:
                result = await owner.route(cast(AgentHost, host), message, guess)
                accepted = getattr(host.reviewer, "accepted", None)
                if accepted is not None:
                    meta["routing"] = accepted.model_dump(mode="json")
                    meta["classification"] = accepted.kind
    meta["model_ran"] = result is not None
    if result is None:
        result = RunResult(run_id=uuid.uuid5(uuid.NAMESPACE_URL, f"eval/{case.case_id}"), status="ok")
    posted = _warroom_payloads(recorder, since, slug)
    if meta["model_ran"] and result.status == "ok" and result.message:
        # The run's final War Room message is the RunResult's ``message`` (the supervisor posts it).
        posted.append(dict(result.message, author=slug))
    meta["posted"] = posted
    meta["assignments"] = [
        dict(p["card"])
        for p in posted
        if isinstance(p.get("card"), Mapping) and p["card"].get("type") == "assignment"
    ]
    meta["settings_version_after"] = _settings_version(supervisor)
    meta["proposal_statuses_after"] = _proposal_statuses(supervisor)
    base = transcript(result, host.prompt or case.prompt)
    cards = [dict(p["card"]) for p in posted if isinstance(p.get("card"), Mapping)]
    for card in cards:
        card.pop("links", None)  # code's hrefs to the cards (why_answered reads them from meta.posted)
    if meta.get("routing"):
        # The model's own routing (its deadline, the task it wrote) joins the number guard's pool.
        outputs.append({"as_of": None, "source": "agent:orchestrator routing", "data": meta["routing"]})
    # The "Cards: [..](/warroom/threads/..#m-..)" line of a /why answer is code's, not the model's:
    # it is checked by why_answered, and left out of the text the number guard reads.
    text = "\n\n".join(_LINKS_LINE.sub("", str(p.get("content_md", ""))).strip() for p in posted).strip()
    return result, props.Transcript(
        status=result.status,
        text=text,
        card=cards[-1] if cards else None,
        tool_calls=base.tool_calls,
        tool_outputs=[*base.tool_outputs, *outputs],
        error=result.error,
        prompt=str(case.inputs.get("message") or case.prompt),
        meta=meta,
    )


DRIVERS: dict[str, Driver] = {
    "market_scout": market_scout_driver,
    "technical_analyst": technical_analyst_driver,
    "strategist": strategist_driver,
    "derivatives_sentiment": derivatives_sentiment_driver,
    "devils_advocate": devils_advocate_driver,
    "orchestrator": orchestrator_driver,
}


def driver_for(agent: str) -> Driver:
    return DRIVERS.get(agent, default_driver)


__all__ = [
    "DRIVERS",
    "Driver",
    "default_driver",
    "derivatives_sentiment_driver",
    "devils_advocate_driver",
    "driver_for",
    "market_scout_driver",
    "orchestrator_driver",
    "technical_analyst_driver",
]
