"""The Historian's weekly review (brief §7.3 PROACTIVE DUTIES, decision #253 (K), ATL-199).

At ``agents.schedule.historian_weekly_review`` ("Sun 18:00" Baghdad) code aggregates the last
``lookback_days`` Baghdad days (:func:`compute`), never the model:

* the Portfolio Manager's post-mortems in ``journal_entries`` (ATL-194) whose ``data.actual.closed_at``
  falls in the window, grouped by setup-type tag, asset class and opening agent: count, wins, losses,
  scratches, total R (over the trades that have an R), total USD;
* the paper-trade R of skipped ideas from ``paper_trades`` (ATL-196) when that table exists; the
  section is left out otherwise;
* each agent's newest ``agent_scores`` row up to the review day.

The Historian is given that table and answers with a short comment, then one JSON block holding a
one-line verdict and **exactly one** suggested change for the owner. :class:`WeeklyReviewReviewer`
rejects any other shape (one follow-up, then the run ends degraded per brief §6.3). Every figure in
the card is code's; the number guard checks the model's prose against it.

The message is a War Room ``research_report`` whose card ``kind`` (and ``type``) is
``weekly_review``; the review is stored as a ``research_reports`` row of kind ``weekly_review`` with
``data.week_start`` = the Baghdad Monday of the review's week (ATL-200's list endpoint reads it).

A week with no closed trades posts a short code-only card without a model run. When the model fails,
code posts the figures with ``suggestion: null``. Either way one review per week: a stored row for
the week stops every later tick.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final
from zoneinfo import ZoneInfo

from atlas_shared.db import AgentScore, JournalEntry, ResearchReport
from atlas_shared.defaults import DEFAULTS
from claude_agent_sdk.types import Message
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from atlas_agents import numguard
from atlas_agents.historian import SLUG, split_card
from atlas_agents.registry import AgentHost, ScheduleFire
from atlas_agents.session import Review, RunResult
from atlas_agents.tools.framework import RunScope, ToolContext
from atlas_agents.tools.warroom import MAIN_THREAD, message_envelope

log = logging.getLogger(__name__)

JOB: Final = "historian_weekly_review"
KIND: Final = "weekly_review"
MESSAGE_KIND: Final = "research_report"
WARROOM_STREAM: Final = "atlas.warroom"
SOURCE: Final = "atlas.weekly_review_code"
POSTMORTEM_AGENT: Final = "portfolio_manager"
OUTCOMES: Final = ("win", "loss", "scratch")
CENT: Final = Decimal("0.01")
ZERO: Final = Decimal(0)
UNKNOWN: Final = "unknown"


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["history"]["weekly_review"]
    return cfg


def _zone() -> ZoneInfo:
    return ZoneInfo(str(DEFAULTS["timezone"]["display"]))


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


def week_start(day: date) -> date:
    """The Baghdad Monday of ``day``'s week (``data.week_start``, ATL-200)."""
    return day - timedelta(days=day.weekday())


def window(day: date, now: datetime, lookback_days: int | None = None) -> tuple[datetime, datetime]:
    """[00:00 Baghdad of ``day - (lookback_days - 1)``, ``now``) as UTC instants."""
    days = int(config()["lookback_days"]) if lookback_days is None else lookback_days
    first = day - timedelta(days=days - 1)
    start = datetime(first.year, first.month, first.day, tzinfo=_zone()).astimezone(UTC)
    return start, now.astimezone(UTC)


# -- the figures ----------------------------------------------------------------------------------


