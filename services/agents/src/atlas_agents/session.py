"""One agent's long-lived Claude session on the owner's logged-in Claude Code CLI (decision #242).

AgentSession wraps one ClaudeSDKClient. Every ``run()``:

1. refuses (status ``over_budget``, no client call) when today's tokens or turns are at budget;
2. refuses (status ``rate_limited``, no client call) while a known usage-limit reset is pending;
3. otherwise writes an ``agent_runs`` row, sets the agent ``thinking``, sends the prompt and reads
   the response, then records the outcome, adds usage to ``agent_usage_daily`` and sets the state.

Nothing a run does raises out of ``run()`` except a database failure; the supervisor catches that.

The CLI must bill the owner's subscription, never pay-as-you-go: the subprocess env never carries
ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN. The SDK merges ``os.environ`` into the child's env at
connect time (and ``options.env`` can only add or override keys, not remove them), so the runtime
removes both keys from this process's own environment before every connect, and refuses options
that pass either explicitly.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator, Callable, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final, Literal, Protocol

from atlas_shared.db import AgentRun, add_usage, usage_day
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    RateLimitEvent,
    ResultError,
    ResultMessage,
    TextBlock,
)
from claude_agent_sdk.types import Message
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from atlas_agents import budget, events
from atlas_agents.registry import AgentSpec, budget_warning_fraction
from atlas_agents.states import State, set_state

log = logging.getLogger(__name__)

FORBIDDEN_ENV: Final = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
ERROR_MAX_CHARS: Final = 1000
RATE_LIMIT_TEXT: Final = re.compile(r"usage limit|rate[ _-]?limit|\b429\b|limit reached", re.IGNORECASE)

RunStatus = Literal["ok", "error", "rate_limited", "over_budget", "degraded"]
Trigger = Literal["mention", "schedule", "eval", "manual"]


class ForbiddenCredentialError(ValueError):
    """Raised when options try to hand the CLI an Anthropic API key or auth token."""


class ClaudeClient(Protocol):
    """The slice of ClaudeSDKClient that AgentSession uses (FakeClaudeClient implements it too)."""

    async def connect(self, prompt: str | None = None) -> None: ...
    async def disconnect(self) -> None: ...
    async def set_model(self, model: str | None = None) -> None: ...
    async def query(self, prompt: str, session_id: str = "default") -> None: ...
    def receive_response(self) -> AsyncIterator[Message]: ...


ClientFactory = Callable[[ClaudeAgentOptions], ClaudeClient]


class RunObserver(Protocol):
    """Sees every SDK message of one run as it streams, then the run's result (ATL-88:
    atlas_agents.streaming.ThinkingPublisher). Its failures are logged and never fail the run."""

    async def on_message(self, message: Message) -> None: ...
    async def on_finish(self, result: RunResult) -> None: ...


@dataclass(frozen=True)
class Review:
    """A reviewer's decision on one attempt of a run (ATL-93: output validation, brief §6.3).

    * ``follow_up``: send this prompt in the same run and review the answer again;
    * ``message``: accept; these keys override the run's final ``warroom.message`` payload
      (kind, card, content_md, thread_id, reply_to, ...);
    * ``failure``: give up; the run ends ``degraded`` and no message is published.

    A Review with none of the three accepts the run as it is (the default final message).
    """

    follow_up: str | None = None
    message: dict[str, Any] | None = None
    failure: str | None = None


class Reviewer(Protocol):
    """Called after every attempt with that attempt's assistant text, every SDK message of the
    run so far and the attempt number (1 = the first answer)."""

    async def __call__(self, text: str, messages: list[Message], attempt: int) -> Review: ...


ObserverFactory = Callable[[uuid.UUID, str | None], RunObserver]
"""(run_id, correlation_id) -> the observer for that run."""
Clock = Callable[[], datetime]


def scrub_process_env(environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Remove the forbidden keys from this process's environment; return the names removed."""
    target = os.environ if environ is None else environ
    return [key for key in FORBIDDEN_ENV if target.pop(key, None) is not None]


def cli_env(environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """A copy of ``environ`` (default ``os.environ``) without the forbidden keys."""
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items() if k not in FORBIDDEN_ENV}


