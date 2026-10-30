"""News Intelligence's hooks (brief §7.2, decision #247 (5), ATL-107).

* @News: one ``research_report`` reply whose card validates as NewsBrief (``brief_kind``
  ``instrument``), in the mention's thread.
* ``news_morning_brief`` (agents.schedule, 07:30 Baghdad): the morning brief in Main, once per
  Baghdad day (``brief_kind`` ``morning``).
* ``news.alert`` from ingest: an alert analysis replying to the alert message (``brief_kind``
  ``alert``), at most ``agents.news_intelligence.alerts_per_hour`` per rolling hour.
"""

from __future__ import annotations

import asyncio
import logging

from atlas_shared.events import EventEnvelope

from atlas_agents import news_intelligence as news
from atlas_agents.mentions import MentionRequest, resolve_parent
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.session import RunResult
from atlas_agents.tools import ToolContext
from atlas_agents.tools.framework import ToolError
from atlas_agents.tools.warroom import _parent

log = logging.getLogger(__name__)

PARENT_TRIES = int(news.config()["alert_parent_tries"])
PARENT_WAIT_SECONDS = int(news.config()["alert_parent_wait_ms"]) / 1000


@HOOKS.mention(news.SLUG)
async def answer_mention(host: AgentHost, request: MentionRequest) -> RunResult:
    ctx = host.tool_context(news.SLUG)
    reply_to, thread_id = await asyncio.to_thread(resolve_parent, ctx, request)
    degraded = await asyncio.to_thread(news.degraded_asset_classes, ctx)
    reviewer = news.BriefReviewer(
        ctx,
        kind="instrument",
        reply_to=reply_to,
        thread_id=thread_id,
        # The asker's own numbers are quoted, not invented.
        outputs=[{"as_of": None, "source": "warroom.message", "data": {"content_md": request.content_md}}],
    )
    return await host.run(
        news.SLUG,
        news.mention_prompt(request, ctx.now(), degraded),
        trigger="mention",
        task=f"answer {request.author}",
        correlation_id=request.correlation_id,
        reviewer=reviewer,
        max_follow_ups=news.output_retries(),
        thread_id=thread_id,
        reply_to=reply_to,
    )


@HOOKS.schedule(news.SLUG, news.MORNING_JOB)
async def morning_brief(host: AgentHost, fire: ScheduleFire) -> RunResult:
    ctx = host.tool_context(news.SLUG)
    context = await asyncio.to_thread(news.build_morning_context, ctx)
    reviewer = news.BriefReviewer(
        ctx,
        kind="morning",
        reply_to=None,
        thread_id=None,
        outputs=[news.context_output(context)],
        top_instruments=context.top_instruments,
    )
    return await host.run(
        news.SLUG,
        news.morning_prompt(context),
        trigger="schedule",
        task="morning brief",
        correlation_id=fire.correlation_id,
        reviewer=reviewer,
        max_follow_ups=news.output_retries(),
    )


async def _alert_parent(ctx: ToolContext, event_id: str) -> tuple[str | None, str | None]:
    """(row id, thread id) of the alert's War Room message. The bridge stores it from the same
    stream, so it may land a moment after we read the envelope: look up to PARENT_TRIES times
    (once in replay), then reply in Main with no reply_to."""
    tries = 1 if ctx.replaying else PARENT_TRIES
    for attempt in range(tries):
        try:
            return await asyncio.to_thread(_parent, ctx, event_id)
        except ToolError:
            if attempt + 1 < tries:
                await asyncio.sleep(PARENT_WAIT_SECONDS)
    log.warning("news_intelligence: alert %s is not stored yet; the analysis goes to Main", event_id)
    return None, None


@HOOKS.event(news.SLUG, news.ALERT_EVENT)
async def analyse_alert(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    ctx = host.tool_context(news.SLUG)
    now = ctx.now()
    if not news.ALERTS.allow(host, now):
        log.warning(
            "news_intelligence: alert %s skipped: %d alert analyses in the last hour (alerts_per_hour)",
            envelope.id,
            news.alerts_per_hour(),
        )
        return None
    reply_to, thread_id = await _alert_parent(ctx, str(envelope.id))
    data = await asyncio.to_thread(news.alert_data, ctx, envelope.payload)
    degraded = await asyncio.to_thread(news.degraded_asset_classes, ctx)
    reviewer = news.BriefReviewer(
        ctx,
        kind="alert",
        reply_to=reply_to,
        thread_id=thread_id,
        outputs=[{"as_of": None, "source": "atlas.news_alert", "data": data}],
    )
    return await host.run(
        news.SLUG,
        news.alert_prompt(data, now, degraded),
        trigger="alert",
        task="news alert",
        correlation_id=envelope.correlation_id,
        reviewer=reviewer,
        max_follow_ups=news.output_retries(),
        thread_id=thread_id,
        reply_to=reply_to,
    )