@dataclasses.dataclass
class _Group:
    count: int = 0
    wins: int = 0
    losses: int = 0
    scratches: int = 0
    total_r: Decimal = ZERO
    r_n: int = 0
    total_usd: Decimal = ZERO

    def add(self, outcome: str, r: Decimal | None, usd: Decimal | None) -> None:
        self.count += 1
        if outcome == "win":
            self.wins += 1
        elif outcome == "loss":
            self.losses += 1
        elif outcome == "scratch":
            self.scratches += 1
        if r is not None:
            self.total_r += r
            self.r_n += 1
        if usd is not None:
            self.total_usd += usd

    def row(self, key: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {} if key is None else {"key": key}
        out.update(
            {
                "count": self.count,
                "wins": self.wins,
                "losses": self.losses,
                "scratches": self.scratches,
                "total_r": _fmt(self.total_r) if self.r_n else None,
                "r_n": self.r_n,
                "total_usd": _fmt(self.total_usd),
            }
        )
        return out


@dataclasses.dataclass(frozen=True)
class ClosedTrade:
    journal_id: str
    inst_id: str | None
    setup: str
    asset_class: str
    agent: str
    outcome: str
    r: Decimal | None
    usd: Decimal | None


def _label(value: Any) -> str:
    return value if isinstance(value, str) and value.strip() else UNKNOWN


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def trade_from_entry(entry: JournalEntry) -> ClosedTrade:
    data = _map(entry.data)
    tags, actual, expected = _map(data.get("tags")), _map(data.get("actual")), _map(data.get("expected"))
    outcome = data.get("outcome") if data.get("outcome") in OUTCOMES else tags.get("outcome")
    return ClosedTrade(
        journal_id=str(entry.id),
        inst_id=entry.inst_id,
        setup=_label(tags.get("setup") or data.get("setup_tag")),
        asset_class=_label(data.get("asset_class") or tags.get("asset")),
        agent=_label(expected.get("author")),
        outcome=outcome if outcome in OUTCOMES else UNKNOWN,
        r=_dec(actual.get("realised_r")),
        usd=_dec(actual.get("realised_pnl_usd")),
    )


def closed_trades(session: Session, env: str, start: datetime, end: datetime) -> list[ClosedTrade]:
    """The PM post-mortems (one per full close, ATL-194) of trades closed in [start, end)."""
    rows = session.scalars(
        select(JournalEntry)
        .where(
            JournalEntry.env == env,
            JournalEntry.agent == POSTMORTEM_AGENT,
            JournalEntry.data.has_key("position_ref"),
            JournalEntry.created_at >= start,  # written at or after the close
        )
        .order_by(JournalEntry.created_at, JournalEntry.id)
    ).all()
    out: list[ClosedTrade] = []
    for entry in rows:
        closed_at = _parse_ts(_map(_map(entry.data).get("actual")).get("closed_at"))
        if closed_at is None or not (start <= closed_at < end):
            continue
        out.append(trade_from_entry(entry))
    return out


def _grouped(trades: Iterable[ClosedTrade], attr: str) -> list[dict[str, Any]]:
    groups: dict[str, _Group] = {}
    for t in trades:
        groups.setdefault(getattr(t, attr), _Group()).add(t.outcome, t.r, t.usd)
    return [groups[k].row(k) for k in sorted(groups)]


def paper_trades(session: Session, env: str, start: datetime, end: datetime) -> dict[str, Any] | None:
    """Skipped ideas simulated by ATL-196's scorer, decided in the window; None without the table."""
    if session.execute(text("select to_regclass('paper_trades')")).scalar() is None:
        return None
    rows = session.execute(
        text(
            "select coalesce(proposal_status, 'unknown') as status, count(*) as n, "
            "count(realised_r) as filled_n, sum(realised_r) as total_r "
            "from paper_trades where env = :env and decided_at >= :start and decided_at < :end "
            "group by 1 order by 1"
        ),
        {"env": env, "start": start, "end": end},
    ).all()
    total = _Group()
    by_status: list[dict[str, Any]] = []
    n_all = 0
    for status, n, filled_n, total_r in rows:
        r = _dec(total_r)
        n_all += int(n)
        total.r_n += int(filled_n)
        total.total_r += r or ZERO
        by_status.append(
            {
                "status": str(status),
                "count": int(n),
                "filled": int(filled_n),
                "total_r": _fmt(r) if filled_n else None,
            }
        )
    avg = total.total_r / total.r_n if total.r_n else None
    return {
        "count": n_all,
        "filled": total.r_n,
        "total_r": _fmt(total.total_r) if total.r_n else None,
        "avg_r": _fmt(avg),
        "by_status": by_status,
    }


def _scalar_data(data: Any) -> dict[str, Any]:
    """The headline numbers of an agent_scores row's data (``metrics`` when present, else its
    scalar top-level keys); per-item detail lists are left out of the prompt."""
    if not isinstance(data, Mapping):
        return {}
    metrics = data.get("metrics")
    source = metrics if isinstance(metrics, Mapping) else data
    return {str(k): v for k, v in source.items() if v is None or isinstance(v, (str, int, float, bool))}


def latest_scores(session: Session, env: str, day: date) -> list[dict[str, Any]]:
    """Each agent's newest agent_scores row on or before ``day``."""
    newest = (
        select(AgentScore.agent, func.max(AgentScore.day).label("day"))
        .where(AgentScore.env == env, AgentScore.day <= day)
        .group_by(AgentScore.agent)
        .subquery()
    )
    rows = session.scalars(
        select(AgentScore)
        .join(newest, (AgentScore.agent == newest.c.agent) & (AgentScore.day == newest.c.day))
        .where(AgentScore.env == env)
        .order_by(AgentScore.agent)
    ).all()

    def num(value: Decimal | None) -> str | None:
        return None if value is None else format(value.normalize(), "f")

    return [
        {
            "agent": r.agent,
            "day": r.day.isoformat(),
            "n": r.n,
            "hit_rate": num(r.hit_rate),
            "brier_score": num(r.brier_score),
            "pnl_attributed_usd": _fmt(r.pnl_attributed_usd),
            "metrics": _scalar_data(r.data),
        }
        for r in rows
    ]


def compute(session: Session, ctx: ToolContext, day: date, now: datetime) -> dict[str, Any]:
    """The review's card: every figure, computed by code."""
    start, end = window(day, now)
    trades = closed_trades(session, ctx.env, start, end)
    totals = _Group()
    for t in trades:
        totals.add(t.outcome, t.r, t.usd)
    card: dict[str, Any] = {
        "type": KIND,
        "kind": KIND,
        "env": ctx.env,
        "as_of": _iso(now),
        "week_start": week_start(day).isoformat(),
        "day": day.isoformat(),
        "window": {"from": _iso(start), "to": _iso(end)},
        "trade_count": len(trades),
        "no_trades": not trades,
        "totals": totals.row(),
        "by_setup": _grouped(trades, "setup"),
        "by_asset_class": _grouped(trades, "asset_class"),
        "by_agent": _grouped(trades, "agent"),
        "journal_ids": [t.journal_id for t in trades],
        "agent_scores": latest_scores(session, ctx.env, day),
        "verdict": None,
        "suggestion": None,
        "status": "no_trades" if not trades else "pending",
    }
    paper = paper_trades(session, ctx.env, start, end)
    if paper is not None:
        card["paper_trades"] = paper
    return card


# -- text -----------------------------------------------------------------------------------------


def _money(value: str | None) -> str:
    if value is None:
        return "n/a"
    d = Decimal(value)
    return f"{'+' if d > 0 else '-' if d < 0 else ''}${abs(d):,.2f}"


def code_summary(card: Mapping[str, Any]) -> str:
    """The text code posts when there is no model answer."""
    head = f"**Weekly review, week of {card['week_start']}** ({card['env']})."
    if card["no_trades"]:
        return f"{head}\nNo closed trades this week."
    t = card["totals"]
    r = f", total {t['total_r']}R" if t["total_r"] is not None else ""
    lines = [
        head,
        f"{t['count']} closed trades: {t['wins']} wins, {t['losses']} losses, {t['scratches']} scratches"
        f"{r}, {_money(t['total_usd'])}.",
    ]
    if card["status"] != "ok":
        lines.append("The Historian's verdict and suggested change are not available this week.")
    return "\n".join(lines)


def prompt(card: Mapping[str, Any]) -> str:
    return (
        f"Write this week's review (week of {card['week_start']}, Baghdad, env {card['env']}).\n\n"
        "Every figure below was computed by code from the journal's post-mortems, paper trades and "
        "agent scores. Do not recompute or change any of them: code keeps its own figures in the card.\n\n"
        f"```json\n{json.dumps(card, indent=1)}\n```\n\n"
        "Answer with:\n"
        "1. At most 150 words: which setups, asset classes and agents made or lost money (in R and USD), "
        "what the skipped ideas would have done, and what the agent scores say. State n for every claim "
        "and say the evidence is weak when n < 20. Use only numbers from the figures above.\n"
        "2. Then exactly one fenced ```json block: "
        '{"verdict": "<one line: History supports / weakly supports / does not support / contradicts '
        'the current process, plus the single most useful statistic>", '
        '"suggested_changes": [{"change": "<one concrete change for the owner to review, e.g. raise min '
        'R for breakout setups to 2.0>", "reason": "<the figure behind it>"}]}. '
        "suggested_changes holds exactly one item."
    )


def _line(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    line = " ".join(value.split())
    return line[:limit] if line else None


def parse_answer(model_card: Mapping[str, Any]) -> tuple[str, dict[str, str]] | str:
    """(verdict, suggestion) from the model's block, or the reason it is not acceptable."""
    cfg = config()
    verdict = _line(model_card.get("verdict"), int(cfg["verdict_max_chars"]))
    if verdict is None:
        return "verdict must be a non-empty one-line string"
    changes = model_card.get("suggested_changes")
    if not isinstance(changes, list):
        return "suggested_changes must be a list holding exactly one change"
    if len(changes) != 1:
        return f"suggested_changes holds {len(changes)} changes; exactly one is required"
    item = changes[0]
    if not isinstance(item, Mapping):
        return "the suggested change must be an object with change and reason"
    change = _line(item.get("change"), int(cfg["change_max_chars"]))
    if change is None:
        return "the suggested change needs a non-empty change"
    reason = _line(item.get("reason"), int(cfg["change_max_chars"])) or ""
    return verdict, {"change": change, "reason": reason}


def finalize(
    code_card: Mapping[str, Any], verdict: str | None, suggestion: Mapping[str, str] | None, status: str
) -> dict[str, Any]:
    card: dict[str, Any] = copy.deepcopy(dict(code_card))
    card["verdict"] = verdict
    card["suggestion"] = None if suggestion is None else dict(suggestion)
    card["status"] = status
    return card


class WeeklyReviewReviewer:
    """Accepts a comment plus one JSON block with a verdict and exactly one suggested change. Any
    other shape gets one follow-up; a second failure ends the run degraded (brief §6.3)."""

    def __init__(self, card: Mapping[str, Any]) -> None:
        self.code_card = card
        self.card: dict[str, Any] | None = None
        self.content: str | None = None

    async def __call__(self, text_: str, messages: list[Message], attempt: int) -> Review:
        content, model_card, error = split_card(text_)
        parsed: tuple[str, dict[str, str]] | str = error or "no JSON block"
        if model_card is not None:
            parsed = parse_answer(model_card)
        if isinstance(parsed, str):
            return Review(
                follow_up=(
                    f"Your answer was not accepted: {parsed}. Send the review again, then exactly one fenced "
                    '```json block {"verdict": "...", '
                    '"suggested_changes": [{"change": "...", "reason": "..."}]} '
                    "with exactly one suggested change. This is your only retry."
                ),
                failure=f"invalid weekly review after retry: {parsed}",
            )
        verdict, suggestion = parsed
        card = finalize(self.code_card, verdict, suggestion, "ok")
        body = content or code_summary(card)
        full = f"{body}\n\n**Verdict:** {verdict}\n\n**Suggested change:** {suggestion['change']}"
        if suggestion["reason"]:
            full += f" ({suggestion['reason']})"
        outputs = [{"as_of": card["as_of"], "source": SOURCE, "data": self.code_card}]
        card["meta"] = numguard.check(full, None, outputs).to_meta()
        self.card, self.content = card, full
        return Review(
            message={
                "kind": MESSAGE_KIND,
                "content_md": full,
                "card": card,
                "reply_to": None,
                "mentions": [],
                "thread": dict(MAIN_THREAD),
            }
        )


# -- storage and the job --------------------------------------------------------------------------


def already_reviewed(session: Session, env: str, start_of_week: date) -> bool:
    return (
        session.scalars(
            select(ResearchReport.id)
            .where(
                ResearchReport.env == env,
                ResearchReport.agent == SLUG,
                ResearchReport.kind == KIND,
                ResearchReport.data["week_start"].astext == start_of_week.isoformat(),
            )
            .limit(1)
        ).first()
        is not None
    )


def save(
    session: Session, ctx: ToolContext, card: Mapping[str, Any], content: str, run_id: uuid.UUID | None
) -> ResearchReport:
    stamp = ctx.clock()
    row = ResearchReport(
        env=ctx.env,
        agent=SLUG,
        agent_run_id=run_id,
        inst_id=None,
        kind=KIND,
        content_md=content,
        data=dict(card),
        confidence=None,
        valid_until=None,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(row)
    session.flush()
    return row


def _context(host: AgentHost, correlation_id: str) -> ToolContext:
    return dataclasses.replace(
        host.tool_context(SLUG), publish=host.publish, correlation_id=correlation_id, run=RunScope()
    )


async def run(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    """The Sunday job: compute, post the code-only card on a week with no trades, else let the
    Historian write the verdict and one suggested change; store one review per week."""
    ctx = _context(host, fire.correlation_id)

    def load() -> dict[str, Any] | None:
        with ctx.session_factory() as session:
            if already_reviewed(session, ctx.env, week_start(fire.day)):
                return None
            return compute(session, ctx, fire.day, fire.at)

    code_card = await asyncio.to_thread(load)
    if code_card is None:
        return None
    result: RunResult | None = None
    card: dict[str, Any] | None = None
    content: str | None = None
    if not code_card["no_trades"]:
        reviewer = WeeklyReviewReviewer(code_card)
        try:
            result = await host.run(
                SLUG,
                prompt(code_card),
                trigger="schedule",
                task="weekly review",
                correlation_id=fire.correlation_id,
                reviewer=reviewer,
                max_follow_ups=int(config()["output_retries"]),
            )
        except LookupError:  # AgentUnavailableError: disabled or over budget
            log.info("historian: not available; the weekly review is posted by code")
        if result is not None and result.status == "ok" and result.message is not None:
            card, content = reviewer.card, reviewer.content
    if card is None or content is None:
        status = (
            "no_trades"
            if code_card["no_trades"]
            else (result.status if result is not None else "unavailable")
        )
        card = finalize(code_card, None, None, status)
        content = code_summary(card)
        if not host.replaying:
            ctx.publish(
                WARROOM_STREAM, message_envelope(ctx, kind=MESSAGE_KIND, content_md=content, card=card)
            )
    run_id = result.run_id if result is not None else None
    final_card, final_content = card, content

    def store() -> None:
        with ctx.session_factory() as session:
            save(session, ctx, final_card, final_content, run_id)
            session.commit()

    await asyncio.to_thread(store)
    return result


__all__ = [
    "JOB",
    "KIND",
    "ClosedTrade",
    "WeeklyReviewReviewer",
    "already_reviewed",
    "closed_trades",
    "code_summary",
    "compute",
    "finalize",
    "latest_scores",
    "paper_trades",
    "parse_answer",
    "prompt",
    "run",
    "week_start",
    "window",
]
