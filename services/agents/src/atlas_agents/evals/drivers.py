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

``inputs.replies`` in ``case.yaml``::

    inputs:
      replies:
        news_intelligence: {kind: text, content_md: "...", card: null}
        historian: {kind: research_report, content_md: "...", card: {...}}
"""

from __future__ import annotations

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
            return None, props.Transcript(status="error", error="the run returned nothing", prompt=case.prompt)
        table, _ = await build_ta_table(supervisor.tool_context(ta_agent.SLUG), inst_id)
        if not table.get("stale"):
            return None, props.Transcript(status="error", error=f"{inst_id} was not analysed", prompt=case.prompt)
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
        outputs.append({"as_of": reviewer.table.get("data_as_of"), "source": TA_TABLE_SOURCE, "data": reviewer.table})
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


DRIVERS: dict[str, Driver] = {
    "market_scout": market_scout_driver,
    "technical_analyst": technical_analyst_driver,
}


def driver_for(agent: str) -> Driver:
    return DRIVERS.get(agent, default_driver)


__all__ = [
    "DRIVERS",
    "Driver",
    "default_driver",
    "driver_for",
    "market_scout_driver",
    "technical_analyst_driver",
]
