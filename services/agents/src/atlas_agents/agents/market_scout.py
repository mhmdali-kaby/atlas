"""Market Scout's hooks (brief §7.1 steps 4-6, decision #247 (2)(3), ATL-108).

* ``scout_watchlist`` (agents.schedule, 08:00 Baghdad) and @Scout mentions run :func:`run_watchlist`:

  1. Code builds (or reuses, while still valid) the feature table and the shortlist with its
     correlation flags (ATL-104/105). Today's owner pins are kept in the shortlist; the settings
     blocklist always wins.
  2. An empty shortlist posts a "no watchlist today" card with the filter reason and saves an
     ``empty`` watchlist. The model is not called.
  3. (:mod:`atlas_agents.scout.teammates`) Code posts a @News mention (shortlist instruments
     with news or catalysts) and a @Historian mention (the top ``historian_top_n``) and polls
     ``warroom_messages`` for their replies (``reply_to`` = the mention's row)
     up to ``agents.scout.wait_seconds``.
  4. The Scout session gets the shortlist, pins, blocklist, open positions, the 48h calendar, the
     replies and its last ``history_days`` days of picks, and answers with a fenced JSON
     :class:`~atlas_agents.scout.publish.ScoutAnswer`.
  5. :class:`WatchlistReviewer` validates it (one retry), refuses once a pick set that breaks the
     correlation rule, then :func:`~atlas_agents.scout.publish.enforce` applies the code rules
     (blocklist, partial-evidence and news-class-down conviction caps, ...), the watchlist is saved with
     ``save_watchlist`` (edit window = created_at + ``watchlist_edit_window_minutes``) and the
     ``watchlist`` card is the run's final War Room message.

A mention handler must not block the supervisor's War Room consumer (the teammates' replies arrive
through it), so an @Scout mention starts the pipeline as a background task and returns None.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Final

from atlas_shared.db import NewsSource, ResearchReport, Watchlist, WatchlistItem, owner_day
from atlas_shared.events import EventEnvelope
from claude_agent_sdk.types import Message
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from atlas_agents import events, historian, numguard
from atlas_agents.mentions import MentionRequest, max_depth
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.schemas import WatchlistPick
from atlas_agents.scout import features as scout_features
from atlas_agents.scout.features import FeatureTable, build_feature_table, save_feature_table
from atlas_agents.scout.prescore import Shortlist, build_shortlist, prescore_config, shortlist_correlations
from atlas_agents.scout.publish import (
    CARD_TYPE,
    Cap,
    Evidence,
    Pair,
    ScoutAnswer,
    SourceHealth,
    TeammateReply,
    answer_problems,
    correlation_violations,
    degraded_asset_classes,
    enforce,
    publish_config,
    status_for,
)
from atlas_agents.scout.teammates import (
    Reply,
    historian_request,
    mention_envelope,
    news_request,
    news_targets,
    wait_for_replies,
)
from atlas_agents.scout.universe import build_universe, load_config
from atlas_agents.session import Review, RunResult
from atlas_agents.tools import ToolContext
from atlas_agents.tools.account import compute_correlations, get_positions
from atlas_agents.tools.framework import NoInput
from atlas_agents.tools.warroom import MAIN_THREAD
from atlas_agents.watchlists import save_watchlist

log = logging.getLogger(__name__)

SLUG: Final = "market_scout"
NEWS: Final = "news_intelligence"
HISTORIAN: Final = "historian"
JOB: Final = "scout_watchlist"
WARROOM_STREAM: Final = "atlas.warroom"
SOURCE: Final = "atlas.scout_watchlist"

_tasks: set[asyncio.Task[Any]] = set()


# -- config -----------------------------------------------------------------------------------------


def wait_seconds() -> float:
    return float(publish_config()["wait_seconds"])


def poll_seconds() -> float:
    return float(publish_config()["poll_seconds"])


def link_seconds() -> float:
    return float(publish_config()["link_seconds"])


def _int(key: str) -> int:
    return int(publish_config()[key])


def _cap() -> float:
    return float(str(publish_config()["partial_conviction_cap"]))


def _threshold() -> float:
    return float(str(prescore_config()["correlation"]["threshold"]))


# -- step 1: feature table, shortlist, today's owner edits -------------------------------------------


@dataclass
class Prepared:
    table: FeatureTable
    shortlist: Shortlist
    report_id: str | None
    blocklist: list[str]
    max_picks: int
    pinned_items: list[WatchlistPick] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    previous_version: int | None = None
    degraded_asset_classes: list[str] = field(default_factory=list)
    """Asset classes whose news sources are all down (their picks' convictions are capped)."""

    def asset_class_of(self) -> dict[str, str]:
        """inst_id -> asset class as code knows it (feature table, then owner pins)."""
        out = {p.inst_id: p.asset_class for p in self.pinned_items}
        out.update({r.inst_id: r.asset_class for r in self.table.rows})
        return out


def _current_watchlist(session: Session, ctx: ToolContext, day: date) -> Watchlist | None:
    return session.scalars(
        select(Watchlist)
        .where(Watchlist.env == ctx.env, Watchlist.date == day, ctx.bound(Watchlist.created_at))
        .order_by(Watchlist.version.desc())
        .limit(1)
    ).first()


def _item_pick(item: WatchlistItem) -> WatchlistPick:
    return WatchlistPick.model_validate(
        {
            "inst_id": item.inst_id,
            "rank": item.rank,
            "asset_class": item.asset_class,
            "bias": item.bias,
            "conviction": float(item.conviction),
            "thesis": item.thesis,
            "catalysts": item.catalysts or [],
            "key_levels": item.key_levels or {},
            "invalidation": item.invalidation or "Owner pin: no invalidation set yet.",
            "data_as_of": item.data_as_of,
        }
    )


def _owner_edits(
    session: Session, ctx: ToolContext, day: date
) -> tuple[list[WatchlistPick], list[str], int | None]:
    """(today's owner-pinned items, instruments the owner removed today, current version)."""
    current = _current_watchlist(session, ctx, day)
    if current is None:
        return [], [], None
    items = session.scalars(select(WatchlistItem).where(WatchlistItem.watchlist_id == current.id)).all()
    pinned = [_item_pick(i) for i in items if i.pinned_by_owner and i.removed_by_owner_at is None]
    removed = [i.inst_id for i in items if i.removed_by_owner_at is not None and not i.pinned_by_owner]
    return pinned, removed, current.version


def _reusable_table(session: Session, ctx: ToolContext) -> tuple[FeatureTable, str] | None:
    now = ctx.now()
    report = session.scalars(
        select(ResearchReport)
        .where(
            ResearchReport.env == ctx.env,
            ResearchReport.agent == scout_features.AGENT,
            ResearchReport.kind == scout_features.KIND,
            ResearchReport.valid_until > now,
            ctx.bound(ResearchReport.created_at),
        )
        .order_by(ResearchReport.created_at.desc())
        .limit(1)
    ).first()
    if report is None:
        return None
    try:
        table = FeatureTable.model_validate(report.data)
    except ValidationError:
        return None
    if table.as_of > now:
        return None
    return table, str(report.id)


def _max_picks(data: Mapping[str, Any] | None) -> int:
    from atlas_shared.defaults import DEFAULTS

    markets = DEFAULTS["markets"]
    value = (data or {}).get("max_instruments_per_day")
    try:
        picks = int(value) if value is not None else int(markets["max_instruments_per_day"])
    except (TypeError, ValueError):
        picks = int(markets["max_instruments_per_day"])
    low, high = int(markets["max_instruments_per_day_min"]), int(markets["max_instruments_per_day_max"])
    return max(low, min(high, picks))


def _settings_data(session: Session, ctx: ToolContext) -> Mapping[str, Any] | None:
    if not ctx.replaying:
        row = session.execute(
            text("SELECT data FROM settings WHERE env = :env LIMIT 1"), {"env": ctx.env}
        ).first()
    else:
        row = session.execute(
            text(
                "SELECT h.data FROM settings_history h JOIN settings s ON s.id = h.settings_id "
                "WHERE s.env = :env AND h.created_at <= :at ORDER BY h.version DESC LIMIT 1"
            ),
            {"env": ctx.env, "at": ctx.replay_as_of},
        ).first()
    if row is None or not isinstance(row[0], dict):
        return None
    data: Mapping[str, Any] = row[0]
    return data


def news_categories() -> dict[str, list[str]]:
    """``agents.news_intelligence.asset_class_categories``: asset class -> news_sources categories."""
    from atlas_shared.defaults import DEFAULTS

    raw: Mapping[str, Any] = DEFAULTS["agents"]["news_intelligence"]["asset_class_categories"]
    return {str(k): [str(c) for c in v] for k, v in raw.items()}


def news_degraded_classes(session: Session, ctx: ToolContext) -> list[str]:
    """Asset classes none of whose news sources is up now (see :func:`degraded_asset_classes`).
    news_sources holds current health only, so a replay reads it as it is today."""
    rows = session.execute(
        select(
            NewsSource.category,
            NewsSource.enabled,
            NewsSource.requires_key,
            NewsSource.api_key.is_not(None),
            NewsSource.disabled_until,
        )
    ).all()
    sources = [
        SourceHealth(
            category=str(r[0]),
            enabled=bool(r[1]),
            requires_key=bool(r[2]),
            has_key=bool(r[3]),
            disabled_until=r[4],
        )
        for r in rows
    ]
    return degraded_asset_classes(sources, ctx.now(), news_categories())


def prepare(ctx: ToolContext, day: date) -> Prepared:
    """Step 1 (synchronous, DB-bound: call it with ``asyncio.to_thread``)."""
    with ctx.session_factory() as session:
        pinned_items, removed, version = _owner_edits(session, ctx, day)
        config = load_config(session, ctx)
        max_picks = _max_picks(_settings_data(session, ctx))
        degraded = news_degraded_classes(session, ctx)
        reused = _reusable_table(session, ctx)
    if reused is None:
        table = build_feature_table(ctx, build_universe(ctx))
        with ctx.session_factory() as session:
            report = save_feature_table(session, ctx, table)
            report_id = str(report.id)
            session.commit()
    else:
        table, report_id = reused
    pinned_items = [p for p in pinned_items if p.inst_id.upper() not in config.blocklist]
    shortlist = build_shortlist(table, pinned=[p.inst_id for p in pinned_items])
    if shortlist.rows:
        shortlist = shortlist_correlations(ctx, shortlist)
    return Prepared(
        table=table,
        shortlist=shortlist,
        report_id=report_id,
        blocklist=sorted(config.blocklist),
        max_picks=max_picks,
        pinned_items=pinned_items,
        removed=removed,
        previous_version=version,
        degraded_asset_classes=degraded,
    )


def empty_reason(table: FeatureTable) -> str:
    """Why the shortlist is empty, from the universe filter's rejections."""
    if not table.rejected:
        return "No instrument is in the active universe yet (no live instruments with 24h volume were found)."
    counts: dict[str, int] = {}
    for row in table.rejected:
        for reason in row.reasons:
            counts[reason] = counts.get(reason, 0) + 1
    detail = ", ".join(
        f"{reason} {n}" for reason, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return f"No instrument passed the universe filter: {len(table.rejected)} rejected (by check: {detail})."


# -- step 4: the Scout session's prompt ----------------------------------------------------------------


def _recent_picks(ctx: ToolContext, day: date) -> list[dict[str, Any]]:
    """The last ``history_days`` Baghdad days of the Scout's own watchlists (current version each)."""
    days = _int("history_days")
    start = day - timedelta(days=days)
    out: list[dict[str, Any]] = []
    with ctx.session_factory() as session:
        latest = (
            select(Watchlist.date, func.max(Watchlist.version).label("version"))
            .where(
                Watchlist.env == ctx.env,
                Watchlist.date >= start,
                Watchlist.date < day,
                ctx.bound(Watchlist.created_at),
            )
            .group_by(Watchlist.date)
            .subquery()
        )
        lists = session.scalars(
            select(Watchlist)
            .join(latest, (Watchlist.date == latest.c.date) & (Watchlist.version == latest.c.version))
            .where(Watchlist.env == ctx.env)
            .order_by(Watchlist.date.desc())
        ).all()
        for wl in lists:
            items = session.scalars(
                select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id).order_by(WatchlistItem.rank)
            ).all()
            out.append(
                {
                    "date": wl.date.isoformat(),
                    "status": wl.status,
                    "picks": [
                        {
                            "inst_id": i.inst_id,
                            "rank": i.rank,
                            "bias": i.bias,
                            "conviction": float(i.conviction),
                        }
                        for i in items
                        if i.removed_by_owner_at is None
                    ],
                    "outcome": "not scored yet",
                }
            )
    return out


def _row_json(row: Any) -> dict[str, Any]:
    features = row.features.model_dump(mode="json", exclude={"missing", "quality_notes", "inst_id"})
    return {
        "inst_id": row.inst_id,
        "rank": row.rank,
        "score": round(row.score, 4),
        "pinned": row.pinned,
        "in_top": row.in_top,
        "unknown_features": sorted(row.features.missing),
        **features,
    }


@dataclass
class Inputs:
    """Everything the Scout session is shown, as one tool-like result (also the number guard's pool)."""

    prepared: Prepared
    replies: dict[str, Reply]
    evidence: Evidence
    positions: list[dict[str, Any]]
    recent: list[dict[str, Any]]

    def data(self) -> dict[str, Any]:
        p = self.prepared
        return {
            "shortlist": [_row_json(r) for r in p.shortlist.rows],
            "correlated_pairs": [c.model_dump(mode="json") for c in p.shortlist.correlated_pairs or []],
            "pinned_by_owner": [i.inst_id for i in p.pinned_items],
            "removed_by_owner_today": p.removed,
            "blocklist": p.blocklist,
            "open_positions": self.positions,
            "calendar_48h": [c.model_dump(mode="json") for c in p.table.shared_catalysts],
            "btc_reference": p.table.btc_reference,
            "spy_reference": p.table.spy_reference,
            "feature_table_as_of": p.table.as_of.isoformat(),
            "max_picks": p.max_picks,
            "your_recent_watchlists": self.recent,
            "teammates": [t.model_dump(mode="json") for t in self.evidence.teammates],
            "news_down_asset_classes": p.degraded_asset_classes,
        }

    def tool_output(self) -> dict[str, Any]:
        return {"as_of": self.prepared.table.as_of.isoformat(), "source": SOURCE, "data": self.data()}


_ANSWER_FIELDS: Final = (
    '{"type": "watchlist", "backdrop": [3 strings], "picks": [{"inst_id": str, "rank": int (1 = '
    'strongest), "asset_class": str, "bias": "long"|"short"|"neutral", "conviction": 0-1, "thesis": '
    '"2-4 sentences, incl. why it beats the rejected", "catalysts": [{"name": str, "time": ISO-8601|null, '
    '"source": str, "importance": 1-5}], "key_levels": {"support": num, "resistance": num, '
    '"invalidation": num}, "invalidation": str, "data_as_of": ISO-8601 with zone}], "rejected": '
    '[{"inst_id": str, "reason": str}] (the 3 strongest), "no_pick_reason": str|null}'
)


def _reply_block(reply: Reply) -> str:
    body = {"kind": reply.kind, "content_md": reply.content_md, "card": reply.card}
    return (
        f'<teammate_reply agent="{reply.agent}" message_id="{reply.message_id}">\n'
        f"{json.dumps(body, default=str)}\n</teammate_reply>"
    )


def scout_prompt(inputs: Inputs) -> str:
    p = inputs.prepared
    replies = "\n\n".join(_reply_block(r) for r in inputs.replies.values()) or "(no teammate replied)"
    partial = ""
    if inputs.evidence.partial:
        reasons = "; ".join(inputs.evidence.partial_reasons(_int("stale_minutes")))
        partial = (
            f"\n\nEVIDENCE IS PARTIAL ({reasons}). Atlas will publish this watchlist as "
            f"'partial evidence' and cap every conviction at {_cap():g}."
        )
    if p.degraded_asset_classes:
        partial += (
            f"\n\nEVERY NEWS SOURCE IS DOWN for: {', '.join(p.degraded_asset_classes)}. Atlas caps the "
            f"conviction of picks in those asset classes at {_cap():g}."
        )
    return (
        "It is time for today's watchlist (brief §7.1). Atlas computed the shortlist (top by "
        "pre-score, plus owner pins), the 48h calendar, open positions and your recent watchlists; "
        "here they are as a tool result:\n\n"
        f"```json\n{json.dumps(inputs.tool_output(), default=str)}\n```\n\n"
        f"Your teammates' replies to Atlas's requests:\n\n{replies}{partial}\n\n"
        f"Choose 0..{p.max_picks} instruments from the shortlist. Rules Atlas enforces after you: "
        "blocklisted or owner-removed instruments are dropped; more than "
        f"{_int('max_correlated_picks')} picks/open positions with 30-day correlation > "
        f"{_threshold():g} are refused; owner pins stay on the list. Use your tools for anything "
        "not in the table. Write at most ~120 words, then end with exactly one fenced ```json block "
        f"with these keys and no others:\n{_ANSWER_FIELDS}\n"
        "Every number must come from the table, a reply or a tool result. Do not call the warroom "
        "tools: Atlas posts the watchlist card after its checks."
    )


# -- step 5: review, enforce, save -------------------------------------------------------------------


def _summary(
    picks: Sequence[WatchlistPick],
    status: str,
    reasons: Sequence[str],
    backdrop: Sequence[str],
    degraded: Sequence[str] = (),
) -> str:
    lines = [line.strip() for line in backdrop if line.strip()]
    if degraded:
        lines.insert(
            0,
            f"**News down** for {', '.join(degraded)}: convictions in that class capped at {_cap():g}.",
        )
    if status == "partial_evidence":
        lines.insert(0, f"**Partial evidence**: {'; '.join(reasons)}. Convictions capped at {_cap():g}.")
    if picks:
        listed = ", ".join(f"{p.inst_id} {p.bias} ({p.conviction:g})" for p in picks)
        lines.append(f"Today's watchlist: {listed}.")
    return "\n".join(lines)


def _card_pick(pick: WatchlistPick, pinned: Sequence[str], caps: Mapping[str, Cap]) -> dict[str, Any]:
    out: dict[str, Any] = {**pick.model_dump(mode="json"), "pinned_by_owner": pick.inst_id in pinned}
    cap = caps.get(pick.inst_id)
    if cap is not None:
        out["conviction_before_cap"] = cap.conviction_before_cap
        out["cap_reason"] = cap.cap_reason
    return out


@dataclass
class Saved:
    watchlist_id: str
    version: int
    status: str
    card: dict[str, Any]


def save_and_card(
    ctx: ToolContext,
    day: date,
    *,
    picks: Sequence[WatchlistPick],
    pinned: Sequence[str],
    status: str,
    reason: str | None,
    data: dict[str, Any],
    caps: Mapping[str, Cap] | None = None,
) -> Saved:
    """Save the watchlist (new version for the day) and return its card. Synchronous."""
    created = ctx.now()
    window = created + timedelta(minutes=_int("watchlist_edit_window_minutes"))
    with ctx.session_factory() as session:
        wl = save_watchlist(
            session,
            ctx.env,
            day,
            list(picks),
            status,  # type: ignore[arg-type]
            created_by=SLUG,
            reason=reason,
            edit_window_ends_at=window,
            data=data,
            created_at=created,
        )
        if pinned:
            for item in session.scalars(
                select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id)
            ).all():
                if item.inst_id in pinned:
                    item.pinned_by_owner = True
        session.commit()
        watchlist_id, version = str(wl.id), wl.version
    card: dict[str, Any] = {
        "type": CARD_TYPE,
        "watchlist_id": watchlist_id,
        "version": version,
        "date": day.isoformat(),
        "status": status,
        "created_at": created.isoformat(),
        "edit_window_ends_at": window.isoformat(),
        "reason": reason,
        "picks": [_card_pick(p, pinned, caps or {}) for p in picks],
        **data,
    }
    return Saved(watchlist_id, version, status, card)


