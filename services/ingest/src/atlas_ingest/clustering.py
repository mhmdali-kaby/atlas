"""Story clustering and novelty (brief §7.2 step 5, decision #243).

One rule, two places it runs:

* :func:`assign` — live ingest. Finds candidate items with pgvector cosine search (HNSW index) among
  items published within ``window_hours``, scores each candidate, joins the best cluster above the
  threshold or creates a new ``news_clusters`` row, and keeps the cluster's ``first_seen`` (earliest
  published_at), ``last_seen``, ``item_count``, ``max_importance`` and ``instruments`` current.
* :class:`StoryClusterer` / :func:`cluster_batch` — the same rule in memory, for the offline scorer
  (registered as the report's ``clusterer`` stage through :func:`cluster_rows`).

The pair score (weights in defaults.yaml ``news.clustering``)::

    cosine + title_weight * token_set_ratio/100 + number_weight * [same figure stated]
           + instrument_weight * [linked instruments overlap] - conflict_penalty * [both linked, disjoint]
           + event_type_weight * [same event_type]

A join also requires the cluster to keep spanning at most ``window_hours`` from its earliest to its
latest item, so two items published more than 12 h apart are never in one cluster.

``novelty`` = 1 - the highest cosine to any item published in the ``novelty_hours`` before the item
(1.0 when there is none).
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from atlas_shared.defaults import DEFAULTS
from rapidfuzz import fuzz
from sqlalchemy import Connection, text

from atlas_ingest.embedding import (
    Embedder,
    cosine,
    current_default_embedder,
    get_default_embedder,
    item_text,
    make_embedder,
)
from atlas_ingest.items import PreparedItem

# --------------------------------------------------------------------------------------------------
# Parameters


@dataclass(frozen=True)
class ClusterParams:
    window_hours: float
    join_threshold: float
    title_weight: float
    number_weight: float
    instrument_weight: float
    conflict_penalty: float
    event_type_weight: float
    candidate_limit: int
    novelty_hours: float

    @property
    def window(self) -> timedelta:
        return timedelta(hours=self.window_hours)

    @property
    def novelty_window(self) -> timedelta:
        return timedelta(hours=self.novelty_hours)

    @classmethod
    def from_defaults(cls, overrides: Mapping[str, Any] | None = None) -> ClusterParams:
        cfg: dict[str, Any] = {**DEFAULTS["news"]["clustering"], **(overrides or {})}
        return cls(
            window_hours=float(cfg["window_hours"]),
            join_threshold=float(cfg["join_threshold"]),
            title_weight=float(cfg["title_weight"]),
            number_weight=float(cfg["number_weight"]),
            instrument_weight=float(cfg["instrument_weight"]),
            conflict_penalty=float(cfg["conflict_penalty"]),
            event_type_weight=float(cfg["event_type_weight"]),
            candidate_limit=int(cfg["candidate_limit"]),
            novelty_hours=float(cfg["novelty_hours"]),
        )


# --------------------------------------------------------------------------------------------------
# Figures stated in a headline ("$48 million", "48M", "4,050", "126 ألف", "1.900 millones" is not
# parsed as 1.9 thousand — commas and dots are treated as thousands separators only when the group
# after them has exactly three digits).

_MULTIPLIERS: dict[str, float] = {
    "k": 1e3,
    "thousand": 1e3,
    "ألف": 1e3,
    "الف": 1e3,
    "m": 1e6,
    "mn": 1e6,
    "million": 1e6,
    "millones": 1e6,
    "millions": 1e6,
    "millionen": 1e6,
    "мил": 1e6,
    "مليون": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "billions": 1e9,
    "milliarden": 1e9,
    "مليار": 1e9,
    "万": 1e4,
    "亿": 1e8,
    "億": 1e8,
}
_NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?)\s*("
    + "|".join(sorted((re.escape(k) for k in _MULTIPLIERS), key=len, reverse=True))
    + r")?(?![^\W\d_])",
    re.IGNORECASE,
)
_YEARS = range(1990, 2101)


def extract_numbers(text_: str) -> frozenset[float]:
    """Distinctive figures in ``text_``, scaled by any magnitude word. Small bare integers (< 10) and
    bare years are dropped: they match too much to say two headlines are the same story."""
    found: set[float] = set()
    for m in _NUMBER.finditer(text_):
        raw, suffix = m.group(1), (m.group(2) or "").lower()
        grouped = re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+", raw) is not None
        try:
            value = float(re.sub(r"[,.]", "", raw)) if grouped else float(raw)
        except ValueError:
            continue
        mult = _MULTIPLIERS.get(suffix, 1.0)
        if mult == 1.0 and not grouped and "." not in raw and (value < 10 or int(value) in _YEARS):
            continue
        found.add(round(value * mult, 6))
    return frozenset(found)


# --------------------------------------------------------------------------------------------------
# The pair score


@dataclass(frozen=True)
class StoryItem:
    """What the rule needs to know about one item."""

    key: str
    title: str
    published_at: datetime
    embedding: Sequence[float]
    summary: str = ""
    instruments: frozenset[str] = frozenset()
    event_type: str | None = None
    importance: int = 1
    numbers: frozenset[float] = field(default=frozenset())

    @classmethod
    def build(
        cls,
        key: str,
        title: str,
        published_at: datetime,
        embedding: Sequence[float],
        *,
        summary: str | None = None,
        instruments: Iterable[str] = (),
        event_type: str | None = None,
        importance: int = 1,
    ) -> StoryItem:
        return cls(
            key=key,
            title=title,
            published_at=published_at,
            embedding=embedding,
            summary=summary or "",
            instruments=frozenset(instruments),
            event_type=event_type,
            importance=importance,
            numbers=extract_numbers(f"{title} {summary or ''}"),
        )


def title_ratio(a: str, b: str) -> float:
    return float(fuzz.token_set_ratio(a.casefold(), b.casefold())) / 100.0


def pair_score(a: StoryItem, b: StoryItem, cos: float, params: ClusterParams) -> float:
    score = cos + params.title_weight * title_ratio(a.title, b.title)
    if a.numbers & b.numbers:
        score += params.number_weight
    if a.instruments and b.instruments:
        if a.instruments & b.instruments:
            score += params.instrument_weight
        else:
            score -= params.conflict_penalty
    if a.event_type is not None and a.event_type == b.event_type:
        score += params.event_type_weight
    return score


def novelty_from(max_cosine: float | None) -> float:
    """1 - the highest cosine to recent items, clamped to 0..1; 1.0 when there were none."""
    if max_cosine is None or math.isnan(max_cosine):
        return 1.0
    return min(1.0, max(0.0, 1.0 - max_cosine))


def _span_ok(first: datetime, last: datetime, published: datetime, window: timedelta) -> bool:
    return max(last, published) - min(first, published) <= window


# --------------------------------------------------------------------------------------------------
# In memory


@dataclass
class ClusterState:
    id: str
    headline: str
    first_seen: datetime
    last_seen: datetime
    item_count: int = 0
    max_importance: int = 1
    instruments: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Assignment:
    cluster_id: str
    novelty: float
    created: bool


class StoryClusterer:
    """The clustering rule over items held in memory (offline scoring, tests)."""

    def __init__(self, params: ClusterParams | None = None) -> None:
        self.params = params or ClusterParams.from_defaults()
        self.items: list[StoryItem] = []
        self.item_cluster: dict[str, str] = {}
        self.clusters: dict[str, ClusterState] = {}

    def add(self, item: StoryItem) -> Assignment:
        p = self.params
        best: tuple[float, str] | None = None
        max_cos: float | None = None
        for other in self.items:
            delta = item.published_at - other.published_at
            cos = cosine(item.embedding, other.embedding)
            if timedelta(0) <= delta <= p.novelty_window:
                max_cos = cos if max_cos is None else max(max_cos, cos)
            if abs(delta) > p.window:
                continue
            cid = self.item_cluster[other.key]
            c = self.clusters[cid]
            if not _span_ok(c.first_seen, c.last_seen, item.published_at, p.window):
                continue
            score = pair_score(item, other, cos, p)
            if score > p.join_threshold and (best is None or score > best[0]):
                best = (score, cid)

        created = best is None
        if best is None:
            cid = f"k-{len(self.clusters) + 1}"
            self.clusters[cid] = ClusterState(
                id=cid, headline=item.title, first_seen=item.published_at, last_seen=item.published_at
            )
        else:
            cid = best[1]
        c = self.clusters[cid]
        c.first_seen = min(c.first_seen, item.published_at)
        c.last_seen = max(c.last_seen, item.published_at)
        c.item_count += 1
        c.max_importance = max(c.max_importance, item.importance)
        c.instruments |= item.instruments
        self.items.append(item)
        self.item_cluster[item.key] = cid
        return Assignment(cluster_id=cid, novelty=novelty_from(max_cos), created=created)


def cluster_batch(items: Sequence[StoryItem], params: ClusterParams | None = None) -> dict[str, str]:
    """{item key: cluster id} for ``items``, fed in published_at order exactly as ingest would."""
    clusterer = StoryClusterer(params)
    for item in sorted(items, key=lambda i: i.published_at):
        clusterer.add(item)
    return dict(clusterer.item_cluster)


def cluster_rows(rows: Sequence[Any], embedder: Embedder | None = None) -> dict[str, str]:
    """The report's ``clusterer`` stage over fixture rows (``HeadlineRow``).

    Instruments and event types come from the linker and classifier registered for the same report,
    as they would from the live pipeline; either may be absent, which drops that term. The embedder is
    ``embedder``, else the one the report chose (``--embedder``), else ``auto``: the real model only
    when it is already cached, so scoring never downloads a model by surprise.
    """
    from atlas_ingest.evaluation import registry

    emb = embedder or current_default_embedder() or make_embedder("auto")
    linker = registry.get_linker()
    classifier = registry.get_classifier()
    vectors = emb.embed([item_text(r.title, r.summary) for r in rows])
    items = [
        StoryItem.build(
            r.id,
            r.title,
            r.published_at,
            vec,
            summary=r.summary,
            instruments=linker(r) if linker is not None else (),
            event_type=classifier(r) if classifier is not None else None,
        )
        for r, vec in zip(rows, vectors, strict=True)
    ]
    return cluster_batch(items)


# --------------------------------------------------------------------------------------------------
# Live ingest (Postgres + pgvector)


def _vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


def _decimal4(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _story_item(item: PreparedItem, embedding: Sequence[float]) -> StoryItem:
    return StoryItem.build(
        str(item.id),
        item.title,
        item.published_at,
        embedding,
        summary=item.summary,
        instruments=item.instruments,
        event_type=item.event_type,
        importance=item.importance,
    )


_NEAREST_SQL = text(
    """
    SELECT i.id, i.title, i.summary, i.published_at, i.instruments, i.event_type, i.importance,
           i.cluster_id, 1 - (i.embedding <=> CAST(:q AS vector)) AS cos,
           c.first_seen, c.last_seen
    FROM news_items i
    LEFT JOIN news_clusters c ON c.id = i.cluster_id
    WHERE i.embedding IS NOT NULL
      AND i.id <> :id
      AND i.published_at >= :since AND i.published_at <= :until
    ORDER BY i.embedding <=> CAST(:q AS vector)
    LIMIT :limit
    """
)


def _nearest(
    conn: Connection,
    item_id: uuid.UUID,
    vec: str,
    since: datetime,
    until: datetime,
    limit: int,
) -> list[Any]:
    # pgvector >= 0.8: keep scanning the HNSW graph until LIMIT rows pass the time filter.
    conn.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
    rows = conn.execute(
        _NEAREST_SQL, {"q": vec, "id": item_id, "since": since, "until": until, "limit": limit}
    ).all()
    # relaxed_order may return rows slightly out of order; the caller only needs the set.
    return sorted(rows, key=lambda r: -float(r.cos))


def assign(
    conn: Connection,
    item: PreparedItem,
    embedding: Sequence[float],
    params: ClusterParams | None = None,
) -> Assignment:
    """Cluster one item against what is already in ``news_items`` and set its ``embedding``,
    ``cluster_id`` and ``novelty``.

    Creates or updates the ``news_clusters`` row on ``conn`` (run it in the transaction that inserts
    the item). Items of the same poll must be inserted (or flushed) one by one after their
    ``assign``, in published_at order — :func:`cluster_items` does that for a batch.
    """
    p = params or ClusterParams.from_defaults()
    vec = _vector_literal(embedding)
    me = _story_item(item, embedding)
    published = item.published_at

    recent = _nearest(conn, item.id, vec, published - p.novelty_window, published, 1)
    novelty = novelty_from(float(recent[0].cos) if recent else None)

    candidates = _nearest(conn, item.id, vec, published - p.window, published + p.window, p.candidate_limit)
    best: tuple[float, uuid.UUID] | None = None
    for row in candidates:
        if row.cluster_id is None or row.first_seen is None:
            continue
        if not _span_ok(row.first_seen, row.last_seen, published, p.window):
            continue
        other = StoryItem.build(
            str(row.id),
            row.title,
            row.published_at,
            (),
            summary=row.summary,
            instruments=row.instruments or (),
            event_type=row.event_type,
            importance=row.importance,
        )
        score = pair_score(me, other, float(row.cos), p)
        if score > p.join_threshold and (best is None or score > best[0]):
            best = (score, row.cluster_id)

    instruments = sorted(set(item.instruments))
    if best is None:
        cluster_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO news_clusters
                    (id, headline, first_seen, last_seen, item_count, max_importance, instruments,
                     created_at, updated_at)
                VALUES (:id, :headline, :ts, :ts, 1, :imp, CAST(:inst AS text[]), now(), now())
                """
            ),
            {
                "id": cluster_id,
                "headline": item.title,
                "ts": published,
                "imp": item.importance,
                "inst": instruments,
            },
        )
    else:
        cluster_id = best[1]
        conn.execute(
            text(
                """
                UPDATE news_clusters SET
                    first_seen = LEAST(first_seen, :ts),
                    last_seen = GREATEST(last_seen, :ts),
                    item_count = item_count + 1,
                    max_importance = GREATEST(max_importance, :imp),
                    instruments = ARRAY(
                        SELECT DISTINCT x FROM unnest(instruments || CAST(:inst AS text[])) AS x ORDER BY x
                    ),
                    updated_at = now()
                WHERE id = :id
                """
            ),
            {"id": cluster_id, "ts": published, "imp": item.importance, "inst": instruments},
        )

    item.embedding = [float(v) for v in embedding]
    item.cluster_id = cluster_id
    item.novelty = _decimal4(novelty)
    return Assignment(cluster_id=str(cluster_id), novelty=novelty, created=best is None)


def cluster_items(
    conn: Connection,
    items: Sequence[PreparedItem],
    embedder: Embedder | None = None,
    params: ClusterParams | None = None,
) -> list[Assignment]:
    """Embed, cluster and insert a batch of new items in published_at order on ``conn``.

    Each item is inserted right after its :func:`assign`, so later items of the same batch see it.
    Pass only items whose canonical URL is not stored yet (the pipeline filters seen URLs first):
    a duplicate is skipped by ``ON CONFLICT DO NOTHING`` but its cluster was already counted.
    """
    from atlas_shared.db import NewsItem
    from sqlalchemy.dialects.postgresql import insert

    emb = embedder or get_default_embedder()
    ordered = sorted(items, key=lambda i: i.published_at)
    vectors = emb.embed([item_text(i.title, i.summary) for i in ordered])
    out: list[Assignment] = []
    for item, vec in zip(ordered, vectors, strict=True):
        out.append(assign(conn, item, vec, params))
        conn.execute(
            insert(NewsItem)
            .values(**item.row())
            .on_conflict_do_nothing(index_elements=["canonical_url_hash"])
        )
    return out
