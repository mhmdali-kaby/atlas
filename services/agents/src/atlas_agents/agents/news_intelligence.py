"""News Intelligence's hooks (brief §7.2, decision #247 (5), ATL-107).

* @News: one ``research_report`` reply whose card validates as NewsBrief (``form``
  ``instrument_brief``), in the mention's thread.
* ``news_morning_brief`` (agents.schedule, 07:30 Baghdad): the morning brief in Main, once per
  Baghdad day (``form`` ``morning_brief``).
* ``news.alert`` from ingest: an alert analysis replying to the alert message (``form``
  ``alert_analysis``), at most ``agents.news_intelligence.alerts_per_hour`` per rolling hour.
"""

from __future__ import annotations

import asyncio
import logging

from atlas_shared.events import EventEnvelope

from atlas_agents import news_intelligence as news
from atlas_agents.mentions import MentionRequest, resolve_alert_row, resolve_parent
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.session import RunResult

log = logging.getLogger(__name__)


@HOOKS.mention(news.SLUG)
async def answer_mention(host: AgentHost, request: MentionRequest) -> RunResult:
    ctx = host.tool_context(news.SLUG)
    reply_to, thread_id = await asyncio.to_thread(resolve_parent, ctx, request)
    degraded = await asyncio.to_thread(news.degraded_asset_classes, ctx)
    reviewer = news.BriefReviewer(
        ctx,
        form="instrument_brief",
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
        form="morning_brief",
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
    reply_to, thread_id = await resolve_alert_row(ctx, str(envelope.id))
    if reply_to is None:
        log.warning("news_intelligence: alert %s is not stored yet; the analysis goes to Main", envelope.id)
    data = await asyncio.to_thread(news.alert_data, ctx, envelope.payload)
    degraded = await asyncio.to_thread(news.degraded_asset_classes, ctx)
    reviewer = news.BriefReviewer(
        ctx,
        form="alert_analysis",
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
