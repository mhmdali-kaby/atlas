"""The Orchestrator's analysis cycle, in code (brief §7.9 state machine, §8; ATL-160).

One cycle walks ``cycles.state`` through::

    cycle_start → research_requests_sent → research_collected → proposals_open → reviews_done
      → voting → vote_closed → risk_check → awaiting_owner | executing → cycle_closed

and may close early (no watchlist, no new proposals, no proposal, nothing passed). Every state it
enters is stamped in ``cycles.transitions``; every late agent the cycle went on without is appended
to ``cycles.timeouts``; each transition publishes ``cycle.state_changed`` on ``atlas.system`` and
posts one short system line in the War Room.

Steps:

1. Research: one Technical Analyst and one Derivatives run per current watchlist instrument
   (``agents.orchestrator.research_agents``), all started at once, waited for up to
   ``research_timeout_seconds``. A run still going at the deadline is recorded as a timeout and left
   to finish on its own; the cycle goes on.
2. If the Technical Analyst or the Strategist (``blocking_agents``) is not enabled or is degraded /
   rate-limited / paused, the cycle closes with ``no_new_proposals`` and the reason.
3. The Strategist plans every watchlist instrument (``agents.strategist.run_cycle``), proposals
   carry this cycle's id.
4. Each open proposal waits up to ``review_timeout_seconds`` for its Devil's Advocate ``da_review``.
5. The vote opens (status ``voting``) for ``vote_seconds`` and is closed by
   :func:`atlas_agents.votes.close_vote`. Opening it starts one vote run per available
   ``agents.votes.run_voters`` agent (:mod:`atlas_agents.orchestrator.voters`, ATL-182); a version
   revised during the vote gets fresh runs. The vote closes earlier when every voter has voted and
   no vote run on a current version is still going.
6. Each passed proposal gets exactly one signed ``POST /internal/risk/evaluate/{id}``. A rejected
   check closes the proposal ``rejected``; an approved/resized one becomes ``advise_only`` at
   autonomy 1, else ``awaiting_owner`` with ``approval_expires_at``. While autonomy 3-4 are not
   enabled (``autonomy.max_enabled_level`` = 2) a ``send_now`` decision is treated as
   ``awaiting_owner``: this module never calls the Executor, so ``executing`` is not reached yet.

Timing goes through a :class:`CycleClock` so tests drive a simulated clock.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Any, Final, Protocol

from atlas_shared.db import AgentState, Cycle, Proposal, Vote, owner_day
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Table, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from atlas_agents import budget, votes
from atlas_agents import proposals as proposal_reads
from atlas_agents.orchestrator import voters as voter_runs
from atlas_agents.orchestrator.trading_core import TradingCoreClient, TradingCoreError
from atlas_agents.registry import AgentHost
from atlas_agents.tools import ToolContext
from atlas_agents.tools.framework import RunScope, ToolError
from atlas_agents.tools.warroom import MAIN_THREAD, message_envelope
from atlas_agents.watchlists import current_watchlist, listed_ids

log = logging.getLogger(__name__)

SLUG: Final = "orchestrator"
SOURCE: Final = "system:orchestrator"
SYSTEM_STREAM: Final = "atlas.system"
WARROOM_STREAM: Final = "atlas.warroom"
PROPOSALS_STREAM: Final = "atlas.proposals"
STATE_EVENT: Final = "cycle.state_changed"

STATES: Final = (
    "cycle_start",
    "research_requests_sent",
    "research_collected",
    "proposals_open",
    "reviews_done",
    "voting",
    "vote_closed",
    "risk_check",
    "awaiting_owner",
    "executing",
    "cycle_closed",
)

ResearchFn = Callable[..., Awaitable[Any]]
"""``(host, inst_id, *, trigger, correlation_id, why) -> RunResult | None`` (the analysts' analyse)."""
StrategistFn = Callable[[AgentHost, str], Awaitable[Any]]
"""``(host, cycle_id) -> Any``: the Strategist's cycle run."""


def config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = defaults["agents"]["orchestrator"]
    return cfg


def correlation_id(cycle_id: uuid.UUID | str) -> str:
    """``cycle-<uuid>``: submit_proposal reads the cycle id from it (ATL-153)."""
    return f"cycle-{cycle_id}"


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


# -- the schedule (brief §8) ------------------------------------------------------------------------


def _hhmm(text: str) -> time:
    hours, minutes = str(text).split(":")
    return time(int(hours), int(minutes))


def due_slot(now: datetime, defaults: Mapping[str, Any] = DEFAULTS) -> datetime | None:
    """The analysis slot (UTC) due at ``now``: the latest ``analysis_first_slot + k x
    analysis_interval_minutes`` at or before ``now`` (Asia/Baghdad), if it is at or before
    ``analysis_last_slot``. None before the first slot and after the last one."""
    from zoneinfo import ZoneInfo

    sched = defaults["agents"]["schedule"]
    zone = ZoneInfo(str(defaults["timezone"]["display"]))
    local = now.astimezone(zone)
    first = datetime.combine(local.date(), _hhmm(sched["analysis_first_slot"]), tzinfo=zone)
    last = datetime.combine(local.date(), _hhmm(sched["analysis_last_slot"]), tzinfo=zone)
    interval = timedelta(minutes=int(sched["analysis_interval_minutes"]))
    if local < first:
        return None
    slot = first + interval * math.floor((local - first) / interval)
    if slot > last:
        return None
    return slot.astimezone(UTC)


# -- the clock ----------------------------------------------------------------------------------------


class CycleClock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...

    async def wait(self, tasks: set[asyncio.Task[Any]], seconds: float) -> None:
        """Return when every task is done or ``seconds`` have passed."""
        ...


@dataclasses.dataclass
class WallClock:
    clock: Callable[[], datetime]

    def now(self) -> datetime:
        return self.clock()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))

    async def wait(self, tasks: set[asyncio.Task[Any]], seconds: float) -> None:
        if tasks:
            await asyncio.wait(tasks, timeout=max(0.0, seconds))


