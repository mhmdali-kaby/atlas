"""Applies the owner's agent settings to the running sessions (ATL-114, brief §3.2).

The specs in force are config/defaults.yaml's merged with the env's settings row
(:mod:`atlas_agents.agent_settings`), at start and whenever the row's version changes. Disabling an
agent removes its session at once (so no new run starts) and closes it after any in-flight run;
enabling one builds a session. Budgets apply at the agent's next run, a model change at its next
connect. Each enable/disable is recorded in ``agent_states`` and published as ``system.agent_state``.
A replayed day (evals) runs on the defaults and never reads live settings.

Prompt pins (ATL-252, decision #265 (3)): ``agents.<slug>.prompt_version`` = N makes the agent run on
``prompt_versions`` row N's stored body instead of its files; null or absent means the files. A pin
change reaches a live session at its next run, which reconnects with the new prompt, and
``agent_runs.prompt_version`` records N. A pin to a row that does not exist leaves that agent
without a session, marks it ``degraded`` with the reason, and raises one ``system.alert`` plus a War
Room ``system_alert``; every other agent carries on.

The :class:`atlas_agents.supervisor.Supervisor` owns one applier and delegates to it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from atlas_agents import agent_settings, events, prompts
from atlas_agents.agent_settings import AgentSettings
from atlas_agents.registry import AgentSpec
from atlas_agents.session import AgentSession
from atlas_agents.states import State, set_state

log = logging.getLogger(__name__)

SETTINGS_CHANGED: Final = "system.settings_changed"
WARROOM_STREAM: Final = "atlas.warroom"
SYSTEM_ALERT: Final = "system.alert"
PIN_SOURCE: Final = "system:agents"
PIN_MISSING: Final = "prompt_version_missing"
_ALERT_NAMESPACE: Final = uuid.UUID("0b8e6c1e-52f4-4d55-9a0e-3f1c7d2a6b90")

PromptRenderer = Callable[[str, prompts.PromptSource], Callable[[], str]]
"""(slug, source) -> the zero-argument system-prompt hook a session renders at connect."""


class SettingsApplier:
    def __init__(
        self,
        *,
        env: str,
        engine: Engine,
        base: Mapping[str, AgentSpec],
        replaying: bool,
        prompts_dir: Path | None,
        publish: events.Publisher,
        clock: Callable[[], datetime],
    ) -> None:
        self.env = env
        self._engine = engine
        self._base: dict[str, AgentSpec] = dict(base)
        self._replaying = replaying
        self._prompts_dir = prompts_dir
        self._publish = publish
        self._clock = clock
        self.settings: AgentSettings = self._load()
        enabled = [spec.slug for spec in self.settings.specs.values() if spec.enabled]
        # Every enabled agent's current prompt version is recorded once, at start (ATL-90).
        self.file_prompts: dict[str, tuple[prompts.PromptSource, int]] = dict(
            prompts.sync_versions(engine, enabled, prompts_dir=prompts_dir)
        )
        # The prompt each agent runs on: its files, or the stored version the owner pinned (ATL-252).
        self.prompts: dict[str, tuple[prompts.PromptSource, int]] = {}
        # slug -> why its pinned prompt cannot be used; such an agent has no session.
        self.prompt_errors: dict[str, str] = {}
        self._resolve_prompts()
        self.sessions: dict[str, AgentSession] = {}
        self.locks: dict[str, asyncio.Lock] = {slug: asyncio.Lock() for slug in self._base}
        self._make: Callable[[AgentSpec], AgentSession] | None = None
        self._render: PromptRenderer | None = None
        self._lock = asyncio.Lock()
        self._retiring: set[asyncio.Task[None]] = set()

    def start(self, make: Callable[[AgentSpec], AgentSession], render: PromptRenderer | None = None) -> None:
        """Build a session for every enabled agent; record every disabled one as ``disabled`` so the
        Agents page shows it without reading settings itself. ``render`` builds a session's
        system-prompt hook when a pin changes later; without it a pin change only reaches sessions
        built after it."""
        self._make = make
        self._render = render
        for spec in self.settings.specs.values():
            if spec.enabled and spec.slug in self.prompt_errors:
                self._report_pin_error(spec.slug)
            elif spec.enabled:
                self.sessions[spec.slug] = make(spec)
            elif not self._replaying:
                self._set_state(spec.slug, "disabled", reason="settings_disabled")

    def _load(self) -> AgentSettings:
        if self._replaying:  # a replayed day runs on the defaults, never on today's live settings
            return agent_settings.merge(self._base, None, {})
        return agent_settings.load(self._engine, self.env, self._base)

    def is_enabled(self, agent: str) -> bool:
        spec = self.settings.specs.get(agent)
        return spec is not None and spec.enabled

    def web_policy(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        web = self.settings.web
        return web.allow_domains, web.deny_domains

    async def refresh(self, *, force: bool = False) -> bool:
        """Re-read the env's settings version; when it changed (or ``force``), merge and apply the
        row. Returns whether settings were applied. A database failure keeps the current settings."""
        if self._replaying:
            return False
        async with self._lock:
            try:
                current = agent_settings.read_version(self._engine, self.env)
                if not force and current == self.settings.version:
                    return False
                fresh = agent_settings.load(self._engine, self.env, self._base)
            except Exception:
                log.exception("agents: could not read settings; keeping version %s", self.settings.version)
                return False
            self._apply(fresh)
            return True

    async def handle_system_envelope(self, envelope: EventEnvelope) -> bool:
        """Apply settings on this env's ``system.settings_changed``; ignore every other event."""
        if envelope.type != SETTINGS_CHANGED or envelope.env != self.env:
            return False
        return await self.refresh()

    def _apply(self, fresh: AgentSettings) -> None:
        previous = self.settings.version
        old_errors = dict(self.prompt_errors)
        old_pins = dict(self.settings.prompt_pins)
        self.settings = fresh
        log.info("agents: settings version %s -> %s applied", previous, fresh.version)
        unsynced = [
            slug for slug, spec in fresh.specs.items() if spec.enabled and slug not in self.file_prompts
        ]
        if unsynced:
            self.file_prompts.update(
                prompts.sync_versions(self._engine, unsynced, prompts_dir=self._prompts_dir)
            )
        self._resolve_prompts()
        for slug, spec in fresh.specs.items():
            session = self.sessions.get(slug)
            if spec.enabled and slug in self.prompt_errors:
                if session is not None:
                    self._remove(slug, session)
                if old_errors.get(slug) is None or old_pins.get(slug) != fresh.prompt_pins.get(slug):
                    self._report_pin_error(slug)
            elif spec.enabled and session is None:
                self._enable(spec)
            elif not spec.enabled and session is not None:
                self._disable(slug, session)
            elif session is not None:
                if spec != session.spec:
                    session.update_spec(spec)  # budgets at its next run, the model at its next connect
                self._update_prompt(slug, session)

    # -- prompt pins (ATL-252) --------------------------------------------------------------------

    def _resolve_prompts(self) -> None:
        """Fill ``prompts`` and ``prompt_errors`` from the file versions and the settings' pins."""
        resolved = dict(self.file_prompts)
        errors: dict[str, str] = {}
        for slug, version in self.settings.prompt_pins.items():
            spec = self.settings.specs.get(slug)
            if spec is None or not spec.enabled:
                continue
            try:
                source = prompts.load_stored(self._engine, slug, version)
            except Exception:
                log.exception("agents: could not read pinned prompt version %s of %s", version, slug)
                source = None
            if source is None:
                errors[slug] = (
                    f"Pinned prompt version {version} of {slug} does not exist. The agent is stopped "
                    "until the pin is cleared or set to a stored version in Settings → Agents."
                )
                resolved.pop(slug, None)
                continue
            resolved[slug] = (source, version)
        self.prompts = resolved
        self.prompt_errors = errors

    def _update_prompt(self, slug: str, session: AgentSession) -> None:
        """Hand ``session`` its pinned or file prompt when that changed; taken at its next run."""
        if self._render is None:
            return
        prompt = self.prompts.get(slug)
        version = prompt[1] if prompt else None
        if version == session.prompt_version:
            return
        session.update_prompt(version, self._render(slug, prompt[0]) if prompt else None)

    def _report_pin_error(self, slug: str) -> None:
        """Mark ``slug`` degraded with its pin error and raise the system and War Room alerts."""
        detail = self.prompt_errors[slug]
        self._set_state(slug, "degraded", reason=PIN_MISSING, last_error=detail)
        version = self.settings.prompt_pins.get(slug)
        card: dict[str, Any] = {"code": PIN_MISSING, "agent": slug, "prompt_version": version}
        key = f"{self.env}:{slug}:{version}:{self.settings.version}"
        alert = EventEnvelope(
            id=uuid.uuid5(_ALERT_NAMESPACE, f"system:{key}"),
            type=SYSTEM_ALERT,
            env=self.env,
            source=PIN_SOURCE,
            payload={"detail": detail, **card},
        )
        message = EventEnvelope(
            id=uuid.uuid5(_ALERT_NAMESPACE, f"warroom:{key}"),
            type="warroom.message",
            env=self.env,
            source=PIN_SOURCE,
            payload={
                "thread": {"kind": "main", "key": "main"},
                "author_type": "system",
                "author": PIN_SOURCE,
                "kind": "system_alert",
                "content_md": detail,
                "card": card,
                "reply_to": None,
                "mentions": [],
                "reasoning_md": None,
                "tool_calls": None,
                "tokens_in": None,
                "tokens_out": None,
                "cost_usd": None,
            },
        )
        for stream, envelope in ((events.SYSTEM_STREAM, alert), (WARROOM_STREAM, message)):
            try:
                self._publish(stream, envelope)
            except Exception:  # Redis down must not stop settings from applying
                log.warning("agents: could not publish the prompt pin alert for %s", slug, exc_info=True)

    # -- enable / disable -------------------------------------------------------------------------

    def _enable(self, spec: AgentSpec) -> None:
        if self._make is None:
            raise RuntimeError("SettingsApplier.start() was not called")
        self.sessions[spec.slug] = self._make(spec)
        self.locks.setdefault(spec.slug, asyncio.Lock())
        self._set_state(spec.slug, "idle", reason="settings_enabled")

    def _disable(self, slug: str, session: AgentSession) -> None:
        self._remove(slug, session)
        self._set_state(slug, "disabled", reason="settings_disabled")

    def _remove(self, slug: str, session: AgentSession) -> None:
        # No new run starts from now on; the session closes once any in-flight run has ended.
        del self.sessions[slug]
        lock = self.locks.setdefault(slug, asyncio.Lock())

        async def retire() -> None:
            async with lock:
                await session.close()

        task = asyncio.get_running_loop().create_task(retire())
        self._retiring.add(task)
        task.add_done_callback(self._retiring.discard)

    def _set_state(self, slug: str, state: State, *, reason: str, last_error: str | None = None) -> None:
        """Record a state change in agent_states and publish system.agent_state if it changed."""
        try:
            with Session(self._engine) as db:
                previous = set_state(
                    db, agent=slug, env=self.env, state=state, last_error=last_error, now=self._clock()
                )
                db.commit()
        except Exception:
            log.exception("agents: could not record %s as %s", slug, state)
            return
        if previous == state:
            return
        payload: dict[str, Any] = {
            "agent": slug,
            "state": state,
            "previous_state": previous,
            "reason": reason,
            "run_id": None,
            "current_task": None,
            "last_error": last_error,
            "resets_at": None,
            "settings_version": self.settings.version,
        }
        try:
            self._publish(
                events.SYSTEM_STREAM,
                events.envelope(type_=events.AGENT_STATE, env=self.env, agent=slug, payload=payload),
            )
        except Exception:  # Redis down must not stop settings from applying
            log.warning("agents: could not publish %s for %s", events.AGENT_STATE, slug, exc_info=True)

    def retiring_tasks(self) -> set[asyncio.Task[None]]:
        """Sessions still closing after a disable (the supervisor waits for them after a switch)."""
        return set(self._retiring)

    async def settled(self) -> None:
        """Wait until every disabled agent's session has closed."""
        while self._retiring:
            pending = list(self._retiring)
            await asyncio.gather(*pending, return_exceptions=True)
            self._retiring.difference_update(pending)
