"""The two item shapes of the ingest pipeline.

``RawItem`` is what a collector returns: only what the source said. ``PreparedItem`` is one row about
to be inserted into ``news_items``: canonical URL, extracted body and language filled in by the
pipeline, and every enrichment column (instruments, event_type, sentiment, importance, novelty,
cluster_id, embedding) left at its default for the enrichment hook to fill.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class RawItem:
    url: str
    """The article link as the source gave it (canonicalised later by the pipeline)."""
    title: str
    summary: str | None = None
    """Plain text, HTML stripped."""
    body: str | None = None
    """Full article text when the source carries it; None makes the pipeline extract it."""
    published_at: datetime | None = None
    """UTC. None falls back to fetched_at."""
    language: str | None = None
    """A hint from the feed (``en``, ``en-us``); the pipeline detects from text first."""
    raw: dict[str, Any] = field(default_factory=dict)
    """JSON-safe source payload stored in news_items.raw. Never put a key or keyed URL here."""


@dataclass
class PreparedItem:
    source: str
    url: str
    canonical_url: str
    title: str
    published_at: datetime
    fetched_at: datetime
    summary: str | None = None
    body: str | None = None
    language: str | None = None
    raw: dict[str, Any] | None = None
    title_original: str | None = None
    entities: Any | None = None
    instruments: list[str] = field(default_factory=list)
    asset_classes: list[str] = field(default_factory=list)
    event_type: str | None = None
    sentiment: Decimal | None = None
    importance: int = 1
    novelty: Decimal | None = None
    cluster_id: uuid.UUID | None = None
    embedding: list[float] | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "title_original": self.title_original,
            "summary": self.summary,
            "body": self.body,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "language": self.language,
            "entities": self.entities,
            "instruments": self.instruments,
            "asset_classes": self.asset_classes,
            "event_type": self.event_type,
            "sentiment": self.sentiment,
            "importance": self.importance,
            "novelty": self.novelty,
            "cluster_id": self.cluster_id,
            "embedding": self.embedding,
            "raw": self.raw,
            "created_at": self.fetched_at,
            "updated_at": self.fetched_at,
        }
