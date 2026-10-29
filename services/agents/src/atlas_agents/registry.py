"""The nine agents and their runtime specs (brief §7, decisions #242/#245).

Slugs and display names come from ``warroom.agents`` in config/defaults.yaml; models, budgets and
the enabled flag from ``agents``. A roster entry only lists what differs from ``agents.defaults``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from atlas_shared.defaults import DEFAULTS


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
