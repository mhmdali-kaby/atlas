"""``python -m atlas_ingest.enrich --since 72h``: enrich stored news_items that are missing it.

Picks rows published in the window whose ``event_type`` or ``embedding`` is NULL (stored before
ATL-65's hook, or by a run without it), oldest first, and fills every enrichment column exactly as
the live hook does: link → classify → cluster (embedding, cluster_id, novelty) → importance. Each
batch commits on its own, so an interrupted run keeps what it did. A second run finds nothing to do.
It never publishes ``news.alert``: backfilled stories are old news.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from atlas_shared.db import NewsItem, make_engine
from sqlalchemy import Engine, or_, select, text, update
from sqlalchemy.orm import Session

from atlas_ingest import aliases
from atlas_ingest.clustering import ClusterParams, assign
from atlas_ingest.embedding import Embedder, get_default_embedder, item_text, make_embedder
from atlas_ingest.enrichment import _CLUSTER_MAX_SQL, _importance, enrich_fields
from atlas_ingest.items import PreparedItem
from atlas_ingest.linking import Linker
from atlas_ingest.logs import configure_logging

_SINCE = re.compile(r"^(\d+)\s*([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_since(value: str) -> timedelta:
    """``30m``, ``72h``, ``7d`` → a timedelta."""
    m = _SINCE.match(value.strip().lower())
    if m is None:
        raise argparse.ArgumentTypeError(f"expected e.g. 72h, 30m or 7d, got {value!r}")
    return timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})


def _prepared(row: NewsItem) -> PreparedItem:
    return PreparedItem(
        id=row.id,
        source=row.source,
        url=row.url,
        canonical_url=row.canonical_url,
        title=row.title,
        published_at=row.published_at,
        fetched_at=row.fetched_at,
        summary=row.summary,
        body=row.body,
        language=row.language,
        raw=row.raw,
        title_original=row.title_original,
    )


def backfill(
    engine: Engine,
    *,
    since: timedelta,
    now: datetime | None = None,
    embedder: Embedder | None = None,
    linker: Linker | None = None,
    env: str | None = None,
    batch_size: int = 100,
    params: ClusterParams | None = None,
) -> int:
    """Enrich every row in the window missing event_type or embedding. Returns the rows updated."""
    start = (now or datetime.now(UTC)) - since
    emb = embedder or get_default_embedder()
    lnk = linker or aliases.load_linker(engine, env or aliases.active_env())
    done = 0
    while True:
        with engine.begin() as conn:
            names = {
                str(r.key): str(r.name) for r in conn.execute(text("SELECT key, name FROM news_sources"))
            }
            with Session(bind=conn) as session:
                rows = session.scalars(
                    select(NewsItem)
                    .where(NewsItem.published_at >= start)
                    .where(or_(NewsItem.event_type.is_(None), NewsItem.embedding.is_(None)))
                    .order_by(NewsItem.published_at, NewsItem.id)
                    .limit(batch_size)
                ).all()
                items = [_prepared(r) for r in rows]
            if not items:
                return done
            vectors = emb.embed([item_text(i.title, i.summary) for i in items])
            for item, vec in zip(items, vectors, strict=True):
                name = names.get(item.source, item.source)
                enrich_fields(item, lnk, name)
                assign(conn, item, vec, params)
                item.importance = _importance(item, name)
                conn.execute(
                    update(NewsItem)
                    .where(NewsItem.id == item.id)
                    .values(
                        instruments=item.instruments,
                        asset_classes=item.asset_classes,
                        entities=item.entities,
                        event_type=item.event_type,
                        sentiment=item.sentiment,
                        importance=item.importance,
                        novelty=item.novelty,
                        cluster_id=item.cluster_id,
                        embedding=item.embedding,
                        updated_at=datetime.now(UTC),
                    )
                )
                conn.execute(_CLUSTER_MAX_SQL, {"id": item.cluster_id})
            done += len(items)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m atlas_ingest.enrich")
    parser.add_argument(
        "--since",
        type=parse_since,
        default=parse_since("72h"),
        help="window by published_at, e.g. 72h (default), 30m, 7d",
    )
    parser.add_argument("--env", default=None, help="env whose instruments to link to (default: active env)")
    parser.add_argument("--embedder", choices=("real", "fake", "auto"), default="real")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)
    configure_logging()
    engine = make_engine()
    try:
        count = backfill(
            engine,
            since=args.since,
            embedder=make_embedder(args.embedder),
            env=args.env,
            batch_size=max(1, args.batch_size),
        )
    finally:
        engine.dispose()
    print(f"news_items enriched: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
