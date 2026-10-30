"""The nine agents and their runtime specs (brief §7, decisions #242/#245).

Slugs and display names come from ``warroom.agents`` in config/defaults.yaml; models, budgets and
the enabled flag from ``agents``. A roster entry only lists what differs from ``agents.defaults``.

It also holds the agent hook registry (ATL-101): each module in ``atlas_agents.agents`` registers
its mention handler and schedule hooks with :data:`HOOKS`; the supervisor discovers them.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Protocol

from atlas_shared.defaults import DEFAULTS

if TYPE_CHECKING:
    from atlas_shared.events import EventEnvelope

    from atlas_agents.mentions import MentionRequest
    from atlas_agents.session import RunResult
    from atlas_agents.tools import ToolContext


class UnknownAgentError(KeyError):
    """Raised for a slug that is not one of the nine agents."""


@dataclass(frozen=True)
class AgentSpec:
    slug: str
    name: str
    model: str
    routine_model: str
    enabled: bool
    daily_tokens: int
    daily_turns: int
    max_turns_per_run: int


def _positive_int(value: Any, field: str, slug: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"agents.{field} for [{slug}] must be a positive integer")
    return value


def _model(tier: Any, models: Mapping[str, str], slug: str) -> str:
    if not isinstance(tier, str) or tier not in models:
        raise ValueError(f"agents model tier [{tier}] for [{slug}] is not one of {sorted(models)}")
    return models[tier]


def build_registry(defaults: Mapping[str, Any] = DEFAULTS) -> dict[str, AgentSpec]:
    """Every agent's spec, in brief §7 order (the order of ``warroom.agents``)."""
    names: Mapping[str, Mapping[str, Any]] = defaults["warroom"]["agents"]
    config: Mapping[str, Any] = defaults["agents"]
    models: Mapping[str, str] = config["models"]
    base: Mapping[str, Any] = config["defaults"]
    roster: Mapping[str, Mapping[str, Any]] = config["roster"]

    unknown = set(roster) - set(names)
    if unknown:
        raise ValueError(f"agents.roster has unknown slugs: {sorted(unknown)}")

    specs: dict[str, AgentSpec] = {}
    for slug, meta in names.items():
        merged = {**base, **(roster.get(slug) or {})}
        specs[slug] = AgentSpec(
            slug=slug,
            name=str(meta["name"]),
            model=_model(merged["model"], models, slug),
            routine_model=_model(merged["routine_model"], models, slug),
            enabled=bool(merged["enabled"]),
            daily_tokens=_positive_int(merged["daily_tokens"], "daily_tokens", slug),
            daily_turns=_positive_int(merged["daily_turns"], "daily_turns", slug),
            max_turns_per_run=_positive_int(merged["max_turns_per_run"], "max_turns_per_run", slug),
        )
    return specs


REGISTRY: dict[str, AgentSpec] = build_registry()


def get_spec(slug: str, registry: Mapping[str, AgentSpec] = REGISTRY) -> AgentSpec:
    try:
        return registry[slug]
    except KeyError:
        raise UnknownAgentError(slug) from None


def enabled_agents(registry: Mapping[str, AgentSpec] = REGISTRY) -> list[AgentSpec]:
    return [spec for spec in registry.values() if spec.enabled]


def budget_warning_fraction(defaults: Mapping[str, Any] = DEFAULTS) -> Decimal:
    """``agents.budget_warning_pct`` as a fraction (``"80"`` -> 0.8)."""
    pct = Decimal(str(defaults["agents"]["budget_warning_pct"]))
    if not Decimal(0) < pct <= Decimal(100):
        raise ValueError("agents.budget_warning_pct must be in (0, 100]")
    return pct / Decimal(100)


# -- agent hooks (ATL-101, decision #247 (1)) -------------------------------------------------------
#
# Each agent module under ``atlas_agents.agents`` registers what the supervisor should do for it:
# a mention handler and schedule hooks. ``discover_hooks`` imports every module in that package, so
# adding an agent is adding one file; supervisor.py names no agent.


class AgentHost(Protocol):
    """What the supervisor offers a hook. Implemented by :class:`atlas_agents.supervisor.Supervisor`."""

    env: str

    @property
    def replaying(self) -> bool: ...

    def tool_context(self, agent: str) -> ToolContext: ...

    async def run(self, agent: str, prompt: str, **kwargs: Any) -> RunResult: ...

    def publish(self, stream: str, envelope: EventEnvelope) -> None: ...


MentionHandler = Callable[["AgentHost", "MentionRequest"], Awaitable["RunResult | None"]]
"""``(host, request) -> RunResult | None``: answer one mention, usually with ``host.run(slug, ...,
trigger="mention", correlation_id=request.correlation_id, thread_id=..., reply_to=...)``."""

EventHandler = Callable[["AgentHost", "EventEnvelope"], Awaitable["RunResult | None"]]
"""``(host, envelope) -> RunResult | None``: react to one ``atlas.warroom`` envelope of a registered
type that is not a mention (ATL-107: News Intelligence on ingest's ``news.alert``). Only the agents
that register a type are called for it."""

