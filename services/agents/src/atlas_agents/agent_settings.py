"""The owner's agent settings merged over config/defaults.yaml (ATL-114, brief §3.2 and §14.7).

Laravel stores agent settings as flat dotted keys in ``settings.data`` for the env (ATL-113):
``agents.<slug>.enabled|model|daily_tokens|daily_turns`` and ``agents.web.allow_domains|deny_domains``.
A key missing from the row (a row saved before the field existed) falls back to the default spec
built from ``agents.defaults`` + ``agents.roster`` (:func:`atlas_agents.registry.build_registry`)
and ``agents.web``. A value of the wrong type or out of ``agents.settings_bounds`` is ignored with a
warning: Laravel's validation already refuses those, so one here means a hand-edited row.

``model`` in settings is a tier (strong|standard|routine); the spec carries the CLI alias from
``agents.models``. ``routine_model`` and ``max_turns_per_run`` are not owner settings.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from atlas_shared.defaults import DEFAULTS
from sqlalchemy import Engine, Integer, String, column, select, table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from atlas_agents.registry import AgentSpec

log = logging.getLogger(__name__)

# Laravel-owned; only the columns read here.
_settings = table(
    "settings",
    column("env", String),
    column("version", Integer),
    column("data", JSONB),
)


@dataclass(frozen=True)
class WebPolicy:
    """``agents.web`` domain lists for news.fetch_article (ATL-100 matching rules)."""

    allow_domains: tuple[str, ...] = ()
    deny_domains: tuple[str, ...] = ()


def default_web_policy(defaults: Mapping[str, Any] = DEFAULTS) -> WebPolicy:
    conf: Mapping[str, Any] = defaults["agents"]["web"]
    return WebPolicy(
        allow_domains=tuple(str(d) for d in conf.get("allow_domains") or ()),
        deny_domains=tuple(str(d) for d in conf.get("deny_domains") or ()),
    )


@dataclass(frozen=True)
class AgentSettings:
    """The merged result for one env. ``version`` is the settings row's (None: no row yet)."""

    version: int | None
    specs: dict[str, AgentSpec]
    web: WebPolicy = field(default_factory=WebPolicy)


def read_row(engine: Engine, env: str) -> tuple[int | None, dict[str, Any]]:
    """(version, data) of the env's settings row; (None, {}) when Laravel has not created it."""
    with Session(engine) as db:
        row = db.execute(select(_settings.c.version, _settings.c.data).where(_settings.c.env == env)).first()
    if row is None:
        return None, {}
    version, data = row
    return int(version), dict(data) if isinstance(data, dict) else {}


def read_version(engine: Engine, env: str) -> int | None:
    with Session(engine) as db:
        value = db.scalar(select(_settings.c.version).where(_settings.c.env == env))
    return None if value is None else int(value)


def _strict_int(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def _domains(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return tuple(value)


def _warn(key: str, value: Any) -> None:
    log.warning("agents: ignoring settings value %s=%r (wrong type or out of bounds)", key, value)


def merge_spec(base: AgentSpec, data: Mapping[str, Any], defaults: Mapping[str, Any] = DEFAULTS) -> AgentSpec:
    """``base`` with every valid ``agents.<slug>.*`` value in ``data`` applied."""
    config: Mapping[str, Any] = defaults["agents"]
    models: Mapping[str, str] = config["models"]
    bounds: Mapping[str, Any] = config["settings_bounds"]
    prefix = f"agents.{base.slug}."
    changes: dict[str, Any] = {}

    key = prefix + "enabled"
    if key in data:
        if isinstance(data[key], bool):
            changes["enabled"] = data[key]
        else:
            _warn(key, data[key])
    key = prefix + "model"
    if key in data:
        if isinstance(data[key], str) and data[key] in models:
            changes["model"] = str(models[data[key]])
        else:
            _warn(key, data[key])
    for name in ("daily_tokens", "daily_turns"):
        key = prefix + name
        if key in data:
            limits = bounds[name]
            number = _strict_int(data[key], int(limits["min"]), int(limits["max"]))
            if number is None:
                _warn(key, data[key])
            else:
                changes[name] = number
    return dataclasses.replace(base, **changes) if changes else base


def merge_web(data: Mapping[str, Any], defaults: Mapping[str, Any] = DEFAULTS) -> WebPolicy:
    policy = default_web_policy(defaults)
    out: dict[str, tuple[str, ...]] = {}
    for name in ("allow_domains", "deny_domains"):
        key = f"agents.web.{name}"
        if key in data:
            domains = _domains(data[key])
            if domains is None:
                _warn(key, data[key])
            else:
                out[name] = domains
    return dataclasses.replace(policy, **out) if out else policy


def merge(
    base: Mapping[str, AgentSpec],
    version: int | None,
    data: Mapping[str, Any],
    defaults: Mapping[str, Any] = DEFAULTS,
) -> AgentSettings:
    return AgentSettings(
        version=version,
        specs={slug: merge_spec(spec, data, defaults) for slug, spec in base.items()},
        web=merge_web(data, defaults),
    )


def load(engine: Engine, env: str, base: Mapping[str, AgentSpec]) -> AgentSettings:
    """The env's settings row merged over ``base`` (the default specs)."""
    version, data = read_row(engine, env)
    return merge(base, version, data)
