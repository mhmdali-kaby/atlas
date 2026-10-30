"""The nine agent specs come from config/defaults.yaml (ATL-83)."""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest
from atlas_agents.registry import (
    REGISTRY,
    UnknownAgentError,
    budget_warning_fraction,
    build_registry,
    enabled_agents,
    get_spec,
)
from atlas_shared.defaults import DEFAULTS

# The Portfolio Manager's runs happen only when a rule fires (§7.8 "escalate to the strong model when an
# action is needed"); its no-rule checks call no model at all (ATL-169).
STRONG = {
    "market_scout",
    "news_intelligence",
    "historian",
    "strategist",
    "devils_advocate",
    "portfolio_manager",
}


def test_nine_agents_in_brief_order() -> None:
    assert list(REGISTRY) == list(DEFAULTS["warroom"]["agents"])
    assert len(REGISTRY) == 9


def test_news_intelligence_market_scout_and_the_historian_are_enabled_in_this_patch() -> None:
    # ATL-107 enabled News Intelligence and ATL-108 Market Scout, ATL-161 the Orchestrator; the rest
    # are turned on by the owner in Settings → Agents.
    assert [s.slug for s in enabled_agents()] == [
        "orchestrator",
        "market_scout",
        "news_intelligence",
        "historian",
    ]


def test_research_and_decision_agents_get_the_strongest_model() -> None:
    strong = DEFAULTS["agents"]["models"]["strong"]
    routine = DEFAULTS["agents"]["models"]["routine"]
    for slug, spec in REGISTRY.items():
        assert (spec.model == strong) == (slug in STRONG), slug
        assert spec.routine_model == routine
        assert spec.daily_tokens > 0 and spec.daily_turns > 0 and spec.max_turns_per_run > 0


def test_get_spec_rejects_unknown_slugs() -> None:
    assert get_spec("historian").name == "Historian"
    with pytest.raises(UnknownAgentError):
        get_spec("trader")


def test_bad_config_is_refused() -> None:
    bad = copy.deepcopy(DEFAULTS)
    bad["agents"]["roster"]["historian"] = {"model": "huge"}
    with pytest.raises(ValueError, match="tier"):
        build_registry(bad)
    bad = copy.deepcopy(DEFAULTS)
    bad["agents"]["roster"]["trader"] = {}
    with pytest.raises(ValueError, match="unknown"):
        build_registry(bad)
    bad = copy.deepcopy(DEFAULTS)
    bad["agents"]["defaults"]["daily_tokens"] = 0
    with pytest.raises(ValueError, match="daily_tokens"):
        build_registry(bad)


def test_budget_warning_fraction() -> None:
    assert budget_warning_fraction() == Decimal("0.8")
