"""The enrichment hook: where entity linking, event typing, sentiment, importance, embeddings,
clustering and novelty plug into the ingest pipeline (decision #243).

The pipeline calls :func:`enrich_items` once per poll with every *new* item of that poll (already
deduplicated, body extracted, language detected), just before they are inserted. The hook mutates
the items in place: set ``instruments``, ``asset_classes``, ``entities``, ``event_type``,
``sentiment``, ``importance``, ``novelty``, ``embedding``, ``cluster_id`` (create the
``news_clusters`` row yourself through ``ctx.engine``). Items it leaves alone are inserted with the
column defaults. An exception in the hook fails the poll like a fetch error would, so nothing
half-enriched is stored.

ATL-59 ships it as a no-op; the wiring task replaces the body (or passes its own hook to
``Pipeline(enrich=...)``) without touching the pipeline.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine

from atlas_ingest.items import PreparedItem


@dataclass(frozen=True)
class EnrichContext:
    engine: Engine
    now: datetime
    """UTC time of the poll (the runner's clock)."""


EnrichHook = Callable[[Sequence[PreparedItem], EnrichContext], Awaitable[None]]


async def enrich_items(items: Sequence[PreparedItem], ctx: EnrichContext) -> None:
    """No-op until the enrichment wiring task fills it in."""
    return None
