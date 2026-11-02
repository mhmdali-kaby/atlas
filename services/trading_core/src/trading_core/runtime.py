"""Everything in trading_core that is bound to one env, and the switch between envs (ATL-237).

Decision #255 (4): the active env is ``system_state.active_env``. An :class:`EnvRuntime` holds what
talks to OKX for one env: the scheduler (sync, reconcile, market-data and approvals jobs, whose OKX
REST clients are built per run from the env's connection and ``okx.environments.<env>``), the public
and private WebSockets, the entry lifecycle, the breaker monitor and the reconcile gate.

:class:`EnvSwitcher` follows the stored env. On ``system.env_changed`` (read from ``atlas.system``)
and every ``env.watch_poll_seconds`` it re-reads ``system_state``; when the env differs it

1. closes the Executor at once (a new gate, reason ``reconciling``),
2. stops the old runtime (scheduler loops, sockets, monitor),
3. builds the new env's runtime, swaps ``app.state`` (settings.env, scheduler, gate, feeds) and starts
   it. Its reconcile job runs first and opens the gate when it finishes (rule 8).

The order path is unchanged: /execute still goes Executor ← Risk Engine, and refuses until the gate
opens.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import redis
from atlas_shared import bus
from atlas_shared.active_env import ENV_CHANGED, stored_env
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from trading_core.config import Settings
from trading_core.execution.lifecycle import EntryLifecycle
from trading_core.execution.reconcile import RECONCILING, ReconcileGate
from trading_core.risk.breakers import BreakerMonitor
from trading_core.scheduler import Scheduler
from trading_core.ws import MarketFeed
from trading_core.ws.private import PrivateFeed

log = logging.getLogger("trading_core.runtime")

SYSTEM_STREAM = "atlas.system"


@dataclass
class EnvRuntime:
    env: str
    scheduler: Scheduler
    gate: ReconcileGate
    feed: MarketFeed | None = None
    pfeed: PrivateFeed | None = None
    lifecycle: EntryLifecycle | None = None
    monitor: BreakerMonitor | None = None

    async def start(self) -> None:
        self.scheduler.start()
        if self.feed is not None:
            await self.feed.start()
        if self.pfeed is not None:
            await self.pfeed.start()
        if self.monitor is not None:
            await self.monitor.start()

    async def stop(self) -> None:
        if self.monitor is not None:
            await self.monitor.stop()
        if self.pfeed is not None:
            await self.pfeed.stop()
        if self.feed is not None:
            await self.feed.stop()
        await self.scheduler.stop()

    def install(self, state: Any) -> None:
        """Point ``app.state`` at this runtime (routes read it per request)."""
        state.runtime = self
        state.scheduler = self.scheduler
        state.reconcile_gate = self.gate
        state.market_feed = self.feed
        state.private_feed = self.pfeed
        state.breaker_monitor = self.monitor
        state.entry_lifecycle = self.lifecycle


# Builds the runtime of ``settings.env``; ``gate_reason`` is the closed gate's refusal reason.
RuntimeFactory = Callable[[Settings, str], EnvRuntime]


class EnvSwitcher:
    def __init__(
        self,
        state: Any,
        factory: RuntimeFactory,
        engine: Engine,
        *,
        start_runtimes: bool = True,
        poll_seconds: float | None = None,
        block_ms: int | None = None,
    ) -> None:
        self._state = state
        self._factory = factory
        self._engine = engine
        self._start = start_runtimes
        self.poll_seconds = float(poll_seconds or DEFAULTS["env"]["watch_poll_seconds"])
        self.block_ms = int(block_ms if block_ms is not None else min(self.poll_seconds, 5) * 1000)
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self.switches = 0

    @property
    def env(self) -> str:
        return str(self._state.settings.env)

    async def follow_stored_env(self, *, reason: str) -> bool:
        """Switch to ``system_state.active_env`` if it differs from the running env. No row, or a
        database error, keeps the running env (ATLAS_ENV was the fallback at startup)."""
        try:
            target = await asyncio.to_thread(stored_env, self._engine)
        except SQLAlchemyError:
            log.warning("could not read system_state; staying in %s", self.env, exc_info=True)
            return False
        if target is None:
            return False
        return await self.switch(target, reason=reason)

    async def switch(self, target: str, *, reason: str) -> bool:
        """Stop the running env's runtime and start ``target``'s. Returns False when already there."""
        async with self._lock:
            old_env = self.env
            if target == old_env:
                return False
            state = self._state
            old: EnvRuntime = state.runtime
            # Close the Executor first, so nothing is sent while the old runtime winds down.
            state.reconcile_gate = ReconcileGate(reason=reason)
            log.info("env switch %s -> %s: stopping the %s runtime", old_env, target, old_env)
            await old.stop()
            settings: Settings = dataclasses.replace(state.settings, env=target)
            runtime = self._factory(settings, reason)
            state.settings = settings
            runtime.install(state)
            if self._start:
                await runtime.start()
            self.switches += 1
            log.info("env switch %s -> %s: %s runtime started, reconciling", old_env, target, target)
            return True

    async def handle_envelope(self, envelope: EventEnvelope) -> bool:
        """``system.env_changed`` re-reads system_state (the row, not the payload, decides)."""
        if envelope.type != ENV_CHANGED:
            return False
        return await self.follow_stored_env(reason=RECONCILING)

    # -- the background watcher ------------------------------------------------------------------

    def start(self, client: redis.Redis | None = None) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._watch(client), name="env:watch")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _watch(self, client: redis.Redis | None) -> None:
        key = bus.stream_key(SYSTEM_STREAM)
        last = "$"
        rds = client
        loop = asyncio.get_running_loop()
        next_poll = loop.time() + self.poll_seconds
        while True:
            try:
                if rds is None:
                    rds = bus.make_client()
                if last == "$":
                    latest: Any = await asyncio.to_thread(rds.xrevrange, key, "+", "-", 1)
                    last = str(latest[0][0]) if latest else "0-0"
                rows = cast(
                    "list[tuple[str, list[tuple[str, dict[str, str]]]]] | None",
                    await asyncio.to_thread(rds.xread, {key: last}, count=100, block=self.block_ms),
                )
                for _key, entries in rows or []:
                    for entry_id, fields in entries:
                        last = str(entry_id)
                        raw = fields.get(bus.FIELD)
                        if raw is None:
                            continue
                        try:
                            envelope = EventEnvelope.model_validate_json(raw)
                        except Exception:
                            log.warning("env watcher: unreadable atlas.system entry %s", entry_id)
                            continue
                        await self.handle_envelope(envelope)
            except asyncio.CancelledError:
                raise
            except redis.RedisError:
                log.warning("env watcher: reading atlas.system failed")
                await asyncio.sleep(min(self.poll_seconds, 5))
            except Exception:
                log.exception("env watcher: switch failed")
            # A missed event (Redis down when Laravel published) is caught by the poll.
            if loop.time() >= next_poll:
                next_poll = loop.time() + self.poll_seconds
                try:
                    await self.follow_stored_env(reason=RECONCILING)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("env watcher: switch failed")


__all__ = ["EnvRuntime", "EnvSwitcher", "RuntimeFactory"]
