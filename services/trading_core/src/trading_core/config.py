"""Runtime configuration for trading_core, read from the process environment once at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from atlas_shared.defaults import DEFAULTS

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    env: str
    internal_shared_secret: str
    max_clock_skew_seconds: int = 30
    # TRADING_CORE_SCHEDULER=0 serves the API without the periodic sync loops (tests, one-off runs).
    run_scheduler: bool = True
    # Laravel's APP_KEY ("base64:..."), used only to decrypt connections (decision #235). Never logged.
    app_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get("ATLAS_ENV") or os.environ.get("ATLAS_DEFAULT_ENV") or DEFAULTS["env"]["default"]
        allowed: list[str] = DEFAULTS["env"]["allowed"]
        if env not in allowed:
            raise ValueError(f"ATLAS_ENV must be one of {allowed}")
        return cls(
            host=os.environ.get("TRADING_CORE_HOST", DEFAULT_HOST),
            port=int(os.environ.get("TRADING_CORE_PORT", str(DEFAULT_PORT))),
            env=env,
            internal_shared_secret=os.environ.get("INTERNAL_SHARED_SECRET", ""),
            run_scheduler=os.environ.get("TRADING_CORE_SCHEDULER", "1").strip().lower()
            not in ("0", "false", "no"),
            app_key=os.environ.get("APP_KEY", ""),
        )