class WatchlistReviewer:
    """Validates the Scout's answer (one retry), refuses a correlated pick set once, then enforces
    the code rules, saves the watchlist and makes the card the run's final message."""

    def __init__(self, ctx: ToolContext, day: date, inputs: Inputs, base_data: dict[str, Any]) -> None:
        self.ctx = ctx
        self.day = day
        self.inputs = inputs
        self.base_data = base_data
        self.schema_retries = 1
        self.correlation_retries = 1
        self.saved: Saved | None = None
        self.refused_correlation: list[list[str]] = []

    @property
    def max_follow_ups(self) -> int:
        return self.schema_retries + self.correlation_retries

    def _pairs(self, pick_ids: Sequence[str]) -> list[Pair]:
        p = self.inputs.prepared
        pairs = [Pair(c.a, c.b, c.corr) for c in p.shortlist.correlated_pairs or []]
        held = [str(pos["inst_id"]) for pos in self.inputs.positions]
        if held and pick_ids:
            result = compute_correlations(self.ctx, [*pick_ids, *held], 30)
            pairs.extend(Pair(c.a, c.b, c.corr) for c in result.pairs if c.corr is not None)
        return pairs

    async def __call__(self, text_: str, messages: list[Message], attempt: int) -> Review:
        p = self.inputs.prepared
        shortlist_ids = [r.inst_id for r in p.shortlist.rows]
        _content, card, error = historian.split_card(text_)
        answer: ScoutAnswer | None = None
        problems: list[str] = []
        if card is not None:
            card.setdefault("type", CARD_TYPE)
            try:
                answer = ScoutAnswer.model_validate(card)
            except ValidationError as exc:
                problems = [historian._validation_errors(exc)]
            else:
                problems = answer_problems(answer, max_picks=p.max_picks, shortlist_ids=shortlist_ids)
        elif error:
            problems = [error]
        if answer is None or problems:
            why = "; ".join(problems) or "no watchlist card"
            if self.schema_retries > 0:
                self.schema_retries -= 1
                return Review(follow_up=self._follow_up(why), failure=f"invalid watchlist after retry: {why}")
            return Review(failure=f"invalid watchlist after retry: {why}")

        held = [str(pos["inst_id"]) for pos in self.inputs.positions]
        blocked = {b.upper() for b in p.blocklist}
        candidates = [x.inst_id for x in answer.picks if x.inst_id.upper() not in blocked]
        pairs = await asyncio.to_thread(self._pairs, candidates)
        threshold, max_members = _threshold(), _int("max_correlated_picks")
        groups = correlation_violations(candidates, held, pairs, threshold, max_members)
        if groups and self.correlation_retries > 0:
            self.correlation_retries -= 1
            self.refused_correlation = groups
            listed = "; ".join(", ".join(g) for g in groups)
            return Review(
                follow_up=(
                    f"Your picks break the correlation rule: more than {max_members} picks/open "
                    f"positions with 30-day correlation > {threshold:g} ({listed}). Keep at most "
                    f"{max_members} of each group and send the whole answer again with the corrected "
                    "```json block. This is your only retry."
                )
            )

        evidence = self.inputs.evidence
        partial = evidence.partial
        enforced = enforce(
            answer.picks,
            blocklist=p.blocklist,
            removed=p.removed,
            shortlist_ids=shortlist_ids,
            pinned_items=p.pinned_items,
            held_ids=held,
            pairs=pairs,
            threshold=threshold,
            max_members=max_members,
            partial=partial,
            cap=_cap(),
            degraded_classes=p.degraded_asset_classes,
            asset_class_of=p.asset_class_of(),
        )
        status = status_for(enforced.picks, partial)
        reasons = evidence.partial_reasons(_int("stale_minutes")) if partial else []
        reason: str | None
        if status == "empty":
            reason = (answer.no_pick_reason or "").strip() or "Every pick was removed by Atlas's rules."
        elif partial:
            reason = "Partial evidence: " + "; ".join(reasons)
        else:
            reason = None
        data = {
            **self.base_data,
            "backdrop": [line.strip() for line in answer.backdrop if line.strip()],
            "rejected": [r.model_dump(mode="json") for r in answer.rejected],
            "corrections": [c.model_dump(mode="json") for c in enforced.corrections],
            "correlation_refused": self.refused_correlation,
        }
        saved = await asyncio.to_thread(
            save_and_card,
            self.ctx,
            self.day,
            picks=enforced.picks,
            pinned=enforced.pinned,
            status=status,
            reason=reason,
            data=data,
            caps=enforced.caps,
        )
        self.saved = saved
        summary = _summary(enforced.picks, status, reasons, answer.backdrop, p.degraded_asset_classes)
        if status == "empty":
            summary = f"No watchlist today: {reason}\n{summary}".strip()
        outputs: list[Mapping[str, Any]] = [
            c.output for c in historian.tool_calls(messages) if c.output is not None
        ]
        outputs.append(self.inputs.tool_output())
        for reply in self.inputs.replies.values():
            outputs.append(
                {
                    "as_of": None,
                    "source": f"warroom:{reply.agent}",
                    "data": {"content_md": reply.content_md, "card": reply.card},
                }
            )
        plain = {
            k: v
            for k, v in saved.card.items()
            if k not in {"watchlist_id", "version", "created_at", "edit_window_ends_at", "date"}
        }
        saved.card["meta"] = numguard.check(summary, plain, outputs).to_meta()
        return Review(
            message={
                "kind": CARD_TYPE,
                "thread": dict(MAIN_THREAD),
                "content_md": summary,
                "card": saved.card,
                "reply_to": None,
                "mentions": [],
            }
        )

    @staticmethod
    def _follow_up(reason: str) -> str:
        return (
            f"Your watchlist was not accepted: {reason}\n\nSend the whole answer again: at most ~120 "
            f"words, then exactly one fenced ```json block:\n{_ANSWER_FIELDS}\nThis is your only retry."
        )