# -- the cycle ---------------------------------------------------------------------------------------


def _default_research() -> dict[str, ResearchFn]:
    from atlas_agents import technical_analyst
    from atlas_agents.derivatives import report

    return {technical_analyst.SLUG: technical_analyst.analyse, report.SLUG: report.analyse}


async def _default_strategist(host: AgentHost, cycle_id: str) -> Any:
    from atlas_agents.agents import strategist

    return await strategist.run_cycle(host, cycle_id)


@dataclasses.dataclass
class _Open:
    """A proposal the cycle follows; ``head`` moves to the newest version when one is revised."""

    root: uuid.UUID
    head: uuid.UUID
    inst_id: str


class CycleRunner:
    """Runs one cycle. ``research``/``strategist``/``risk``/``clock`` are injectable for tests."""

    def __init__(
        self,
        host: AgentHost,
        *,
        clock: CycleClock | None = None,
        research: Mapping[str, ResearchFn] | None = None,
        strategist: StrategistFn | None = None,
        risk: TradingCoreClient | None = None,
        voter_run: voter_runs.VoteRunFn | None = None,
        defaults: Mapping[str, Any] = DEFAULTS,
    ) -> None:
        self.host = host
        base = host.tool_context(SLUG)
        self.ctx: ToolContext = dataclasses.replace(base, publish=host.publish, run=RunScope())
        self.clock: CycleClock = clock or WallClock(base.now)
        self.research = dict(research) if research is not None else _default_research()
        self.strategist = strategist or _default_strategist
        self.risk = risk or TradingCoreClient()
        self.voter_run: voter_runs.VoteRunFn = voter_run or voter_runs.run_vote
        self.vote_tasks: dict[uuid.UUID, dict[str, asyncio.Task[Any]]] = {}
        """proposal version id -> agent -> its vote run (ATL-182)."""
        self.defaults = defaults
        self.cfg = config(defaults)
        self.late: set[asyncio.Task[Any]] = set()

    # -- rows and events -------------------------------------------------------------------------

    def _session(self) -> Session:
        return self.ctx.session_factory()

    def create(self, kind: str, slot_at: datetime | None = None) -> uuid.UUID | None:
        """Insert the cycle row in ``cycle_start`` and announce it; None when the slot already has a
        cycle."""
        at = self.clock.now()
        cid = uuid.uuid4()
        table: Table = Cycle.__table__  # type: ignore[assignment]
        stmt = (
            insert(table)
            .values(
                id=cid,
                env=self.host.env,
                kind=kind,
                state="cycle_start",
                started_at=at,
                slot_at=slot_at,
                deadlines={},
                transitions={"cycle_start": _iso(at)},
                timeouts=[],
                created_at=at,
                updated_at=at,
            )
            .on_conflict_do_nothing(index_elements=[table.c.env, table.c.slot_at])
            .returning(table.c.id)
        )
        with self._session() as session:
            got = session.execute(stmt).scalar_one_or_none()
            session.commit()
        if got is not None:
            slot = f" for the {_iso(slot_at)} slot" if slot_at is not None else ""
            self._publish_state(
                got, "cycle_start", None, f"Analysis cycle started ({kind}){slot}.", kind=kind
            )
        return got

    def _publish_state(
        self, cycle_id: uuid.UUID, state: str, previous: str | None, line: str, **detail: Any
    ) -> None:
        at = self.clock.now()
        corr = correlation_id(cycle_id)
        self.host.publish(
            SYSTEM_STREAM,
            EventEnvelope(
                type=STATE_EVENT,
                ts=at,
                env=self.host.env,
                source=SOURCE,
                correlation_id=corr,
                payload={
                    "cycle_id": str(cycle_id),
                    "state": state,
                    "previous": previous,
                    "at": _iso(at),
                    **detail,
                },
            ),
        )
        self.system_line(cycle_id, state, line)

    def slot_taken(self, slot_at: datetime) -> bool:
        with self._session() as session:
            found = session.scalars(
                select(Cycle.id).where(Cycle.env == self.host.env, Cycle.slot_at == slot_at).limit(1)
            ).first()
        return found is not None

    def system_line(
        self, cycle_id: uuid.UUID | None, state: str, text: str, *, card_type: str = "cycle_state"
    ) -> None:
        """One short system message in the War Room's Main thread (author ``orchestrator``, source
        ``system:orchestrator``, kind ``text``, card ``{"type": card_type, "cycle_id", "state"}``)."""
        self.host.publish(
            WARROOM_STREAM,
            EventEnvelope(
                type="warroom.message",
                ts=self.clock.now(),
                env=self.host.env,
                source=SOURCE,
                correlation_id=correlation_id(cycle_id) if cycle_id is not None else None,
                payload={
                    "thread": dict(MAIN_THREAD),
                    "author_type": "system",
                    "author": SLUG,
                    "kind": "text",
                    "content_md": text,
                    "card": {
                        "type": card_type,
                        "cycle_id": str(cycle_id) if cycle_id is not None else None,
                        "state": state,
                    },
                    "reply_to": None,
                    "mentions": [],
                    "reasoning_md": None,
                    "tool_calls": None,
                    "tokens_in": None,
                    "tokens_out": None,
                    "cost_usd": None,
                },
            ),
        )

    def transition(
        self,
        cycle_id: uuid.UUID,
        state: str,
        line: str,
        *,
        deadlines: Mapping[str, datetime] | None = None,
        outcome: str | None = None,
        reason: str | None = None,
        **detail: Any,
    ) -> None:
        if state not in STATES:
            raise ValueError(f"unknown cycle state {state}")
        at = self.clock.now()
        with self._session() as session:
            row = session.get(Cycle, cycle_id, with_for_update=True)
            if row is None:
                raise LookupError(f"cycle {cycle_id} is gone")
            previous = row.state
            row.state = state
            row.transitions = {**(row.transitions or {}), state: _iso(at)}
            if deadlines:
                row.deadlines = {**(row.deadlines or {}), **{k: _iso(v) for k, v in deadlines.items()}}
            if state == "cycle_closed":
                row.closed_at = at
                row.outcome = outcome
                row.outcome_reason = reason
            row.updated_at = at
            session.commit()
        extra = dict(detail)
        if deadlines:
            extra["deadlines"] = {k: _iso(v) for k, v in deadlines.items()}
        if state == "cycle_closed":
            extra.update({"outcome": outcome, "reason": reason})
        self._publish_state(cycle_id, state, previous, line, **extra)

    def record_timeout(self, cycle_id: uuid.UUID, entry: Mapping[str, Any]) -> None:
        item = {**entry, "at": _iso(self.clock.now())}
        with self._session() as session:
            row = session.get(Cycle, cycle_id, with_for_update=True)
            if row is None:
                return
            row.timeouts = [*(row.timeouts or []), item]
            row.updated_at = self.clock.now()
            session.commit()

    def close(self, cycle_id: uuid.UUID, outcome: str, reason: str) -> str:
        self.transition(cycle_id, "cycle_closed", f"Cycle closed: {reason}", outcome=outcome, reason=reason)
        return outcome

    # -- reads -------------------------------------------------------------------------------------

    def watchlist(self) -> list[str]:
        with self._session() as session:
            rows = listed_ids(session, current_watchlist(session, self.ctx, owner_day(self.ctx.now())))
        cap = int(self.cfg["watchlist_max_instruments"])
        return list(dict.fromkeys(rows))[:cap]

    def enabled(self, slug: str) -> bool:
        sessions = getattr(self.host, "sessions", None)
        return True if sessions is None else slug in sessions

    def unavailable_reason(self, slug: str) -> str | None:
        if not self.enabled(slug):
            return f"{slug} is not enabled"
        with self._session() as session:
            row = session.scalars(
                select(AgentState)
                .where(AgentState.agent == slug, AgentState.env == self.host.env)
                .execution_options(populate_existing=True)
            ).one_or_none()
        if row is not None and row.state in set(self.cfg["unavailable_states"]):
            why = f": {row.last_error}" if row.last_error else ""
            return f"{slug} is {row.state}{why}"
        return None

    def over_budget_reason(self, slug: str) -> str | None:
        sessions = getattr(self.host, "sessions", None)
        session = sessions.get(slug) if sessions is not None else None
        spec = getattr(session, "spec", None)
        if spec is None:
            return None
        with self._session() as db:
            usage = budget.today_usage(db, agent=slug, env=self.host.env, day=owner_day(self.clock.now()))
        hit = budget.exhausted(spec, usage)
        return f"{slug} is over its daily {' and '.join(hit)} budget" if hit else None

    def voter_skip_reason(self, slug: str, version: uuid.UUID) -> str | None:
        """Why ``slug`` gets no vote run on ``version``: not enabled, unavailable, over budget, or it
        already voted on this version. None when it gets one."""
        reason = self.unavailable_reason(slug) or self.over_budget_reason(slug)
        if reason:
            return reason
        with self._session() as session:
            voted = session.scalars(
                select(Vote.id).where(Vote.proposal_id == version, Vote.agent == slug).limit(1)
            ).first()
        return f"{slug} already voted" if voted is not None else None

    def cycle_proposals(self, cycle_id: uuid.UUID) -> list[_Open]:
        with self._session() as session:
            rows = session.scalars(
                select(Proposal)
                .where(
                    Proposal.env == self.host.env,
                    Proposal.cycle_id == cycle_id,
                    Proposal.kind == "entry",
                    Proposal.status.in_(["under_review", "voting"]),
                )
                .order_by(Proposal.created_at)
            ).all()
            children = {r.parent_id for r in rows if r.parent_id is not None}
            return [_Open(root=r.id, head=r.id, inst_id=r.inst_id) for r in rows if r.id not in children]

    def _follow(self, session: Session, pid: uuid.UUID) -> Proposal | None:
        """The newest version of ``pid`` (a revision is a child row)."""
        current = session.get(Proposal, pid, populate_existing=True)
        seen = {pid}
        while current is not None:
            child = session.scalars(select(Proposal).where(Proposal.parent_id == current.id)).first()
            if child is None or child.id in seen:
                return current
            seen.add(child.id)
            current = child
        return None

    def refresh(self, items: list[_Open]) -> list[_Open]:
        """Move each to its newest version; drop withdrawn/closed ones."""
        out: list[_Open] = []
        with self._session() as session:
            for item in items:
                head = self._follow(session, item.head)
                if head is not None and head.status in ("under_review", "voting"):
                    out.append(dataclasses.replace(item, head=head.id))
        return out

    def has_review(self, item: _Open) -> bool:
        vcfg = votes.votes_config(self.defaults)
        with self._session() as session:
            head = session.get(Proposal, item.head)
            if head is None:
                return False
            chain = votes.version_chain(session, head)
            return votes.da_rating(session, self.ctx, chain, vcfg) is not None

    def missing_voters(self, item: _Open) -> list[str]:
        voters = [str(v) for v in votes.votes_config(self.defaults)["voters"]]
        with self._session() as session:
            head = session.get(Proposal, item.head)
            if head is None:
                return []
            chain = votes.version_chain(session, head)
            got = set(
                session.scalars(select(Vote.agent).where(Vote.proposal_id.in_([p.id for p in chain]))).all()
            )
        return [v for v in voters if v not in got]

    def autonomy_level(self) -> int:
        """The owner's autonomy level: settings.data, else the typed column, else the default."""
        with self._session() as session:
            data = proposal_reads.settings_data(session, self.ctx).data
            row = session.execute(
                text("SELECT autonomy_level FROM settings WHERE env = :e"), {"e": self.host.env}
            ).first()
        raw = data.get("autonomy_level", row[0] if row is not None else None)
        try:
            level = int(raw) if raw is not None else int(self.defaults["autonomy"]["default_level"])
        except (TypeError, ValueError):
            level = int(self.defaults["autonomy"]["default_level"])
        return level

    def approval_timeout(self) -> timedelta:
        with self._session() as session:
            data = proposal_reads.settings_data(session, self.ctx).data
        raw = data.get("approval_timeout_minutes", self.defaults["autonomy"]["approval_timeout_minutes"])
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            minutes = int(self.defaults["autonomy"]["approval_timeout_minutes"])
        return timedelta(minutes=max(1, minutes))

    # -- waits ---------------------------------------------------------------------------------------

    async def wait_until(self, deadline: datetime, done: Callable[[], bool]) -> bool:
        """Poll ``done`` (sync, run in a thread) until true or the deadline; True when it was met."""
        poll = float(self.cfg["poll_seconds"])
        while True:
            if await asyncio.to_thread(done):
                return True
            remaining = (deadline - self.clock.now()).total_seconds()
            if remaining <= 0:
                return False
            await self.clock.sleep(min(poll, remaining))

    # -- steps -----------------------------------------------------------------------------------------

    async def _agent_research(self, slug: str, inst_ids: Sequence[str], corr: str, done: set[str]) -> None:
        from atlas_agents.supervisor import AgentUnavailableError

        fn = self.research[slug]
        for inst_id in inst_ids:
            try:
                await fn(
                    self.host,
                    inst_id,
                    trigger="schedule",
                    correlation_id=corr,
                    why=f"Analysis cycle {corr}: analyse {inst_id} for the Strategist.",
                )
            except AgentUnavailableError:
                log.info("orchestrator: %s is not enabled; research on %s skipped", slug, inst_id)
                return
            except Exception:
                log.exception("orchestrator: %s research on %s failed", slug, inst_id)
            done.add(inst_id)

    async def research_step(self, cycle_id: uuid.UUID, inst_ids: list[str]) -> None:
        corr = correlation_id(cycle_id)
        timeout = float(self.cfg["research_timeout_seconds"])
        deadline = self.clock.now() + timedelta(seconds=timeout)
        agents = [a for a in self.research if a in set(self.cfg["research_agents"])]
        running = [a for a in agents if self.enabled(a)]
        skipped = [a for a in agents if a not in running]
        done: dict[str, set[str]] = {a: set() for a in running}
        tasks = {a: asyncio.create_task(self._agent_research(a, inst_ids, corr, done[a])) for a in running}
        skipped_note = f"; not enabled: {', '.join(skipped)}" if skipped else ""
        self.transition(
            cycle_id,
            "research_requests_sent",
            f"Research requested from {', '.join(running) or 'nobody'} on {', '.join(inst_ids)} "
            f"until {_iso(deadline)}{skipped_note}.",
            deadlines={"research": deadline},
            agents=running,
            skipped=skipped,
            inst_ids=inst_ids,
        )
        await self.clock.wait(set(tasks.values()), (deadline - self.clock.now()).total_seconds())
        late: list[str] = []
        for agent, task in tasks.items():
            if task.done():
                continue
            self.late.add(task)
            task.add_done_callback(self.late.discard)
            missing = [i for i in inst_ids if i not in done[agent]]
            late.append(agent)
            self.record_timeout(
                cycle_id,
                {"step": "research", "agent": agent, "inst_ids": missing, "deadline": _iso(deadline)},
            )
        line = (
            f"Research timed out for {', '.join(late)} at {_iso(deadline)}; continuing without them."
            if late
            else "Research collected."
        )
        self.transition(cycle_id, "research_collected", line, timed_out=late)

    async def strategist_step(self, cycle_id: uuid.UUID) -> None:
        timeout = float(self.cfg["strategist_timeout_seconds"])
        deadline = self.clock.now() + timedelta(seconds=timeout)

        async def call() -> Any:
            return await self.strategist(self.host, str(cycle_id))

        task = asyncio.create_task(call())
        await self.clock.wait({task}, timeout)
        if not task.done():
            self.late.add(task)
            task.add_done_callback(self.late.discard)
            self.record_timeout(
                cycle_id, {"step": "proposals", "agent": "strategist", "deadline": _iso(deadline)}
            )
        elif task.exception() is not None:
            log.error("orchestrator: the Strategist's cycle run failed", exc_info=task.exception())

    async def run(self, cycle_id: uuid.UUID) -> str:
        """Drive the cycle from ``cycle_start`` to ``cycle_closed``; returns its outcome."""
        inst_ids = await asyncio.to_thread(self.watchlist)
        if not inst_ids:
            return self.close(cycle_id, "no_watchlist", "no watchlist instruments today")
        await self.research_step(cycle_id, inst_ids)

        blocked: list[str] = []
        for slug in self.cfg["blocking_agents"]:
            reason = await asyncio.to_thread(self.unavailable_reason, str(slug))
            if reason:
                blocked.append(reason)
        if blocked:
            return self.close(cycle_id, "no_new_proposals", "no new proposals: " + "; ".join(blocked))

        await self.strategist_step(cycle_id)
        items = await asyncio.to_thread(self.cycle_proposals, cycle_id)
        review_deadline = self.clock.now() + timedelta(seconds=float(self.cfg["review_timeout_seconds"]))
        self.transition(
            cycle_id,
            "proposals_open",
            f"{len(items)} proposal(s) open"
            + (
                f" ({', '.join(i.inst_id for i in items)}); "
                f"Devil's Advocate review until {_iso(review_deadline)}."
                if items
                else "."
            ),
            deadlines={"review": review_deadline} if items else None,
            proposal_ids=[str(i.head) for i in items],
        )
        if not items:
            return self.close(cycle_id, "no_proposals", "the Strategist made no proposal")

        await self.wait_until(
            review_deadline,
            lambda: all(self.has_review(i) for i in self.refresh(items)),
        )
        items = await asyncio.to_thread(self.refresh, items)
        unreviewed = [i for i in items if not self.has_review(i)]
        for item in unreviewed:
            self.record_timeout(
                cycle_id,
                {
                    "step": "review",
                    "agent": "devils_advocate",
                    "proposal_id": str(item.head),
                    "inst_id": item.inst_id,
                    "deadline": _iso(review_deadline),
                },
            )
        self.transition(
            cycle_id,
            "reviews_done",
            "Reviews done"
            + (
                f"; no Devil's Advocate review on {', '.join(i.inst_id for i in unreviewed)} (timed out)."
                if unreviewed
                else "."
            ),
            unreviewed=[str(i.head) for i in unreviewed],
        )
        if not items:
            return self.close(cycle_id, "no_proposals", "every proposal was withdrawn")

        vote_deadline = self.clock.now() + timedelta(seconds=float(self.cfg["vote_seconds"]))
        await asyncio.to_thread(self._open_votes, items)
        self.transition(
            cycle_id,
            "voting",
            f"Vote open on {', '.join(i.inst_id for i in items)} until {_iso(vote_deadline)}.",
            deadlines={"vote": vote_deadline},
            proposal_ids=[str(i.head) for i in items],
        )
        await self.vote_step(cycle_id, items, vote_deadline)
        items = await asyncio.to_thread(self.refresh, items)
        passed: list[_Open] = []
        results: list[str] = []
        for item in items:
            for voter in await asyncio.to_thread(self.missing_voters, item):
                self.record_timeout(
                    cycle_id,
                    {
                        "step": "vote",
                        "agent": voter,
                        "proposal_id": str(item.head),
                        "inst_id": item.inst_id,
                        "deadline": _iso(vote_deadline),
                    },
                )
            try:
                closed = await asyncio.to_thread(votes.close_vote, self._vote_ctx(cycle_id), str(item.head))
            except ToolError as exc:
                log.warning("orchestrator: vote on %s not closed: %s", item.head, exc)
                results.append(f"{item.inst_id} not closed ({exc.code})")
                continue
            results.append(f"{item.inst_id} {closed.result}")
            if closed.result == "passed":
                passed.append(item)
        self.transition(
            cycle_id,
            "vote_closed",
            "Vote closed: " + ", ".join(results) + ".",
            passed=[str(i.head) for i in passed],
        )
        if not passed:
            return self.close(cycle_id, "nothing_passed", "no proposal passed the vote")

        self.transition(
            cycle_id, "risk_check", f"Risk Engine check on {', '.join(i.inst_id for i in passed)}."
        )
        awaiting: list[str] = []
        advised: list[str] = []
        level = await asyncio.to_thread(self.autonomy_level)
        for item in passed:
            outcome = await self._risk(cycle_id, item, level)
            if outcome == "awaiting_owner":
                awaiting.append(item.inst_id)
            elif outcome == "advise_only":
                advised.append(item.inst_id)
        if awaiting:
            self.transition(
                cycle_id,
                "awaiting_owner",
                f"Awaiting the owner's approval: {', '.join(awaiting)}.",
                inst_ids=awaiting,
            )
            return self.close(cycle_id, "awaiting_owner", f"awaiting approval on {', '.join(awaiting)}")
        if advised:
            return self.close(cycle_id, "advise_only", f"advice only (autonomy 1) on {', '.join(advised)}")
        return self.close(cycle_id, "risk_rejected", "the Risk Engine approved nothing")

    async def start_vote_runs(self, cycle_id: uuid.UUID, item: _Open, vote_deadline: datetime) -> list[str]:
        """One vote run per available run_voters agent on ``item.head``; returns the agents asked."""
        if item.head in self.vote_tasks:
            return []
        runs: dict[str, asyncio.Task[Any]] = {}
        self.vote_tasks[item.head] = runs
        timeout = timedelta(seconds=float(voter_runs.config(self.defaults)["voter_run_timeout_seconds"]))
        skipped: list[str] = []
        for slug in voter_runs.run_voters(self.defaults):
            reason = await asyncio.to_thread(self.voter_skip_reason, slug, item.head)
            if reason:
                skipped.append(reason)
                continue
            deadline = min(vote_deadline, self.clock.now() + timeout)
            runs[slug] = asyncio.create_task(self._vote_run(slug, item, deadline, correlation_id(cycle_id)))
        asked = list(runs)
        line = f"Votes requested on {item.inst_id} ({item.head}) from {', '.join(asked) or 'nobody'}"
        if skipped:
            line += f"; not asked: {'; '.join(skipped)}"
        self.system_line(cycle_id, "voting", line + ".", card_type="vote_runs")
        return asked

    async def _vote_run(self, slug: str, item: _Open, deadline: datetime, corr: str) -> Any:
        try:
            return await self.voter_run(self.host, slug, item.head, deadline=deadline, correlation_id=corr)
        except Exception:
            log.exception("orchestrator: %s vote run on %s failed", slug, item.head)
            return None

    def _all_voted(self, items: Sequence[_Open]) -> bool:
        return all(not self.missing_voters(i) for i in items)

    def _runs_pending(self, items: Sequence[_Open]) -> bool:
        return any(not t.done() for i in items for t in self.vote_tasks.get(i.head, {}).values())

    async def vote_step(self, cycle_id: uuid.UUID, items: list[_Open], vote_deadline: datetime) -> None:
        """Start the vote runs, and fresh ones for each version revised during the vote. Return at the
        deadline, or once every voter has voted and no run on a current version is still going. Runs
        still going at the deadline are left to finish; cast_vote refuses their late votes."""
        poll = float(self.cfg["poll_seconds"])
        current = items
        while True:
            current = await asyncio.to_thread(self.refresh, current)
            fresh = [i for i in current if i.head not in self.vote_tasks]
            if fresh:
                await asyncio.to_thread(self._open_votes, fresh)
                for item in fresh:
                    await self.start_vote_runs(cycle_id, item, vote_deadline)
            all_voted = await asyncio.to_thread(self._all_voted, current)
            if all_voted and not self._runs_pending(current):
                break
            remaining = (vote_deadline - self.clock.now()).total_seconds()
            if remaining <= 0:
                break
            await self.clock.sleep(min(poll, remaining))
        for runs in self.vote_tasks.values():
            for task in runs.values():
                if not task.done():
                    self.late.add(task)
                    task.add_done_callback(self.late.discard)

    def _open_votes(self, items: Sequence[_Open]) -> None:
        at = self.clock.now()
        with self._session() as session:
            session.execute(
                update(Proposal)
                .where(Proposal.id.in_([i.head for i in items]), Proposal.status == "under_review")
                .values(status="voting", updated_at=at)
            )
            session.commit()

    def _vote_ctx(self, cycle_id: uuid.UUID) -> ToolContext:
        return dataclasses.replace(self.ctx, correlation_id=correlation_id(cycle_id), run=RunScope())

    async def _risk(self, cycle_id: uuid.UUID, item: _Open, autonomy: int) -> str:
        """One risk check; the proposal's next status. Returns awaiting_owner, advise_only, rejected
        or error."""
        try:
            body = await self.risk.risk_evaluate(str(item.head))
        except TradingCoreError as exc:
            self.record_timeout(
                cycle_id,
                {
                    "step": "risk_check",
                    "agent": "trading_core",
                    "proposal_id": str(item.head),
                    "inst_id": item.inst_id,
                    "error": exc.code,
                },
            )
            self.system_line(cycle_id, "risk_check", f"Risk check on {item.inst_id} failed: {exc.code}.")
            return "error"
        ctx = self._vote_ctx(cycle_id)
        card = {"type": "risk_check", **{k: v for k, v in body.items() if k != "ok"}}
        content = f"**Risk {body.get('result')}** on {item.inst_id}: {body.get('reason') or ''}".strip()
        self.host.publish(
            WARROOM_STREAM, message_envelope(ctx, kind="risk_check", content_md=content, card=card)
        )

        at = self.clock.now()
        if body.get("result") == "rejected":
            status, expires = "rejected", None
        elif autonomy <= 1 or body.get("decision") == "advise_only":
            status, expires = "advise_only", None
        else:
            # awaiting_owner, and send_now while autonomy 3-4 are not enabled: the owner approves.
            status, expires = "awaiting_owner", at + await asyncio.to_thread(self.approval_timeout)
        values: dict[str, Any] = {"status": status, "updated_at": at}
        if expires is not None:
            values["approval_expires_at"] = expires
        with self._session() as session:
            row = session.execute(
                update(Proposal)
                .where(Proposal.id == item.head, Proposal.status == "risk_checked")
                .values(**values)
                .returning(Proposal.id, Proposal.version, Proposal.inst_id, Proposal.side)
            ).first()
            session.commit()
        if row is None:
            log.warning("orchestrator: proposal %s was not risk_checked after the check", item.head)
            return "error"
        if status in ("awaiting_owner", "advise_only"):
            self.host.publish(
                PROPOSALS_STREAM,
                EventEnvelope(
                    type=f"proposal.{status}",
                    ts=at,
                    env=self.host.env,
                    source=SOURCE,
                    correlation_id=correlation_id(cycle_id),
                    payload={
                        "proposal_id": str(row.id),
                        "version": row.version,
                        "cycle_id": str(cycle_id),
                        "inst_id": row.inst_id,
                        "side": row.side,
                        "risk_check_id": body.get("risk_check_id"),
                        "result": body.get("result"),
                        "decision": body.get("decision"),
                        "final_contracts": body.get("final_contracts"),
                        "final_notional_usd": body.get("final_notional_usd"),
                        "leverage": body.get("leverage"),
                        "approval_expires_at": _iso(expires) if expires else None,
                    },
                ),
            )
        return status


