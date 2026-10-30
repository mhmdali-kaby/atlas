"""Applies the owner's agent settings to the running sessions (ATL-114, brief §3.2).

The specs in force are config/defaults.yaml's merged with the env's settings row
(:mod:`atlas_agents.agent_settings`), at start and whenever the row's version changes. Disabling an
agent removes its session at once (so no new run starts) and closes it after any in-flight run;
enabling one builds a session. Budgets apply at the agent's next run, a model change at its next
connect. Each enable/disable is recorded in ``agent_states`` and published as ``system.agent_state``.
A replayed day (evals) runs on the defaults and never reads live settings.

The :class:`atlas_agents.supervisor.Supervisor` owns one applier and delegates to it.
"""

from __future__ import annotations

import asyncio
import logging
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
        self.prompts: dict[str, tuple[prompts.PromptSource, int]] = dict(
            prompts.sync_versions(engine, enabled, prompts_dir=prompts_dir)
        )
        self.sessions: dict[str, AgentSession] = {}
        self.locks: dict[str, asyncio.Lock] = {slug: asyncio.Lock() for slug in self._base}
        self._make: Callable[[AgentSpec], AgentSession] | None = None
        self._lock = asyncio.Lock()
        self._retiring: set[asyncio.Task[None]] = set()

    def start(self, make: Callable[[AgentSpec], AgentSession]) -> None:
        """Build a session for every enabled agent; record every disabled one as ``disabled`` so the
        Agents page shows it without reading settings itself."""
        self._make = make
        for spec in self.settings.specs.values():
            if spec.enabled:
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
        self.settings = fresh
        log.info("agents: settings version %s -> %s applied", previous, fresh.version)
        for slug, spec in fresh.specs.items():
            session = self.sessions.get(slug)
            if spec.enabled and session is None:
                self._enable(spec)
            elif not spec.enabled and session is not None:
                self._disable(slug, session)
            elif session is not None and spec != session.spec:
                session.update_spec(spec)  # budgets at its next run, the model at its next connect

    def _enable(self, spec: AgentSpec) -> None:
        if self._make is None:
            raise RuntimeError("SettingsApplier.start() was not called")
        if spec.slug not in self.prompts:
            self.prompts.update(
                prompts.sync_versions(self._engine, [spec.slug], prompts_dir=self._prompts_dir)
            )
        self.sessions[spec.slug] = self._make(spec)
        self.locks.setdefault(spec.slug, asyncio.Lock())
        self._set_state(spec.slug, "idle", reason="settings_enabled")

    def _disable(self, slug: str, session: AgentSession) -> None:
        # No new run starts from now on; the session closes once any in-flight run has ended.
        del self.sessions[slug]
        self._set_state(slug, "disabled", reason="settings_disabled")
        lock = self.locks.setdefault(slug, asyncio.Lock())

        async def retire() -> None:
            async with lock:
                await session.close()

        task = asyncio.get_running_loop().create_task(retire())
        self._retiring.add(task)
        task.add_done_callback(self._retiring.discard)

    def _set_state(self, slug: str, state: State, *, reason: str) -> None:
        """Record an enable/disable in agent_states and publish system.agent_state if it changed."""
        try:
            with Session(self._engine) as db:
                previous = set_state(db, agent=slug, env=self.env, state=state, now=self._clock())
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
            "last_error": None,
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

    async def settled(self) -> None:
        """Wait until every disabled agent's session has closed."""
        while self._retiring:
            pending = list(self._retiring)
            await asyncio.gather(*pending, return_exceptions=True)
            self._retiring.difference_update(pending)