def default_client_factory(options: ClaudeAgentOptions) -> ClaudeClient:
    scrub_process_env()
    return ClaudeSDKClient(options=options)


@dataclass
class RunResult:
    run_id: uuid.UUID
    status: RunStatus
    text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    turns: int = 0
    cost_usd_notional: Decimal = Decimal(0)
    error: str | None = None
    resets_at: datetime | None = None
    messages: list[Message] = field(default_factory=list)
    message: dict[str, Any] | None = None
    """Overrides for the run's final warroom.message payload, set by an accepting Review."""
    attempts: int = 1


@dataclass
class _Exchange:
    """What one run's queries produced, before it is classified."""

    messages: list[Message] = field(default_factory=list)
    results: list[ResultMessage] = field(default_factory=list)
    rate_limited: bool = False
    resets_at: datetime | None = None
    error: str | None = None

    @property
    def result(self) -> ResultMessage | None:
        return self.results[-1] if self.results else None

    @property
    def failed(self) -> bool:
        return self.rate_limited or self.error is not None


def _assistant_text(messages: list[Message]) -> str:
    return "".join(
        block.text
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, TextBlock)
    )


def _clip(text: str) -> str:
    return text if len(text) <= ERROR_MAX_CHARS else text[: ERROR_MAX_CHARS - 1] + "…"


