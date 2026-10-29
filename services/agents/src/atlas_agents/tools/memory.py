"""The ``memory`` MCP server (brief §6.2, §7.8, ATL-86): the journal, the shared daily notebook and
the calling agent's own scores, all for ``ctx.env``.

* Reads are bounded on ``created_at`` by ``ctx.bound`` (and scores also on ``day``), so a replayed
  day never sees later rows.
* Writes are refused in replay (``read_only_in_replay``): an eval must never add rows that a live
  agent would then read as real memory.
* ``write_journal`` is granted only to the Portfolio Manager by the access matrix; the tool checks
  the matrix again for ``ctx.agent`` so a mis-wired session still cannot write.
* ``get_my_scores`` returns rows for ``ctx.agent`` only; there is no argument naming an agent.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from atlas_shared.db import AgentNotebookEntry, AgentScore, JournalEntry, usage_day
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import desc, select

from atlas_agents.tools.catalogue import (
    AppendNotebookInput,
    ReadJournalInput,
    ReadNotebookInput,
    ScoresInput,
    WriteJournalInput,
)
from atlas_agents.tools.framework import ToolContext, ToolError, ToolResult, atlas_tool, fq_name


def _no_writes_in_replay(ctx: ToolContext) -> None:
    if ctx.replaying:
        raise ToolError("read_only_in_replay", "memory writes are refused while replaying a past day")


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


# -- journal -----------------------------------------------------------------------------------------


class JournalOut(BaseModel):
    id: str
    created_at: datetime | None
    agent: str
    inst_id: str | None
    proposal_id: str | None
    title: str
    expected_md: str | None
    actual_md: str | None
    lessons_md: str | None
    tags: list[str]
    data: dict[str, Any]


class JournalListOut(BaseModel):
    entries: list[JournalOut]


class JournalEntryIn(BaseModel):
    """What write_journal accepts in ``entry`` (a post-mortem, brief §7.8)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    inst_id: str | None = Field(default=None, max_length=64)
    proposal_id: uuid.UUID | None = None
    expected_md: str | None = Field(default=None, max_length=8000)
    actual_md: str | None = Field(default=None, max_length=8000)
    lessons_md: str | None = Field(default=None, max_length=8000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    data: dict[str, Any] = Field(default_factory=dict)


class WrittenOut(BaseModel):
    id: str
    created_at: datetime


def _journal_out(row: JournalEntry) -> JournalOut:
    return JournalOut(
        id=str(row.id),
        created_at=row.created_at,
        agent=row.agent,
        inst_id=row.inst_id,
        proposal_id=None if row.proposal_id is None else str(row.proposal_id),
        title=row.title,
        expected_md=row.expected_md,
        actual_md=row.actual_md,
        lessons_md=row.lessons_md,
        tags=list(row.tags or []),
        data=dict(row.data or {}),
    )


@atlas_tool("memory", "read_journal", ReadJournalInput, JournalListOut)
async def read_journal(ctx: ToolContext, args: ReadJournalInput) -> ToolResult:
    """Journal entries (post-mortems and lessons), newest first, optionally for one instId and/or
    only entries carrying every one of the given tags."""
    stmt = select(JournalEntry).where(JournalEntry.env == ctx.env, ctx.bound(JournalEntry.created_at))
    if args.inst_id:
        stmt = stmt.where(JournalEntry.inst_id == args.inst_id)
    if args.tags:
        stmt = stmt.where(JournalEntry.tags.contains(args.tags))
    stmt = stmt.order_by(desc(JournalEntry.created_at), desc(JournalEntry.id)).limit(args.limit)
    with ctx.session_factory() as s:
        rows = list(s.scalars(stmt))
    data = JournalListOut(entries=[_journal_out(r) for r in rows])
    return ToolResult(data=data, as_of=ctx.now(), source="atlas.journal_entries")


def _may_write_journal(agent: str) -> bool:
    from atlas_agents.access import allowed_tools  # access imports this package

    try:
        return fq_name("memory", "write_journal") in allowed_tools(agent)
    except LookupError:
        return False


@atlas_tool("memory", "write_journal", WriteJournalInput, WrittenOut)
async def write_journal(ctx: ToolContext, args: WriteJournalInput) -> ToolResult:
    """Save a journal entry (post-mortem). entry = {title, inst_id?, proposal_id?, expected_md?,
    actual_md?, lessons_md?, tags[], data{}}."""
    if not _may_write_journal(ctx.agent):
        raise ToolError("forbidden", f"{ctx.agent} may not write the journal")
    _no_writes_in_replay(ctx)
    try:
        entry = JournalEntryIn.model_validate(args.entry)
    except ValidationError as exc:
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ToolError("invalid_input", "; ".join(parts)) from exc
    now = ctx.now()
    row = JournalEntry(
        env=ctx.env,
        agent=ctx.agent,
        inst_id=entry.inst_id,
        proposal_id=entry.proposal_id,
        title=entry.title,
        expected_md=entry.expected_md,
        actual_md=entry.actual_md,
        lessons_md=entry.lessons_md,
        tags=sorted({t.strip() for t in entry.tags if t.strip()}),
        data=entry.data,
        created_at=now,
        updated_at=now,
    )
    with ctx.session_factory() as s:
        s.add(row)
        s.commit()
        row_id = str(row.id)
    return ToolResult(data=WrittenOut(id=row_id, created_at=now), as_of=now, source="atlas.journal_entries")


# -- notebook ----------------------------------------------------------------------------------------


class NoteOut(BaseModel):
    id: str
    agent: str
    note_md: str
    created_at: datetime | None


class NotebookOut(BaseModel):
    day: date
    notes: list[NoteOut]


class AppendedOut(BaseModel):
    id: str
    day: date
    created_at: datetime


@atlas_tool("memory", "read_notebook", ReadNotebookInput, NotebookOut)
async def read_notebook(ctx: ToolContext, args: ReadNotebookInput) -> ToolResult:
    """The team's shared notebook for one Asia/Baghdad date (default today), newest note first."""
    day = args.date or usage_day(ctx.now())
    stmt = (
        select(AgentNotebookEntry)
        .where(
            AgentNotebookEntry.env == ctx.env,
            AgentNotebookEntry.day == day,
            ctx.bound(AgentNotebookEntry.created_at),
        )
        .order_by(desc(AgentNotebookEntry.created_at), desc(AgentNotebookEntry.id))
    )
    with ctx.session_factory() as s:
        rows = list(s.scalars(stmt))
    notes = [NoteOut(id=str(r.id), agent=r.agent, note_md=r.note_md, created_at=r.created_at) for r in rows]
    return ToolResult(data=NotebookOut(day=day, notes=notes), as_of=ctx.now(), source="atlas.agent_notebook")


@atlas_tool("memory", "append_notebook", AppendNotebookInput, AppendedOut)
async def append_notebook(ctx: ToolContext, args: AppendNotebookInput) -> ToolResult:
    """Add a note to today's shared notebook (Asia/Baghdad date). Notes are append-only."""
    _no_writes_in_replay(ctx)
    note = args.note.strip()
    if not note:
        raise ToolError("invalid_input", "note is empty")
    now = ctx.now()
    day = usage_day(now)
    row = AgentNotebookEntry(
        env=ctx.env, day=day, agent=ctx.agent, note_md=note, created_at=now, updated_at=now
    )
    with ctx.session_factory() as s:
        s.add(row)
        s.commit()
        row_id = str(row.id)
    return ToolResult(
        data=AppendedOut(id=row_id, day=day, created_at=now), as_of=now, source="atlas.agent_notebook"
    )


# -- scores ------------------------------------------------------------------------------------------


class ScoreOut(BaseModel):
    day: date
    n: int
    hit_rate: float | None
    brier_score: float | None
    calibration: dict[str, Any]
    pnl_attributed_usd: float | None
    data: dict[str, Any]


class ScoresOut(BaseModel):
    agent: str
    days: int
    scores: list[ScoreOut]


@atlas_tool("memory", "get_my_scores", ScoresInput, ScoresOut)
async def get_my_scores(ctx: ToolContext, args: ScoresInput) -> ToolResult:
    """Your own daily scorecard for the last `days` Asia/Baghdad days, newest first: n, hit rate,
    Brier score, calibration buckets and attributed PnL. Use it to adjust your confidence."""
    today = usage_day(ctx.now())
    stmt = select(AgentScore).where(
        AgentScore.agent == ctx.agent,
        AgentScore.env == ctx.env,
        AgentScore.day > today - timedelta(days=args.days),
        AgentScore.day <= today,
        ctx.bound(AgentScore.created_at),
    )
    if ctx.replaying:
        stmt = stmt.where(AgentScore.day < today)  # a day is scored after it ends
    stmt = stmt.order_by(desc(AgentScore.day))
    with ctx.session_factory() as s:
        rows = list(s.scalars(stmt))
    scores = [
        ScoreOut(
            day=r.day,
            n=int(r.n),
            hit_rate=_f(r.hit_rate),
            brier_score=_f(r.brier_score),
            calibration=dict(r.calibration or {}),
            pnl_attributed_usd=_f(r.pnl_attributed_usd),
            data=dict(r.data or {}),
        )
        for r in rows
    ]
    data = ScoresOut(agent=ctx.agent, days=args.days, scores=scores)
    return ToolResult(data=data, as_of=ctx.now(), source="atlas.agent_scores")