ScheduleRun = Callable[["AgentHost", "ScheduleFire"], Awaitable["RunResult | None"]]
"""``(host, fire) -> RunResult | None``: one firing of a schedule hook. Return None when nothing
ran (a daily job is then tried again at the next tick)."""


@dataclass(frozen=True)
class ScheduleFire:
    """One firing of a schedule hook. ``correlation_id`` must be passed to ``host.run`` with
    ``trigger="schedule"``: for a daily hook it is how "done today" is found in agent_runs."""

    name: str
    at: datetime
    day: date
    correlation_id: str


@dataclass(frozen=True)
class ScheduleHook:
    """A daily job at ``at`` ("HH:MM", Asia/Baghdad; when both ``at`` and ``every_seconds`` are None
    it is read from ``agents.schedule.<name>``) or an interval job every ``every_seconds``."""

    name: str
    run: ScheduleRun
    at: str | None = None
    every_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.at is not None and self.every_seconds is not None:
            raise ValueError(f"schedule hook {self.name}: give at or every_seconds, not both")
        if self.every_seconds is not None and self.every_seconds <= 0:
            raise ValueError(f"schedule hook {self.name}: every_seconds must be positive")

    @property
    def daily(self) -> bool:
        return self.every_seconds is None

    def time_of_day(self, defaults: Mapping[str, Any] = DEFAULTS) -> time:
        text = self.at if self.at is not None else str(defaults["agents"]["schedule"][self.name])
        hours, minutes = text.split(":")
        return time(int(hours), int(minutes))


@dataclass(frozen=True)
class AgentHooks:
    slug: str
    on_mention: MentionHandler | None = None
    schedule: tuple[ScheduleHook, ...] = ()
    on_event: Mapping[str, EventHandler] = field(default_factory=dict)
    """envelope type -> handler (ATL-107)."""


class HookRegistry:
    """Mention handlers and schedule hooks by agent slug. Register with the decorators::

    HOOKS = registry.HOOKS

    @HOOKS.mention("my_agent")
    async def answer(host: AgentHost, request: MentionRequest) -> RunResult | None: ...

    @HOOKS.schedule("my_agent", "my_agent_job")          # agents.schedule.my_agent_job
    async def job(host: AgentHost, fire: ScheduleFire) -> RunResult | None: ...

    @HOOKS.event("my_agent", "news.alert")               # any atlas.warroom envelope type
    async def on_alert(host: AgentHost, envelope: EventEnvelope) -> RunResult | None: ...
    """

    def __init__(self) -> None:
        self._mentions: dict[str, MentionHandler] = {}
        self._schedules: dict[str, list[ScheduleHook]] = {}
        self._events: dict[str, dict[str, EventHandler]] = {}

    def mention(self, slug: str) -> Callable[[MentionHandler], MentionHandler]:
        def register(handler: MentionHandler) -> MentionHandler:
            if slug in self._mentions and self._mentions[slug] is not handler:
                raise ValueError(f"agent {slug} already has a mention handler")
            self._mentions[slug] = handler
            return handler

        return register

    def schedule(
        self, slug: str, name: str, *, at: str | None = None, every_seconds: int | None = None
    ) -> Callable[[ScheduleRun], ScheduleRun]:
        def register(run: ScheduleRun) -> ScheduleRun:
            hooks = self._schedules.setdefault(slug, [])
            if any(hook.name == name for hook in hooks):
                raise ValueError(f"agent {slug} already has a schedule hook {name}")
            hooks.append(ScheduleHook(name=name, run=run, at=at, every_seconds=every_seconds))
            return run

        return register

    def event(self, slug: str, event_type: str) -> Callable[[EventHandler], EventHandler]:
        """Register a handler for ``atlas.warroom`` envelopes of ``event_type``. Not for
        ``warroom.message``: mentions have their own hook."""
        if event_type == "warroom.message":
            raise ValueError("warroom.message is dispatched as mentions; use HOOKS.mention")

        def register(handler: EventHandler) -> EventHandler:
            handlers = self._events.setdefault(slug, {})
            if event_type in handlers and handlers[event_type] is not handler:
                raise ValueError(f"agent {slug} already has a {event_type} handler")
            handlers[event_type] = handler
            return handler

        return register

    def hooks(self) -> dict[str, AgentHooks]:
        slugs = list(dict.fromkeys([*self._mentions, *self._schedules, *self._events]))
        return {
            slug: AgentHooks(
                slug=slug,
                on_mention=self._mentions.get(slug),
                schedule=tuple(self._schedules.get(slug, ())),
                on_event=dict(self._events.get(slug, {})),
            )
            for slug in slugs
        }


HOOKS = HookRegistry()
"""The process-wide registry the modules in ``atlas_agents.agents`` register into."""

AGENTS_PACKAGE: Final = "atlas_agents.agents"


def discover_hooks(package: str = AGENTS_PACKAGE, registry: HookRegistry = HOOKS) -> dict[str, AgentHooks]:
    """Import every module in ``package`` (each registers itself on import) and return the hooks."""
    root = importlib.import_module(package)
    for module in pkgutil.iter_modules(root.__path__, prefix=f"{package}."):
        importlib.import_module(module.name)
    return registry.hooks()
