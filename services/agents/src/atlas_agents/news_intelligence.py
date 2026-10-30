"""News Intelligence's three triggers (brief §7.2, decision #247 (5), ATL-107).

1. **@News mentions** -> an instrument brief. The run's answer ends in one fenced ```json NewsBrief
   card; :class:`BriefReviewer` validates it (one retry with the errors, then the run is
   ``degraded``) and turns the final message into a ``research_report`` reply to the mention.
2. **The 07:30 Asia/Baghdad morning brief** (``agents.schedule.news_morning_brief``), once per
   Baghdad day, in Main. Code computes its context first (:func:`build_morning_context`: the top
   stories, the 48h economic calendar and the instruments with the most news flow) and hands it to
   the model like a tool result.
3. **Ingest's ``news.alert``** on ``atlas.warroom`` -> a short alert analysis replying to the alert
   message, at most ``agents.news_intelligence.alerts_per_hour`` per rolling hour
   (:class:`AlertLimiter`). No other agent registers that envelope type.

In every brief the code owns these card fields, whatever the model wrote: each cluster's status,
status sources, first_seen and price move (``warroom.apply_news_status``, ATL-106), ``form``
(from the trigger), each upcoming event's ``importance`` (from economic_events),
``degraded_asset_classes`` (:func:`degraded_asset_classes`) and, for the morning brief,
``top_instruments``. The number guard's findings go in ``card.meta``.

The hooks are registered in :mod:`atlas_agents.agents.news_intelligence`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import weakref
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from atlas_shared.db import EconomicEvent, NewsCluster, NewsItem, NewsSource
from atlas_shared.defaults import DEFAULTS
from claude_agent_sdk.types import Message
from pydantic import BaseModel, ValidationError
from sqlalchemy import desc, func, select

from atlas_agents import numguard
from atlas_agents.historian import split_card, tool_calls
from atlas_agents.mentions import MentionRequest
from atlas_agents.schemas import CardMeta, NewsBrief, NewsBriefForm, NewsFlow, UpcomingEvent
from atlas_agents.session import Review
from atlas_agents.tools import ToolContext
from atlas_agents.tools.framework import ToolError
from atlas_agents.tools.guard import quote_fields
from atlas_agents.tools.warroom import MAIN_THREAD, apply_news_status

log = logging.getLogger(__name__)

SLUG: Final = "news_intelligence"
RESEARCH_REPORT: Final = "research_report"
MORNING_JOB: Final = "news_morning_brief"
ALERT_EVENT: Final = "news.alert"
MORNING_SOURCE: Final = "atlas.news_morning_context"
_TOOL: Final = "news_intelligence.morning_context"


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    section: Mapping[str, Any] = defaults["agents"]["news_intelligence"]
    return section


def output_retries(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    return int(defaults["agents"]["output_retries"])


def alerts_per_hour(defaults: Mapping[str, Any] = DEFAULTS) -> int:
    value = config(defaults)["alerts_per_hour"]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("agents.news_intelligence.alerts_per_hour must be a non-negative integer")
    return value


def _zone() -> ZoneInfo:
    return ZoneInfo(str(DEFAULTS["timezone"]["display"]))


def both_zones(at: datetime) -> str:
    """``2026-10-30 04:30 UTC (07:30 Baghdad)``: how times are written for the owner."""
    utc = at.astimezone(UTC)
    local = at.astimezone(_zone())
    return f"{utc:%Y-%m-%d %H:%M} UTC ({local:%H:%M} Baghdad)"


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


# -- degraded asset classes ------------------------------------------------------------------------


def source_available(source: NewsSource, now: datetime) -> bool:
    """False when the source is disabled by the owner, keyed with no key (needs_key) or inside its
    failure back-off window (degraded): the same states ingest skips."""
    if not source.enabled:
        return False
    if source.requires_key and not source.api_key:
        return False
    return not (source.disabled_until is not None and source.disabled_until > now)


class NewsCoverage(BaseModel):
    """Which asset classes have no news right now, and since when (ATL-120)."""

    degraded_asset_classes: list[str]
    degraded_since: dict[str, str | None]
    """One entry per degraded class: the latest ``last_success_at`` (ISO-8601 UTC, ``Z``) across
    that class's sources, or null when none of them has ever succeeded."""


