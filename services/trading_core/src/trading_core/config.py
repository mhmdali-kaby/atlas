"""Runtime configuration for trading_core, read from the process environment once at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from atlas_shared.active_env import fallback_env

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
    # TRADING_CORE_MARKET_WS=0 keeps the OKX WebSocket market feed (ATL-125) off.
    run_market_ws: bool = True
    # TRADING_CORE_PRIVATE_WS=0 keeps the private OKX WebSocket (ATL-142) off.
    run_private_ws: bool = True
    # Laravel's APP_KEY ("base64:..."), used only to decrypt connections (decision #235). Never logged.
    app_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        # Only the fallback: the app switches to system_state.active_env at startup (ATL-237).
        env = fallback_env()
        return cls(
            host=os.environ.get("TRADING_CORE_HOST", DEFAULT_HOST),
            port=int(os.environ.get("TRADING_CORE_PORT", str(DEFAULT_PORT))),
            env=env,
            internal_shared_secret=os.environ.get("INTERNAL_SHARED_SECRET", ""),
            run_scheduler=os.environ.get("TRADING_CORE_SCHEDULER", "1").strip().lower()
            not in ("0", "false", "no"),
            app_key=os.environ.get("APP_KEY", ""),
            run_market_ws=os.environ.get("TRADING_CORE_MARKET_WS", "1").strip().lower()
            not in ("0", "false", "no"),
            run_private_ws=os.environ.get("TRADING_CORE_PRIVATE_WS", "1").strip().lower()
            not in ("0", "false", "no"),
        )