def _from_unix(value: int | float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(value, UTC)


def _tokens(usage: dict[str, Any] | None) -> tuple[int, int]:
    """(tokens_in, tokens_out). tokens_in counts every input token the model read, cache included."""
    if not usage:
        return 0, 0
    tokens_in = sum(
        int(usage.get(key) or 0)
        for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )
    return tokens_in, int(usage.get("output_tokens") or 0)


class AgentSession:
    """One agent's Claude session plus its run bookkeeping."""

    def __init__(
        self,
        spec: AgentSpec,
        *,
        env: str,
        engine: Engine,
        options: ClaudeAgentOptions | None = None,
        client_factory: ClientFactory = default_client_factory,
        publisher: events.Publisher = events.bus_publisher,
        prompt_version: int | None = None,
        clock: Clock | None = None,
        observer_factory: ObserverFactory | None = None,
        system_prompt: Callable[[], str] | None = None,
    ) -> None:
        base = options or ClaudeAgentOptions()
        passed = [key for key in FORBIDDEN_ENV if key in base.env]
        if passed:
            raise ForbiddenCredentialError(
                f"Agents run on the Claude CLI login; options.env must not set {', '.join(passed)}"
            )
        self.spec = spec
        self.env = env
        self.prompt_version = prompt_version
        # Rendered at every connect, so a new session picks up current settings and scores (ATL-90).
        self._system_prompt = system_prompt
        self._engine = engine
        self._factory = client_factory
        self._publish = publisher
        self._observer_factory = observer_factory
        self._clock: Clock = clock or (lambda: datetime.now(UTC))
        self.options = dataclasses.replace(
            base,
            env={**cli_env(), **base.env},
            model=base.model or spec.model,
            max_turns=base.max_turns or spec.max_turns_per_run,
            # Built-in tools (Bash, Edit, ...) are off unless ATL-84's access matrix adds some.
            tools=[] if base.tools is None else base.tools,
            # Never load the owner's ~/.claude settings, CLAUDE.md files or skills into an agent.
            setting_sources=[] if base.setting_sources is None else base.setting_sources,
            skills=[] if base.skills is None else base.skills,
        )
        self._client: ClaudeClient | None = None
        self._model: str | None = self.options.model
        self._last_total_cost: Decimal = Decimal(0)
        self._rate_limited_until: datetime | None = None

    # -- client lifecycle -------------------------------------------------------------------------

    async def _ensure_client(self) -> ClaudeClient:
        if self._client is None:
            scrub_process_env()
            if self._system_prompt is not None:
                self.options = dataclasses.replace(self.options, system_prompt=self._system_prompt())
            client = self._factory(self.options)
            await client.connect()
            self._client = client
            self._model = self.options.model
            self._last_total_cost = Decimal(0)
        return self._client

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # a dead CLI must not stop the supervisor from moving on
                log.warning("agent %s: disconnect failed", self.spec.slug, exc_info=True)

    # -- events -----------------------------------------------------------------------------------

    def _emit(self, type_: str, payload: dict[str, Any], correlation_id: str | None) -> None:
        try:
            self._publish(
                events.SYSTEM_STREAM,
                events.envelope(
                    type_=type_,
                    env=self.env,
                    agent=self.spec.slug,
                    payload=payload,
                    correlation_id=correlation_id,
                ),
            )
        except Exception:  # Redis down must not fail a run; Postgres already has the record
            log.warning("agent %s: could not publish %s", self.spec.slug, type_, exc_info=True)

    def _state(
        self,
        db: Session,
        state: State,
        *,
        reason: str,
        run_id: uuid.UUID | None,
        correlation_id: str | None,
        current_task: str | None = None,
        last_error: str | None = None,
        resets_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Set the state; return the system.agent_state payload to publish if it changed."""
        previous = set_state(
            db,
            agent=self.spec.slug,
            env=self.env,
            state=state,
            current_task=current_task,
            last_error=last_error,
            now=self._clock(),
        )
        if previous == state:
            return None
        return {
            "agent": self.spec.slug,
            "state": state,
            "previous_state": previous,
            "reason": reason,
            "run_id": str(run_id) if run_id else None,
            "current_task": current_task,
            "last_error": last_error,
            "resets_at": resets_at.isoformat() if resets_at else None,
        }

    # -- running ----------------------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        *,
        trigger: Trigger = "manual",
        task: str | None = None,
        correlation_id: str | None = None,
        routine: bool = False,
        reviewer: Reviewer | None = None,
        max_follow_ups: int = 1,
    ) -> RunResult:
        """Send ``prompt`` to the agent and record the run. ``routine`` uses the cheaper model.

        With a ``reviewer``, each answer is reviewed; the reviewer may ask for at most
        ``max_follow_ups`` follow-up queries inside this same run (same run_id, same stream)."""
        started = self._clock()
        day = usage_day(started)
        model = self.spec.routine_model if routine else self.spec.model
        run_id = uuid.uuid4()
        label = task or trigger

        refusal = self._refusal(started, day, run_id, model, trigger, correlation_id)
        if refusal is not None:
            # A refused run still ends its (empty) live row with run_end.
            await self._finish_observer(self._observer(run_id, correlation_id), refusal)
            return refusal

        with Session(self._engine) as db:
            db.add(
                AgentRun(
                    id=run_id,
                    env=self.env,
                    agent=self.spec.slug,
                    trigger=trigger,
                    correlation_id=correlation_id,
                    started_at=started,
                    status="running",
                    model=model,
                    prompt_version=self.prompt_version,
                    created_at=started,
                    updated_at=started,
                )
            )
            change = self._state(
                db,
                "thinking",
                reason="run_started",
                run_id=run_id,
                correlation_id=correlation_id,
                current_task=label,
            )
            db.commit()
        if change:
            self._emit(events.AGENT_STATE, change, correlation_id)

        observer = self._observer(run_id, correlation_id)
        exchange = _Exchange()
        await self._exchange(prompt, model, observer, exchange)
        review = Review()
        attempts = 1
        if reviewer is not None:
            start = 0
            while not exchange.failed:
                answer = _assistant_text(exchange.messages[start:])
                try:
                    review = await reviewer(answer, list(exchange.messages), attempts)
                except Exception as exc:  # a broken reviewer degrades the run, never the process
                    log.exception("agent %s: reviewer failed", self.spec.slug)
                    review = Review(failure=f"reviewer failed: {type(exc).__name__}: {exc}")
                follow_up = review.follow_up
                if follow_up is None:
                    break
                if attempts > max_follow_ups:
                    review = Review(failure=review.failure or "output still invalid after the follow-up")
                    break
                start = len(exchange.messages)
                attempts += 1
                await self._exchange(follow_up, model, observer, exchange)
        result = self._finish(run_id, day, model, label, correlation_id, exchange, review, attempts)
        await self._finish_observer(observer, result)
        return result

    async def _finish_observer(self, observer: RunObserver | None, result: RunResult) -> None:
        if observer is None:
            return
        try:
            await observer.on_finish(result)
        except Exception:  # the stream is best effort; the run is already recorded
            log.warning("agent %s: run observer failed on finish", self.spec.slug, exc_info=True)

    def _observer(self, run_id: uuid.UUID, correlation_id: str | None) -> RunObserver | None:
        if self._observer_factory is None:
            return None
        try:
            return self._observer_factory(run_id, correlation_id)
        except Exception:
            log.warning("agent %s: could not build the run observer", self.spec.slug, exc_info=True)
            return None

    def _refusal(
        self,
        now: datetime,
        day: date,
        run_id: uuid.UUID,
        model: str,
        trigger: Trigger,
        correlation_id: str | None,
    ) -> RunResult | None:
        """Write a refused run (no client call) when over budget or inside a known rate-limit window."""
        status: RunStatus
        state: State
        with Session(self._engine) as db:
            usage = budget.today_usage(db, agent=self.spec.slug, env=self.env, day=day)
            hit = budget.exhausted(self.spec, usage)
            if hit:
                status, state = "over_budget", "degraded"
                error = (
                    f"over_budget: daily {' and '.join(hit)} budget reached "
                    f"({usage.tokens}/{self.spec.daily_tokens} tokens, "
                    f"{usage.turns}/{self.spec.daily_turns} turns)"
                )
                resets_at = None
            elif self._rate_limited_until is not None and now < self._rate_limited_until:
                status, state = "rate_limited", "rate_limited"
                resets_at = self._rate_limited_until
                error = f"rate_limited: Claude usage limit, resets at {resets_at.isoformat()}"
            else:
                return None

            db.add(
                AgentRun(
                    id=run_id,
                    env=self.env,
                    agent=self.spec.slug,
                    trigger=trigger,
                    correlation_id=correlation_id,
                    started_at=now,
                    ended_at=now,
                    status=status,
                    model=model,
                    prompt_version=self.prompt_version,
                    error=error,
                    created_at=now,
                    updated_at=now,
                )
            )
            change = self._state(
                db,
                state,
                reason=status,
                run_id=run_id,
                correlation_id=correlation_id,
                last_error=error,
                resets_at=resets_at,
            )
            db.commit()
        if change:
            self._emit(events.AGENT_STATE, change, correlation_id)
        return RunResult(run_id=run_id, status=status, error=error, resets_at=resets_at)

    async def _exchange(
        self, prompt: str, model: str, observer: RunObserver | None, out: _Exchange
    ) -> _Exchange:
        """Send one query and add what it produced to ``out``."""
        try:
            client = await self._ensure_client()
            if model != self._model:
                await client.set_model(model)
                self._model = model
            await client.query(prompt)
            async for message in client.receive_response():
                out.messages.append(message)
                if observer is not None:
                    try:
                        await observer.on_message(message)
                    except Exception:  # never let the live stream break the run
                        log.warning("agent %s: run observer failed", self.spec.slug, exc_info=True)
                if isinstance(message, RateLimitEvent):
                    info = message.rate_limit_info
                    if info.status == "rejected":
                        out.rate_limited = True
                        out.resets_at = _from_unix(info.resets_at) or out.resets_at
                elif isinstance(message, AssistantMessage) and message.error == "rate_limit":
                    out.rate_limited = True
                elif isinstance(message, ResultMessage):
                    out.results.append(message)
                    if message.is_error:
                        detail = message.result or "; ".join(message.errors or []) or message.subtype
                        out.error = _clip(detail)
                        if message.api_error_status == 429 or RATE_LIMIT_TEXT.search(detail):
                            out.rate_limited = True
        except ResultError as exc:
            out.error = _clip(str(exc))
            if exc.api_error_status == 429 or RATE_LIMIT_TEXT.search(str(exc)):
                out.rate_limited = True
            await self.close()
        except Exception as exc:  # a run never crashes the supervisor
            out.error = _clip(f"{type(exc).__name__}: {exc}")
            if RATE_LIMIT_TEXT.search(str(exc)):
                out.rate_limited = True
            await self.close()
        return out

    def _usage(self, exchange: _Exchange) -> tuple[int, int, int, Decimal]:
        """tokens_in, tokens_out, turns and the notional cost of this run."""
        result = exchange.result
        if result is not None:
            # Every query of the run (a reviewed run can have a follow-up) adds its own usage.
            per_query = [_tokens(r.usage) for r in exchange.results]
            tokens_in = sum(t[0] for t in per_query)
            tokens_out = sum(t[1] for t in per_query)
            turns = sum(r.num_turns for r in exchange.results)
            cost = Decimal(0)
            if result.total_cost_usd is not None:
                # The CLI reports the session's running total; a run costs the difference.
                total = Decimal(str(result.total_cost_usd))
                cost = total - self._last_total_cost if total >= self._last_total_cost else total
                self._last_total_cost = total
            return tokens_in, tokens_out, turns, cost
        # No result (the run died mid-way): count what the assistant messages reported.
        seen: dict[str, tuple[int, int]] = {}
        for message in exchange.messages:
            if isinstance(message, AssistantMessage) and message.usage:
                seen[message.message_id or str(id(message))] = _tokens(message.usage)
        return sum(t[0] for t in seen.values()), sum(t[1] for t in seen.values()), len(seen), Decimal(0)

    def _finish(
        self,
        run_id: uuid.UUID,
        day: date,
        model: str,
        label: str,
        correlation_id: str | None,
        exchange: _Exchange,
        review: Review | None = None,
        attempts: int = 1,
    ) -> RunResult:
        ended = self._clock()
        tokens_in, tokens_out, turns, cost = self._usage(exchange)
        status: RunStatus
        state: State
        if exchange.rate_limited:
            status, state = "rate_limited", "rate_limited"
            self._rate_limited_until = exchange.resets_at
            error = exchange.error or "rate_limited: Claude usage limit reached"
            if exchange.resets_at:
                error = f"{error} (resets at {exchange.resets_at.isoformat()})"
        elif exchange.error is not None:
            status, state, error = "error", "degraded", exchange.error
        elif review is not None and review.failure is not None:
            # Invalid output after the follow-up (brief §6.3): the agent is marked degraded.
            status, state, error = "degraded", "degraded", review.failure
        else:
            status, state, error = "ok", "idle", None
            self._rate_limited_until = None
        error = _clip(error) if error else None
        text = _assistant_text(exchange.messages)

        fraction = budget_warning_fraction()
        with Session(self._engine) as db:
            run = db.get(AgentRun, run_id)
            assert run is not None
            run.ended_at = ended
            run.status = status
            run.model = model
            run.tokens_in = tokens_in
            run.tokens_out = tokens_out
            run.turns = turns
            run.cost_usd_notional = cost
            run.error = error
            run.updated_at = ended
            before = budget.today_usage(db, agent=self.spec.slug, env=self.env, day=day)
            add_usage(
                db,
                agent=self.spec.slug,
                env=self.env,
                day=day,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                turns=turns,
                runs=1,
            )
            after = before.plus(tokens_in + tokens_out, turns)
            change = self._state(
                db,
                state,
                reason=status,
                run_id=run_id,
                correlation_id=correlation_id,
                current_task=None,
                last_error=error,
                resets_at=exchange.resets_at,
            )
            db.commit()

        if change:
            self._emit(events.AGENT_STATE, change, correlation_id)
        crossed = budget.crossed(self.spec, before, after, fraction)
        if crossed:
            self._emit(
                events.AGENT_BUDGET,
                budget.budget_payload(self.spec, day=day, usage=after, metrics=crossed, fraction=fraction),
                correlation_id,
            )
        log.info("agent %s run %s %s (%s)", self.spec.slug, run_id, status, label)
        return RunResult(
            run_id=run_id,
            status=status,
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            turns=turns,
            cost_usd_notional=cost,
            error=error,
            resets_at=exchange.resets_at,
            messages=exchange.messages,
            message=review.message if review is not None and status == "ok" else None,
            attempts=attempts,
        )