def news_coverage(ctx: ToolContext, defaults: Mapping[str, Any] = DEFAULTS) -> NewsCoverage:
    """Asset classes (``agents.news_intelligence.asset_class_categories``) for which no news source
    is available right now, in config order, with when each last had news. A class whose
    categories have no source row at all counts as degraded too: nothing is feeding it. A
    ``last_success_at`` after ``ctx.now()`` (a replay) is not counted."""
    mapping: Mapping[str, Sequence[str]] = config(defaults)["asset_class_categories"]
    with ctx.session_factory() as session:
        sources = list(session.scalars(select(NewsSource)))
    now = ctx.now()
    available = {s.category for s in sources if source_available(s, now)}
    degraded = [cls for cls, categories in mapping.items() if not available.intersection(categories)]
    since: dict[str, str | None] = {}
    for cls in degraded:
        successes = [
            s.last_success_at
            for s in sources
            if s.category in mapping[cls] and s.last_success_at is not None and s.last_success_at <= now
        ]
        since[cls] = _iso(max(successes)) if successes else None
    return NewsCoverage(degraded_asset_classes=degraded, degraded_since=since)


def degraded_asset_classes(ctx: ToolContext, defaults: Mapping[str, Any] = DEFAULTS) -> list[str]:
    """The degraded classes of :func:`news_coverage`, in config order."""
    return news_coverage(ctx, defaults).degraded_asset_classes


# -- the morning brief's context -------------------------------------------------------------------


class TopStory(BaseModel):
    cluster_id: str
    headline: str | None
    max_importance: int
    items: int
    sources: list[str]
    instruments: list[str]
    first_seen: datetime
    last_published: datetime


class CalendarRow(BaseModel):
    time: datetime
    time_text: str
    country: str
    name: str | None
    importance: int
    forecast: str | None
    previous: str | None


class MorningContext(BaseModel):
    as_of: datetime
    window_from: datetime
    events_to: datetime
    top_stories: list[TopStory]
    upcoming_events: list[CalendarRow]
    top_instruments: list[NewsFlow]
    degraded_asset_classes: list[str]


def build_morning_context(ctx: ToolContext, defaults: Mapping[str, Any] = DEFAULTS) -> MorningContext:
    """The 07:30 brief's facts, computed in code and bounded by ``ctx.now()`` in replay."""
    cfg: Mapping[str, Any] = config(defaults)["morning_brief"]
    now = ctx.now()
    since = now - timedelta(hours=int(cfg["lookback_hours"]))
    until = now + timedelta(hours=int(cfg["events_hours"]))
    window = (
        NewsItem.published_at >= since,
        ctx.bound(NewsItem.fetched_at),
        ctx.bound(NewsItem.published_at),
    )
    with ctx.session_factory() as session:
        stories_q = (
            select(
                NewsItem.cluster_id,
                func.max(NewsItem.importance).label("imp"),
                func.count().label("n"),
                func.max(NewsItem.published_at).label("last"),
                func.min(NewsItem.published_at).label("first"),
                func.array_agg(func.distinct(NewsItem.source)).label("sources"),
            )
            .where(*window, NewsItem.cluster_id.is_not(None))
            .group_by(NewsItem.cluster_id)
            .order_by(desc("imp"), desc("n"), desc("last"), NewsItem.cluster_id)
            .limit(int(cfg["top_stories"]))
        )
        story_rows = session.execute(stories_q).all()
        clusters = {
            c.id: c
            for c in session.scalars(
                select(NewsCluster).where(NewsCluster.id.in_([r.cluster_id for r in story_rows]))
            )
        }
        inst = func.unnest(NewsItem.instruments).label("inst")
        unnested = (
            select(inst, NewsItem.cluster_id.label("cid"), NewsItem.sentiment.label("sentiment"))
            .where(*window)
            .subquery()
        )
        flow_q = (
            select(
                unnested.c.inst,
                func.count(func.distinct(unnested.c.cid)).label("stories"),
                func.count().label("items"),
                func.avg(unnested.c.sentiment).label("sentiment"),
            )
            .group_by(unnested.c.inst)
            .order_by(desc("stories"), desc("items"), unnested.c.inst)
            .limit(int(cfg["top_instruments"]))
        )
        flow_rows = session.execute(flow_q).all()
        events = list(
            session.scalars(
                select(EconomicEvent)
                .where(
                    EconomicEvent.scheduled_at >= now,
                    EconomicEvent.scheduled_at <= until,
                    EconomicEvent.importance >= int(cfg["min_event_importance"]),
                    ctx.bound(EconomicEvent.created_at),
                )
                .order_by(EconomicEvent.scheduled_at, EconomicEvent.name, EconomicEvent.id)
                .limit(int(cfg["max_events"]))
            )
        )
    stories: list[TopStory] = []
    for row in story_rows:
        cluster = clusters.get(row.cluster_id)
        headline = None
        if cluster is not None:
            quoted, _flag = quote_fields(
                ctx,
                tool=_TOOL,
                source="news_clusters",
                item_id=str(cluster.id),
                fields={"headline": cluster.headline},
            )
            headline = quoted["headline"]
        stories.append(
            TopStory(
                cluster_id=str(row.cluster_id),
                headline=headline,
                max_importance=int(row.imp),
                items=int(row.n),
                sources=sorted(str(s) for s in row.sources or []),
                instruments=sorted(cluster.instruments or []) if cluster is not None else [],
                first_seen=row.first,
                last_published=row.last,
            )
        )
    calendar: list[CalendarRow] = []
    for event in events:
        quoted, _flag = quote_fields(
            ctx, tool=_TOOL, source=event.source, item_id=str(event.id), fields={"name": event.name}
        )
        calendar.append(
            CalendarRow(
                time=event.scheduled_at,
                time_text=both_zones(event.scheduled_at),
                country=event.country,
                name=quoted["name"],
                importance=int(event.importance),
                forecast=event.forecast,
                previous=event.previous,
            )
        )
    return MorningContext(
        as_of=now,
        window_from=since,
        events_to=until,
        top_stories=stories,
        upcoming_events=calendar,
        top_instruments=[
            NewsFlow(
                inst_id=str(r.inst),
                stories=int(r.stories),
                net_sentiment=None if r.sentiment is None else round(float(r.sentiment), 3),
            )
            for r in flow_rows
        ],
        degraded_asset_classes=degraded_asset_classes(ctx, defaults),
    )