# -- starting cycles ---------------------------------------------------------------------------------

_running: dict[str, asyncio.Task[Any]] = {}


def running(env: str) -> bool:
    task = _running.get(env)
    return task is not None and not task.done()


async def settled() -> None:
    """Wait for every running cycle (tests, shutdown)."""
    while any(not t.done() for t in _running.values()):
        await asyncio.gather(*[t for t in _running.values() if not t.done()], return_exceptions=True)


def launch(runner: CycleRunner, cycle_id: uuid.UUID) -> asyncio.Task[Any]:
    async def go() -> None:
        try:
            await runner.run(cycle_id)
        except Exception:
            log.exception("orchestrator: cycle %s failed", cycle_id)
            try:
                runner.close(cycle_id, "error", "the cycle failed; see the agents log")
            except Exception:
                log.exception("orchestrator: could not close cycle %s", cycle_id)

    task = asyncio.create_task(go())
    _running[runner.host.env] = task
    return task


def start(
    host: AgentHost,
    kind: str,
    *,
    slot_at: datetime | None = None,
    runner: CycleRunner | None = None,
) -> uuid.UUID | None:
    """Create a cycle row and run it in the background. None when replaying, when a cycle is still
    running in this env, or when ``slot_at`` already has a cycle."""
    if host.replaying:
        return None
    if running(host.env):
        log.info("orchestrator: a cycle is still running in %s; %s cycle not started", host.env, kind)
        return None
    runner = runner or CycleRunner(host)
    cycle_id = runner.create(kind, slot_at)
    if cycle_id is None:
        return None
    launch(runner, cycle_id)
    return cycle_id


__all__ = [
    "STATES",
    "CycleClock",
    "CycleRunner",
    "WallClock",
    "config",
    "correlation_id",
    "due_slot",
    "launch",
    "running",
    "settled",
    "start",
]
