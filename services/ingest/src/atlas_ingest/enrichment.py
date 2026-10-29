"""The enrichment hook: entity linking, event typing, sentiment, embeddings, clustering, novelty
and importance for every new item, then ``news.alert`` publishing (brief §7.2 steps 3-6,
decisions #243/#244).

The pipeline calls the hook once per poll with every *new* item of that poll (already deduplicated,
body extracted, language detected). :class:`Enricher` runs, per item in published_at order and in
one transaction:

1. ``link`` (ATL-62) → instruments, asset_classes, entities
2. ``event_type`` and the headline ``sentiment`` (ATL-63)
3. a first ``importance`` without novelty, so the cluster row gets a sensible max_importance
4. ``clustering.assign`` (ATL-64) → embedding, cluster_id, novelty
5. the final ``importance`` with novelty
6. the insert, then the cluster's max_importance is recomputed from its stored items
7. the alert rule: importance >= ``news.alerts.importance`` (5), or >= ``held_importance`` (4) on an
   instrument the env holds; the cluster is claimed with ``alerted_at`` so a story alerts once.

Items are inserted one at a time, each before the next is clustered, so two items of one story in
the same poll share a cluster. The hook returns the number of rows it inserted, which tells the
pipeline not to insert them again. Alerts are published on ``atlas.warroom`` only after the commit;
a publish that fails releases the cluster's claim so the next item of the story can alert.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from atlas_shared.db import NewsItem
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from sqlalchemy import Connection, Engine, text
from sqlalchemy.dialects.postgresql import insert

from atlas_ingest import aliases
from atlas_ingest.classify import event_type, headline_sentiment, importance
from atlas_ingest.clustering import ClusterParams, assign
from atlas_ingest.embedding import Embedder, get_default_embedder, item_text
from atlas_ingest.items import PreparedItem
from atlas_ingest.linking import Linker, set_default_linker

logger = logging.getLogger("atlas_ingest.enrichment")

ALERT_STREAM = "atlas.warroom"
ALERT_TYPE = "news.alert"
ALERT_SOURCE = "system:news"
ALERT_NAMESPACE = uuid.UUID("6f1d2c3a-4b5e-4f60-8a71-92b3c4d5e6f7")
"""Envelope ids are uuid5(ALERT_NAMESPACE, cluster id): a re-publish of one story's alert is
deduplicated by the bridge's unique event_id."""


@dataclass(frozen=True)
class EnrichContext:
    engine: Engine
    now: datetime
    """UTC time of the poll (the runner's clock)."""


EnrichHook = Callable[[Sequence[PreparedItem], EnrichContext], Awaitable[int | None]]
"""Returns the number of rows it inserted itself, or None to let the pipeline insert the items."""

Publisher = Callable[[str, EventEnvelope], object]


def _bus_publish(stream: str, envelope: EventEnvelope) -> object:
    from atlas_shared import bus

    return bus.publish(stream, envelope)


@dataclass(frozen=True)
class AlertRules:
    importance: int
    held_importance: int
    alias_resync_seconds: float

    @classmethod
    def from_defaults(cls) -> AlertRules:
        cfg: dict[str, Any] = DEFAULTS["news"]["alerts"]
        return cls(
            importance=int(cfg["importance"]),
            held_importance=int(cfg["held_importance"]),
            alias_resync_seconds=float(cfg["alias_resync_seconds"]),
        )


def _base(symbol: str) -> str:
    """``BTC-USDT-SWAP`` / ``BTC-USDT`` → ``BTC``: what a held instId and a linked family share."""
    return symbol.split("-", 1)[0].upper()


_HELD_SQL = text(
    """
    WITH tick AS (
        SELECT COALESCE(
            (SELECT last_success_at FROM sync_status WHERE env = :env AND stream = 'account'),
            (SELECT max(taken_at) FROM positions_snapshots WHERE env = :env)
        ) AS taken_at
    )
    SELECT DISTINCT p.inst_id
    FROM positions_snapshots p, tick
    WHERE p.env = :env AND p.taken_at = tick.taken_at AND p.pos <> 0
    """
)


