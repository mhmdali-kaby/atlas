"""The Historian's hooks (brief §7.3, ATL-93; moved onto the registry by ATL-101).

* @Historian: one ``research_report`` reply whose card validates as HistoryVerdict.
* ``historian_regime_table`` (agents.schedule, 07:30 Baghdad): the regime table, computed in code,
  commented by the Historian; posted without commentary when the run fails.
"""

from __future__ import annotations

import asyncio
import logging

from atlas_agents import events, historian
from atlas_agents.mentions import MentionRequest, resolve_parent
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.session import RunResult

log = logging.getLogger(__name__)

WARROOM_STREAM = "atlas.warroom"


@HOOKS.mention(historian.SLUG)
async def answer_mention(host: AgentHost, request: MentionRequest) -> RunResult:
    reply_to, thread_id = await asyncio.to_thread(resolve_parent, host.tool_context(historian.SLUG), request)
    reviewer = historian.VerdictReviewer(request, reply_to=reply_to, thread_id=thread_id)
    return await host.run(
        historian.SLUG,
        historian.mention_prompt(request),
        trigger="mention",
        task=f"answer {request.author}",
        correlation_id=request.correlation_id,
        reviewer=reviewer,
        max_follow_ups=historian.output_retries(),
        thread_id=thread_id,
        reply_to=reply_to,
    )


@HOOKS.schedule(historian.SLUG, historian.REGIME_JOB)
async def post_regime_table(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    table = await historian.build_regime_table(host.tool_context(historian.SLUG))
    if table is None:
        log.warning("historian: no instrument with 24h volume in %s; no regime table %s", host.env, fire.day)
        return None
    result = await host.run(
        historian.SLUG,
        historian.regime_prompt(table),
        trigger="schedule",
        task="regime table",
        correlation_id=fire.correlation_id,
        reviewer=historian.RegimeTableReviewer(table),
        max_follow_ups=0,
    )
    if result.status != "ok" and not host.replaying:
        payload = historian.message_payload(
            historian.SLUG, historian.fallback_regime_message(table, result.status), str(result.run_id)
        )
        envelope = events.envelope(
            type_="warroom.message",
            env=host.env,
            agent=historian.SLUG,
            payload=payload,
            correlation_id=fire.correlation_id,
        )
        try:
            host.publish(WARROOM_STREAM, envelope)
        except Exception:
            log.warning("historian: could not publish the fallback regime table", exc_info=True)
    return result
