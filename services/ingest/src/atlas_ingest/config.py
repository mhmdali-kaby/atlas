"""Runtime configuration for atlas_ingest, read once at startup.

DB_*, APP_KEY and REDIS_* come from the process environment, which ``./atlas up`` fills from
backend/.env exactly as it does for trading_core (APP_KEY from the Keychain wins). Tunables come
from config/defaults.yaml ``news`` via atlas_shared.defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from atlas_shared.defaults import DEFAULTS


@dataclass(frozen=True)
class IngestSettings:
    reload_seconds: float
    tick_seconds: float
    failure_threshold: int
    disable_minutes: int
    extract_timeout_seconds: float
    extract_max_bytes: int
    extract_max_per_poll: int
    http_timeout_seconds: float = 20.0
    # Laravel's APP_KEY ("base64:..."), used only to decrypt news_sources.api_key. Never logged.
    app_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> IngestSettings:
        news: dict[str, Any] = DEFAULTS["news"]
        extract: dict[str, Any] = news["extract"]
        return cls(
            reload_seconds=float(news["reload_seconds"]),
            tick_seconds=float(news["tick_seconds"]),
            failure_threshold=int(news["failure_threshold"]),
            disable_minutes=int(news["disable_minutes"]),
            extract_timeout_seconds=float(extract["timeout_seconds"]),
            extract_max_bytes=int(extract["max_bytes"]),
            extract_max_per_poll=int(extract["max_per_poll"]),
            app_key=os.environ.get("APP_KEY", ""),
        )