def held_instruments(conn: Connection, env: str) -> list[str]:
    """instIds with an open position in the env's newest positions tick — the same tick the
    Portfolio API trusts (the account sync time when there is one, else the newest snapshot)."""
    return sorted(str(r[0]) for r in conn.execute(_HELD_SQL, {"env": env}))


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def alert_payload(
    item: PreparedItem, *, source_name: str, category: str | None, held: Sequence[str], reason: str
) -> dict[str, Any]:
    """The ``news.alert`` payload (documented in docs/handoff/ATL-65-news-alerts.md). Every text
    field is untrusted source data: the bridge stores it in the card and only the plain, escaped
    headline in content_md."""
    return {
        "news_item_id": str(item.id),
        "cluster_id": str(item.cluster_id),
        "headline": item.title,
        "source": item.source,
        "source_name": source_name,
        "category": category,
        "url": item.url,
        "published_at": _iso(item.published_at),
        "importance": item.importance,
        "event_type": item.event_type,
        "inst_ids": list(item.instruments),
        "asset_classes": list(item.asset_classes),
        "sentiment": float(item.sentiment) if item.sentiment is not None else 0.0,
        "novelty": float(item.novelty) if item.novelty is not None else None,
        "reason": reason,
        "held_inst_ids": list(held),
    }


