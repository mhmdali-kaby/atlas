"""Structured JSON logs, one object per line, in the same shape as trading_core's
(ts, level, logger, msg, plus the record's extra fields). Callers log source keys, hosts, counts
and error summaries only: never a URL with a query string, a key or an article body."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import IO, Any

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


# httpx/httpcore log every request's full URL (query string, so possibly a key) at INFO.
QUIET_LOGGERS = ("httpx", "httpcore", "charset_normalizer")
# The extraction libraries log ERRORs for every page they cannot parse ("Document is empty"); the
# pipeline already treats that as "no body", so their own lines are noise.
SILENT_LOGGERS = ("trafilatura", "htmldate", "courlan", "justext")


def configure_logging(level: int = logging.INFO, *, stream: IO[str] | None = None) -> None:
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Pinned at WARNING whatever the root level: httpx's INFO line is "GET <full url>".
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in SILENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.CRITICAL)