def context_output(context: MorningContext) -> dict[str, Any]:
    """The context as a tool-result envelope ({as_of, source, data}), for the prompt and numguard."""
    return {"as_of": _iso(context.as_of), "source": MORNING_SOURCE, "data": context.model_dump(mode="json")}


# -- prompts ----------------------------------------------------------------------------------------


_CARD_FIELDS: Final = (
    '{"type": "news_brief", "inst_ids": [str], "clusters": [{"cluster_id": "<from the news tools>", '
    '"headline": str, "sources": [{"name": str, "url": str, "published_at": ISO-8601}], '
    '"event_type": str|null, "importance": 1-5, "sentiment": -1..1|null, "first_seen": ISO-8601}], '
    '"upcoming_events": [{"time": ISO-8601, "name": str, "forecast": str|null, "previous": str|null, '
    '"sensitive_instruments": [str]}], "net_sentiment_24h": -1..1|null, "net_sentiment_72h": -1..1|null, '
    '"priced_in": "yes"|"partly"|"no", "what_to_watch": str, "data_as_of": ISO-8601 with zone}'
)

_RULES: Final = (
    "Rules for the card: use only cluster_id values the news tools returned. A story's status "
    "(CONFIRMED/REPORTED/RUMOUR), status_sources, first_seen and price_move_since_first_seen_atr are "
    "computed by Atlas: copy them from the tool result in your text and leave them out of the card "
    "(Atlas fills them in). Write every time as UTC and Baghdad time, e.g. "
    '"14:30 UTC (17:30 Baghdad)". Every number in the text and the card must come from a tool '
    "result. Do not call the warroom tools for this answer: Atlas posts it."
)


def _degraded_line(degraded: Sequence[str]) -> str:
    if not degraded:
        return ""
    return (
        f"\n\nEvery news source for these asset classes is down right now: {', '.join(degraded)}. "
        "Say so in your answer: news coverage for them is missing, so treat the brief as partial."
    )


def mention_prompt(request: MentionRequest, now: datetime, degraded: Sequence[str]) -> str:
    who = "the owner" if request.author_type == "human" else request.author
    hours = int(config()["mention_lookback_hours"])
    return (
        f"War Room message from {who} ({request.author_type}), id {request.event_id}, at "
        f"{both_zones(now)}:\n\n> {request.content_md.strip()}\n\n"
        "Answer with an instrument brief for the instruments it names or that the news links to it: "
        f"the story clusters of the last {hours}h, upcoming catalysts (economic and earnings calendar), "
        "the net sentiment with its trend, a priced-in assessment and what to watch. Write a short "
        "answer (at most ~180 words), then end with exactly one fenced ```json block holding a NewsBrief "
        f"card with these keys and no others:\n{_CARD_FIELDS}\n{_RULES}" + _degraded_line(degraded)
    )