def alert_envelope(env: str, payload: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope(
        id=uuid.uuid5(ALERT_NAMESPACE, payload["cluster_id"]),
        type=ALERT_TYPE,
        env=env,
        source=ALERT_SOURCE,
        correlation_id=f"news:{payload['cluster_id']}",
        payload=payload,
    )


def _sources(conn: Connection, keys: Iterable[str]) -> dict[str, tuple[str, str | None]]:
    rows = conn.execute(
        text("SELECT key, name, category FROM news_sources WHERE key = ANY(CAST(:keys AS text[]))"),
        {"keys": sorted(set(keys))},
    )
    return {str(r.key): (str(r.name), r.category) for r in rows}


_CLUSTER_MAX_SQL = text(
    """
    UPDATE news_clusters SET max_importance = COALESCE(
        (SELECT max(importance) FROM news_items WHERE cluster_id = :id), max_importance)
    WHERE id = :id
    """
)

_CLAIM_SQL = text(
    "UPDATE news_clusters SET alerted_at = :now WHERE id = :id AND alerted_at IS NULL RETURNING id"
)

_RELEASE_SQL = text("UPDATE news_clusters SET alerted_at = NULL WHERE id = :id AND alerted_at = :at")


def _undo_cluster(conn: Connection, cluster_id: uuid.UUID, created: bool) -> None:
    """The insert lost an ON CONFLICT race: take the item back out of its cluster's counts."""
    if created:
        conn.execute(
            text(
                "DELETE FROM news_clusters c WHERE c.id = :id AND NOT EXISTS "
                "(SELECT 1 FROM news_items i WHERE i.cluster_id = c.id)"
            ),
            {"id": cluster_id},
        )
    else:
        conn.execute(
            text("UPDATE news_clusters SET item_count = GREATEST(item_count - 1, 0) WHERE id = :id"),
            {"id": cluster_id},
        )


def enrich_fields(item: PreparedItem, linker: Linker, source_name: str) -> None:
    """Steps 1-3: link, classify, provisional importance (no DB, no embedding)."""
    summary = item.summary or ""
    result = linker.link(item.title, summary)
    item.instruments = list(result.instruments)
    item.asset_classes = list(result.asset_classes)
    item.entities = [asdict(e) for e in result.entities] or None
    item.event_type = event_type(item.title, summary)
    item.sentiment = headline_sentiment(f"{item.title}. {summary}" if summary else item.title)
    item.importance = _importance(item, source_name)


def _importance(item: PreparedItem, source_name: str) -> int:
    return importance(
        item.event_type or "other",
        source_name,
        len(item.instruments),
        abs(item.sentiment) if item.sentiment is not None else Decimal(0),
        item.novelty,
    )


@dataclass
class _Pending:
    cluster_id: uuid.UUID
    claimed_at: datetime
    envelope: EventEnvelope


class Enricher:
    """The ingest process's enrichment hook. Holds the linker (re-synced from ``instruments`` at
    most every ``alias_resync_seconds``), the embedder and the alert publisher."""

    def __init__(
        self,
        *,
        env: str | None = None,
        embedder: Embedder | None = None,
        linker: Linker | None = None,
        publish: Publisher | None = None,
        rules: AlertRules | None = None,
        cluster_params: ClusterParams | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.env = env or aliases.active_env()
        self._embedder = embedder
        self._linker = linker
        self._fixed_linker = linker is not None
        self.publish = publish or _bus_publish
        self.rules = rules or AlertRules.from_defaults()
        self.cluster_params = cluster_params
        self._monotonic = monotonic
        self._watcher: aliases.AliasWatcher | None = None
        self._resynced_at: float | None = None
        self.published: list[EventEnvelope] = []
        """Envelopes this enricher published (newest last), kept for logs and tests."""

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = get_default_embedder()
        return self._embedder

    def linker(self, engine: Engine) -> Linker:
        """The linker, re-synced from ``instruments`` when the env's instrument count changed."""
        if self._fixed_linker and self._linker is not None:
            return self._linker
        now = self._monotonic()
        due = self._resynced_at is None or now - self._resynced_at >= self.rules.alias_resync_seconds
        if due:
            self._resynced_at = now
            if self._watcher is None or self._watcher.engine is not engine:
                self._watcher = aliases.AliasWatcher(engine, self.env)
            try:
                written = self._watcher.poll()
            except Exception:
                logger.exception("alias sync failed; linking with the stored aliases")
                written = None
            if written is not None or self._linker is None:
                self._linker = aliases.load_linker(engine, self.env)
                set_default_linker(self._linker)
        assert self._linker is not None
        return self._linker

    async def __call__(self, items: Sequence[PreparedItem], ctx: EnrichContext) -> int:
        if not items:
            return 0
        ordered = sorted(items, key=lambda i: i.published_at)
        vectors = await asyncio.to_thread(
            self.embedder.embed, [item_text(i.title, i.summary) for i in ordered]
        )
        inserted, pending = await asyncio.to_thread(self._store, ctx, ordered, vectors)
        if pending:
            await asyncio.to_thread(self._publish_all, ctx.engine, pending)
        return inserted

    def _store(
        self, ctx: EnrichContext, items: Sequence[PreparedItem], vectors: Sequence[Sequence[float]]
    ) -> tuple[int, list[_Pending]]:
        linker = self.linker(ctx.engine)
        inserted = 0
        pending: list[_Pending] = []
        with ctx.engine.begin() as conn:
            sources = _sources(conn, (i.source for i in items))
            held = held_instruments(conn, self.env)
            held_bases = {_base(h) for h in held}
            for item, vec in zip(items, vectors, strict=True):
                name, category = sources.get(item.source, (item.source, None))
                enrich_fields(item, linker, name)
                assignment = assign(conn, item, vec, self.cluster_params)
                item.importance = _importance(item, name)
                assert item.cluster_id is not None
                stored = conn.execute(
                    insert(NewsItem)
                    .values(**item.row())
                    .on_conflict_do_nothing(index_elements=["canonical_url_hash"])
                    .returning(NewsItem.id)
                ).first()
                if stored is None:
                    _undo_cluster(conn, item.cluster_id, assignment.created)
                    continue
                inserted += 1
                conn.execute(_CLUSTER_MAX_SQL, {"id": item.cluster_id})

                item_bases = {_base(i) for i in item.instruments}
                held_hit = [h for h in held if _base(h) in item_bases]
                if item.importance >= self.rules.importance:
                    reason = "importance"
                elif item.importance >= self.rules.held_importance and held_bases & item_bases:
                    reason = "held_instrument"
                else:
                    continue
                claimed = conn.execute(_CLAIM_SQL, {"id": item.cluster_id, "now": ctx.now}).first()
                if claimed is None:
                    continue  # this story already alerted
                payload = alert_payload(
                    item, source_name=name, category=category, held=held_hit, reason=reason
                )
                pending.append(_Pending(item.cluster_id, ctx.now, alert_envelope(self.env, payload)))
        return inserted, pending

    def _publish_all(self, engine: Engine, pending: Sequence[_Pending]) -> None:
        for p in pending:
            try:
                self.publish(ALERT_STREAM, p.envelope)
            except Exception as exc:
                # Redis down: release the claim so the story's next item can alert. Never log the
                # payload (untrusted text) or the exception message (it may carry connection details).
                logger.error(
                    "news.alert publish failed",
                    extra={"cluster_id": str(p.cluster_id), "exception": type(exc).__name__},
                )
                with engine.begin() as conn:
                    conn.execute(_RELEASE_SQL, {"id": p.cluster_id, "at": p.claimed_at})
                continue
            self.published.append(p.envelope)
            logger.info(
                "news.alert published",
                extra={
                    "cluster_id": str(p.cluster_id),
                    "importance": p.envelope.payload["importance"],
                    "reason": p.envelope.payload["reason"],
                },
            )


_default: Enricher | None = None


def set_default_enricher(enricher: Enricher | None) -> None:
    """Install the enricher :func:`enrich_items` uses (``__main__`` and tests)."""
    global _default
    _default = enricher


def default_enricher() -> Enricher:
    global _default
    if _default is None:
        _default = Enricher()
    return _default


async def enrich_items(items: Sequence[PreparedItem], ctx: EnrichContext) -> int:
    """The pipeline's default hook: the process-wide :class:`Enricher`."""
    return await default_enricher()(items, ctx)