# -- the pipeline -----------------------------------------------------------------------------------


def _already_done(ctx: ToolContext, day: date, correlation_id: str) -> bool:
    with ctx.session_factory() as session:
        found = session.execute(
            select(Watchlist.id)
            .where(
                Watchlist.env == ctx.env,
                Watchlist.date == day,
                Watchlist.created_by == SLUG,
                Watchlist.data["correlation_id"].astext == correlation_id,
            )
            .limit(1)
        ).first()
    return found is not None


def _link_message(ctx: ToolContext, watchlist_id: str, timeout: float) -> str | None:
    """Wait for atlas:bridge to store the card, then set watchlists.message_id. Synchronous."""
    deadline = time.monotonic() + timeout
    while True:
        with ctx.session_factory() as session:
            row = session.execute(
                text(
                    "SELECT id FROM warroom_messages WHERE env = :env AND author = :author AND kind = :kind "
                    "AND card->>'watchlist_id' = :wid ORDER BY created_at LIMIT 1"
                ),
                {"env": ctx.env, "author": SLUG, "kind": CARD_TYPE, "wid": watchlist_id},
            ).first()
            if row is not None:
                wl = session.get(Watchlist, uuid.UUID(watchlist_id))
                if wl is not None:
                    wl.message_id = row[0]
                    session.commit()
                return str(row[0])
        if time.monotonic() >= deadline:
            return None
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


