"""`uv run --package atlas-trading-core python -m trading_core` serves the internal API."""

from __future__ import annotations

import uvicorn

from trading_core.config import Settings
from trading_core.logs import configure_logging


def main() -> None:
    configure_logging()
    settings = Settings.from_env()
    uvicorn.run(
        "trading_core.app:app",
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