def morning_prompt(context: MorningContext) -> str:
    return (
        f"It is {both_zones(context.as_of)}: time for the morning brief, posted before Market Scout "
        "picks the watchlist. Atlas computed the brief's context in code; here it is as a tool result "
        "(top stories of the last "
        f"{int(config()['morning_brief']['lookback_hours'])}h by importance, the economic calendar "
        "of the next "
        f"{int(config()['morning_brief']['events_hours'])}h, and the instruments with the most news "
        f"flow):\n\n```json\n{json.dumps(context_output(context))}\n```\n\n"
        "Write the morning brief: the top 10 stories across all asset classes (check each with "
        "news.get_story_cluster for its status and sources), a 48h event calendar table (time in UTC "
        "and Baghdad, name, forecast, previous, sensitive instruments), and the 5 instruments with the "
        "most news flow. Keep the text under ~300 words, then end with exactly one fenced ```json "
        f"block holding a NewsBrief card with these keys and no others:\n{_CARD_FIELDS}\n{_RULES}"
        + _degraded_line(context.degraded_asset_classes)
    )


_ALERT_FIELDS: Final = (
    "headline",
    "source",
    "source_name",
    "url",
    "published_at",
    "importance",
    "event_type",
    "inst_ids",
    "asset_classes",
    "sentiment",
    "reason",
    "held_inst_ids",
)


def alert_data(ctx: ToolContext, payload: Mapping[str, Any]) -> dict[str, Any]:
    """The alert's fields for the model, with its source text quoted as untrusted."""
    data = {k: payload.get(k) for k in ("cluster_id", *_ALERT_FIELDS)}
    quoted, _flag = quote_fields(
        ctx,
        tool="news_intelligence.alert",
        source=str(payload.get("source") or "news.alert"),
        item_id=str(payload.get("news_item_id") or payload.get("cluster_id") or "unknown"),
        fields={"headline": _text(payload.get("headline")), "source_name": _text(payload.get("source_name"))},
    )
    data.update(quoted)
    return data


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def alert_prompt(data: Mapping[str, Any], now: datetime, degraded: Sequence[str]) -> str:
    return (
        f"Ingest raised a news alert at {both_zones(now)}. Here it is as a tool result:\n\n"
        f"```json\n{json.dumps({'as_of': _iso(now), 'source': 'atlas.news_alert', 'data': data})}\n```\n\n"
        "Write the alert analysis (at most ~120 words): a one-line headline, the story's status from "
        "news.get_story_cluster (CONFIRMED/REPORTED/RUMOUR, as Atlas computed it), the affected "
        "instruments, the expected impact (direction and magnitude band with a probability) and the "
        'recommended action for the team (e.g. "PM: consider tightening the stop on the XAU long before '
        '15:30 CPI"). Then end with exactly one fenced ```json block holding a NewsBrief card for this '
        f"story with these keys and no others:\n{_CARD_FIELDS}\n{_RULES}" + _degraded_line(degraded)
    )


# -- the reviewer -----------------------------------------------------------------------------------


def _validation_errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in exc.errors()
    )


def event_importance(ctx: ToolContext, events: Sequence[UpcomingEvent]) -> list[UpcomingEvent]:
    """Each event with ``importance`` from economic_events: the highest importance of the events
    scheduled at exactly its time (known by ``ctx.now()``), or null when there is none."""
    times = sorted({e.time for e in events})
    if not times:
        return list(events)
    with ctx.session_factory() as session:
        rows = session.execute(
            select(EconomicEvent.scheduled_at, func.max(EconomicEvent.importance))
            .where(EconomicEvent.scheduled_at.in_(times), ctx.bound(EconomicEvent.created_at))
            .group_by(EconomicEvent.scheduled_at)
        ).all()
    by_time = {at: int(importance) for at, importance in rows}
    return [e.model_copy(update={"importance": by_time.get(e.time)}) for e in events]


def code_facts(brief: NewsBrief) -> dict[str, Any]:
    """The card fields the runtime wrote, for the number guard."""
    return {
        "clusters": [
            {
                "first_seen": c.first_seen.isoformat(),
                "price_move_since_first_seen_atr": [
                    m.model_dump(mode="json") for m in c.price_move_since_first_seen_atr
                ],
            }
            for c in brief.clusters
        ],
        "top_instruments": [f.model_dump(mode="json") for f in brief.top_instruments],
        "event_importance": [e.importance for e in brief.upcoming_events],
        "degraded_since": dict(brief.degraded_since),
    }


