"""The Portfolio Manager's hooks (brief §7.8, ATL-169). Disabled by default (agents.roster): the owner
turns it on in Settings → Agents. The work is :mod:`atlas_agents.pm.manage`.

* schedule ``pm_check`` every ``agents.portfolio_manager.check.every_seconds`` (300): the rules on every
  open position; the model runs only when a rule fires (or once before an importance-4+ event);
* ``news.alert`` (ingest, atlas.warroom) naming a held instrument: an immediate check of it;
* ``warroom.message.fill`` (the Executor's fill card) on a held instrument: an immediate check. A stop
  or target that triggers on OKX fills an order too, so it arrives here as a fill;
* ``warroom.message.system_alert`` from the circuit breakers: an immediate check of every position.

Immediate checks run in the background, one position at a time (a per-position lock shared with the
tick); :func:`settled` waits for them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Final

from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope

from atlas_agents.pm import manage
from atlas_agents.registry import HOOKS, AgentHost, ScheduleFire
from atlas_agents.session import RunResult

SLUG: Final = manage.SLUG
NEWS_ALERT: Final = "news.alert"
FILL: Final = "warroom.message.fill"
SYSTEM_ALERT: Final = "warroom.message.system_alert"
CIRCUIT_BREAKER_AUTHOR: Final = "circuit_breaker"
CHECK_SECONDS: Final = int(DEFAULTS["agents"]["portfolio_manager"]["check"]["every_seconds"])

settled = manage.settled


@HOOKS.schedule(SLUG, "pm_check", every_seconds=CHECK_SECONDS)
async def check_positions(host: AgentHost, fire: ScheduleFire) -> RunResult | None:
    results = await manage.tick(host)
    return results[-1] if results else None


def _strings(value: Any) -> set[str]:
    return {v for v in value if isinstance(v, str)} if isinstance(value, list) else set()


async def _held_among(host: AgentHost, inst_ids: set[str]) -> set[str]:
    if not inst_ids:
        return set()
    return inst_ids & await asyncio.to_thread(manage.held, host)


@HOOKS.event(SLUG, NEWS_ALERT)
async def on_news_alert(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    payload = envelope.payload
    held = await _held_among(host, _strings(payload.get("inst_ids")) | _strings(payload.get("held_inst_ids")))
    if not held:
        return None
    importance = payload.get("importance")
    why = (
        f"News alert (importance {importance}) on {', '.join(sorted(held))}, cluster "
        f"{payload.get('cluster_id')}. Read it with news.search_news / news.get_story_cluster and use "
        "News Intelligence's CONFIRMED/REPORTED/RUMOUR label; never act on the headline alone."
    )
    manage.background(manage.trigger(host, held, why, source="news"), f"news alert {envelope.id}")
    return None


@HOOKS.event(SLUG, FILL)
async def on_fill(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    card = envelope.payload.get("card")
    if not isinstance(card, Mapping):
        return None
    inst_id = card.get("inst_id")
    if not isinstance(inst_id, str):
        return None
    held = await _held_among(host, {inst_id})
    if not held:
        return None
    why = (
        f"Fill on {inst_id}: {card.get('side')} {card.get('size')} at {card.get('price')} "
        f"(purpose {card.get('purpose')}, origin {card.get('origin')})."
    )
    manage.background(manage.trigger(host, held, why, source="fill"), f"fill {envelope.id}")
    return None


@HOOKS.event(SLUG, SYSTEM_ALERT)
async def on_circuit_breaker(host: AgentHost, envelope: EventEnvelope) -> RunResult | None:
    payload = envelope.payload
    if payload.get("author") != CIRCUIT_BREAKER_AUTHOR:
        return None
    card = payload.get("card")
    if not isinstance(card, Mapping) or card.get("type") != "circuit_breaker":
        return None
    name = card.get("code")
    why = f"Circuit breaker tripped ({name or 'unknown'}): new orders are paused; review every open position."
    manage.background(manage.trigger(host, None, why, source="breaker"), f"breaker {envelope.id}")
    return None


__all__ = ["check_positions", "on_circuit_breaker", "on_fill", "on_news_alert", "settled"]