async def _positions(ctx: ToolContext) -> list[dict[str, Any]]:
    out = await get_positions(ctx, NoInput())
    data = out.data
    rows = getattr(data, "positions", [])
    return [
        {
            "inst_id": r.inst_id,
            "pos_side": r.pos_side,
            "pos": str(r.pos),
            "avg_px": None if r.avg_px is None else str(r.avg_px),
        }
        for r in rows
    ]


def _publish(host: AgentHost, envelope: EventEnvelope) -> None:
    if host.replaying:
        return
    try:
        host.publish(WARROOM_STREAM, envelope)
    except Exception:
        log.warning("market_scout: could not publish %s", envelope.type, exc_info=True)


async def _post_empty(
    host: AgentHost,
    ctx: ToolContext,
    day: date,
    prepared: Prepared,
    base: dict[str, Any],
    correlation_id: str,
) -> None:
    reason = empty_reason(prepared.table)
    data = {**base, "backdrop": [], "rejected": [], "corrections": [], "correlation_refused": []}
    saved = await asyncio.to_thread(
        save_and_card, ctx, day, picks=[], pinned=[], status="empty", reason=reason, data=data
    )
    payload: dict[str, Any] = {
        "thread": dict(MAIN_THREAD),
        "author_type": "agent",
        "author": SLUG,
        "kind": CARD_TYPE,
        "content_md": f"No watchlist today: {reason}",
        "card": saved.card,
        "reply_to": None,
        "mentions": [],
        "reasoning_md": None,
        "tool_calls": None,
        "tokens_in": None,
        "tokens_out": None,
        "cost_usd": None,
    }
    _publish(
        host,
        events.envelope(
            type_="warroom.message", env=host.env, agent=SLUG, payload=payload, correlation_id=correlation_id
        ),
    )
    if not host.replaying:
        await asyncio.to_thread(_link_message, ctx, saved.watchlist_id, link_seconds())


