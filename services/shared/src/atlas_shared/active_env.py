"""The env Atlas trades in, as stored by Laravel (decision #255 (4), ATL-236/ATL-237).

``system_state`` holds one row (``id = 1``) with ``active_env``. Every Python service reads it at
startup and again on ``system.env_changed``. The process environment (``ATLAS_ENV``, then
``ATLAS_DEFAULT_ENV``, then ``env.default``) is only the fallback while that row does not exist,
the same rule as Laravel's ``ActiveEnv::get()``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Final

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from atlas_shared.defaults import DEFAULTS

log = logging.getLogger(__name__)

ENV_CHANGED: Final = "system.env_changed"
_QUERY: Final = text("SELECT active_env FROM system_state WHERE id = 1")


def allowed_envs() -> list[str]:
    return [str(e) for e in DEFAULTS["env"]["allowed"]]


def fallback_env(environ: Mapping[str, str] | None = None) -> str:
    """``ATLAS_ENV`` or ``ATLAS_DEFAULT_ENV`` or ``env.default``; raises ValueError for an unknown env."""
    source = os.environ if environ is None else environ
    env = source.get("ATLAS_ENV") or source.get("ATLAS_DEFAULT_ENV") or str(DEFAULTS["env"]["default"])
    if env not in allowed_envs():
        raise ValueError(f"ATLAS_ENV must be one of {allowed_envs()}")
    return env


def stored_env(engine: Engine) -> str | None:
    """``system_state.active_env``, or None when the row (or the table) does not exist.

    Raises SQLAlchemyError when Postgres cannot be reached, so a caller that already runs in an env
    can keep it rather than fall back to the process environment by accident.
    """
    try:
        with engine.connect() as conn:
            value = conn.execute(_QUERY).scalar_one_or_none()
    except SQLAlchemyError as exc:
        # An unmigrated database has no system_state table: same as "no row".
        if "system_state" in str(exc) and "does not exist" in str(exc):
            return None
        raise
    if value is None:
        return None
    env = str(value)
    if env not in allowed_envs():
        log.warning("system_state.active_env has an unknown value; ignoring it")
        return None
    return env


def read_active_env(engine: Engine, fallback: str | None = None) -> str:
    """The stored active env; ``fallback`` (default :func:`fallback_env`) when there is no row or
    Postgres cannot be read."""
    default = fallback if fallback is not None else fallback_env()
    try:
        env = stored_env(engine)
    except SQLAlchemyError:
        log.warning("could not read system_state; using %s", default, exc_info=True)
        return default
    return env if env is not None else default


__all__ = ["ENV_CHANGED", "allowed_envs", "fallback_env", "read_active_env", "stored_env"]
