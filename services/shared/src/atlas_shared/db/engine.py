"""Engine factory reading the same DB_* variables as Laravel."""

import os
from collections.abc import Mapping

from sqlalchemy import URL, Engine, create_engine


def database_url(env: Mapping[str, str] | None = None) -> URL:
    """Build a psycopg 3 URL from DB_HOST, DB_PORT, DB_DATABASE, DB_USERNAME and DB_PASSWORD."""
    source = os.environ if env is None else env
    return URL.create(
        "postgresql+psycopg",
        username=source.get("DB_USERNAME", "atlas"),
        password=source.get("DB_PASSWORD") or None,
        host=source.get("DB_HOST", "127.0.0.1"),
        port=int(source.get("DB_PORT", "5433")),
        database=source.get("DB_DATABASE", "atlas"),
    )


def make_engine(env: Mapping[str, str] | None = None, *, echo: bool = False) -> Engine:
    """Create an engine whose sessions run in UTC, matching Laravel's storage convention."""
    return create_engine(
        database_url(env),
        echo=echo,
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC"},
    )