class BriefReviewer:
    """Reviews one News Intelligence answer: the card must validate as NewsBrief and name only
    known clusters (the follow-up carries the errors); then the code-owned fields are written and
    the number guard checks the text and card. Accepted, it is the run's only message: a
    ``research_report`` reply to ``reply_to`` in ``thread_id`` (Main when None)."""

    def __init__(
        self,
        ctx: ToolContext,
        *,
        form: NewsBriefForm,
        reply_to: str | None,
        thread_id: str | None,
        outputs: Sequence[Mapping[str, Any]] = (),
        top_instruments: Sequence[NewsFlow] = (),
    ) -> None:
        self.ctx = ctx
        self.form = form
        self.reply_to = reply_to
        self.thread_id = thread_id
        self.outputs = list(outputs)
        self.top_instruments = list(top_instruments)
        self.brief: NewsBrief | None = None

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review:
        content, card, error = split_card(text)
        brief: NewsBrief | None = None
        if card is not None:
            for owned in ("meta", "form", "top_instruments", "degraded_asset_classes", "degraded_since"):
                card.pop(owned, None)  # the runtime's fields, never the agent's
            card.setdefault("type", "news_brief")
            try:
                brief = NewsBrief.model_validate(card)
            except ValidationError as exc:
                error = f"NewsBrief card failed validation: {_validation_errors(exc)}"
        if brief is not None:
            try:
                brief = await asyncio.to_thread(apply_news_status, self.ctx, brief)
            except ToolError as exc:
                brief, error = None, exc.message
        if brief is None:
            reason = error or "no NewsBrief card"
            return Review(follow_up=self._follow_up(reason), failure=f"invalid card after retry: {reason}")

        coverage = await asyncio.to_thread(news_coverage, self.ctx)
        events = await asyncio.to_thread(event_importance, self.ctx, brief.upcoming_events)
        brief = brief.model_copy(
            update={
                "form": self.form,
                "degraded_asset_classes": coverage.degraded_asset_classes,
                "degraded_since": coverage.degraded_since,
                "top_instruments": self.top_instruments if self.form == "morning_brief" else [],
                "upcoming_events": events,
            }
        )
        if not content:
            content = brief.what_to_watch
        outputs: list[Mapping[str, Any]] = [c.output for c in tool_calls(messages) if c.output is not None]
        outputs.extend(self.outputs)
        plain = brief.model_dump(mode="json", exclude={"meta"})
        # The fields code wrote are facts as well; only those, never the model's own numbers.
        outputs.append(
            {"as_of": _iso(self.ctx.now()), "source": "atlas.news_brief_code", "data": code_facts(brief)}
        )
        guard = numguard.check(content, plain, outputs).to_meta()
        final = brief.model_copy(update={"meta": CardMeta.model_validate(guard)})
        self.brief = final
        overrides: dict[str, Any] = {
            "kind": RESEARCH_REPORT,
            "content_md": content,
            "card": final.model_dump(mode="json", by_alias=True),
            "reply_to": self.reply_to,
            "mentions": [],
        }
        if self.thread_id is not None:
            overrides["thread_id"] = self.thread_id
        else:
            overrides["thread"] = dict(MAIN_THREAD)
        return Review(message=overrides)

    @staticmethod
    def _follow_up(reason: str) -> str:
        return (
            f"Your NewsBrief card was not accepted: {reason}\n\n"
            "Send the whole answer again: the short answer, then exactly one fenced ```json block with "
            f"a corrected NewsBrief card:\n{_CARD_FIELDS}\n{_RULES}\nThis is your only retry."
        )


# -- the alert rate limit ----------------------------------------------------------------------------


class AlertLimiter:
    """At most ``alerts_per_hour`` alert runs per rolling hour, per supervisor. Kept in memory: a
    restart starts a fresh window (documented in config/defaults.yaml)."""

    def __init__(self) -> None:
        self._windows: weakref.WeakKeyDictionary[object, deque[datetime]] = weakref.WeakKeyDictionary()

    def allow(self, host: object, now: datetime, limit: int | None = None) -> bool:
        cap = alerts_per_hour() if limit is None else limit
        window = self._windows.setdefault(host, deque())
        cutoff = now - timedelta(hours=1)
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= cap:
            return False
        window.append(now)
        return True


ALERTS = AlertLimiter()