async def run_watchlist(
    host: AgentHost, *, trigger: str, correlation_id: str, day: date | None = None, depth: int = 0
) -> RunResult | None:
    """The whole pipeline (module docstring). Returns the Scout run, or None when the model was not
    called (already done for this correlation id, or an empty shortlist)."""
    ctx = host.tool_context(SLUG)
    today = day or owner_day(ctx.now())
    if trigger == "schedule" and await asyncio.to_thread(_already_done, ctx, today, correlation_id):
        return None
    prepared = await asyncio.to_thread(prepare, ctx, today)
    base: dict[str, Any] = {
        "trigger": trigger,
        "correlation_id": correlation_id,
        "feature_table_report_id": prepared.report_id,
        "feature_table_as_of": prepared.table.as_of.isoformat(),
        "shortlist": [r.inst_id for r in prepared.shortlist.rows],
        "previous_version": prepared.previous_version,
        "degraded_asset_classes": prepared.degraded_asset_classes,
    }
    if not prepared.shortlist.rows:
        await _post_empty(host, ctx, today, prepared, base, correlation_id)
        return None

    asked: dict[str, str] = {}
    teammates: list[TeammateReply] = []
    mention_depth = depth + 1
    requests = {
        NEWS: news_request(prepared.shortlist, prepared.table),
        HISTORIAN: historian_request(prepared.shortlist),
    }
    for agent, request in requests.items():
        if mention_depth >= max_depth():
            teammates.append(TeammateReply(agent=agent, status="not_asked", note="mention chain depth limit"))
            continue
        envelope = mention_envelope(ctx, agent, request, correlation_id=correlation_id, depth=mention_depth)
        _publish(host, envelope)
        asked[agent] = str(envelope.id)
    replies: dict[str, Reply] = {}
    if asked:
        replies = await asyncio.to_thread(
            wait_for_replies, ctx, asked, timeout=wait_seconds(), poll=poll_seconds()
        )
    for agent, mention_id in asked.items():
        reply = replies.get(agent)
        if reply is None:
            teammates.append(
                TeammateReply(
                    agent=agent,
                    status="timed_out",
                    mention_id=mention_id,
                    note=f"no reply within {wait_seconds():g} s",
                )
            )
        else:
            teammates.append(
                TeammateReply(
                    agent=agent,
                    status="replied",
                    mention_id=mention_id,
                    message_id=reply.message_id,
                    replied_at=reply.replied_at,
                )
            )
    teammates.sort(key=lambda t: list(requests).index(t.agent))

    now = ctx.now()
    age = (now - prepared.table.as_of).total_seconds() / 60
    evidence = Evidence(
        feature_table_as_of=prepared.table.as_of,
        feature_table_age_minutes=round(age, 2),
        feature_table_stale=age > _int("stale_minutes"),
        teammates=teammates,
    )
    positions = await _positions(ctx)
    recent = await asyncio.to_thread(_recent_picks, ctx, today)
    inputs = Inputs(prepared=prepared, replies=replies, evidence=evidence, positions=positions, recent=recent)
    base["evidence"] = evidence.model_dump(mode="json")
    reviewer = WatchlistReviewer(ctx, today, inputs, base)
    result = await host.run(
        SLUG,
        scout_prompt(inputs),
        trigger=trigger,
        task="today's watchlist",
        correlation_id=correlation_id,
        reviewer=reviewer,
        max_follow_ups=reviewer.max_follow_ups,
    )
    if reviewer.saved is not None and result.status == "ok" and not host.replaying:
        await asyncio.to_thread(_link_message, ctx, reviewer.saved.watchlist_id, link_seconds())
    return result


# -- hooks ------------------------------------------------------------------------------------------


@HOOKS.schedule(SLUG, JOB)
async def publish_watchlist(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    return await run_watchlist(host, trigger="schedule", correlation_id=fire.correlation_id, day=fire.day)


@HOOKS.mention(SLUG)
async def answer_mention(host: AgentHost, request: MentionRequest) -> RunResult | None:
    """@Scout re-runs today's watchlist. In the background: the teammates' replies come through the
    War Room consumer this handler is called from."""
    correlation_id = request.correlation_id or f"{JOB}:mention:{request.event_id}"

    async def go() -> None:
        try:
            await run_watchlist(host, trigger="mention", correlation_id=correlation_id, depth=request.depth)
        except Exception:
            log.exception("market_scout: watchlist run for mention %s failed", request.event_id)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return None


__all__ = [
    "JOB",
    "SLUG",
    "Inputs",
    "Prepared",
    "Reply",
    "WatchlistReviewer",
    "answer_mention",
    "empty_reason",
    "news_targets",
    "prepare",
    "publish_watchlist",
    "run_watchlist",
    "save_and_card",
    "scout_prompt",
    "wait_for_replies",
]
