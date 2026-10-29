"""Quality evaluation for news enrichment against the labelled 300-headline fixture (brief §7.2).

Run ``uv run --package atlas-ingest python -m atlas_ingest.evaluation report``.
"""

from atlas_ingest.evaluation.fixture import (
    EVENT_TYPES,
    HeadlineRow,
    gold_clusters,
    gold_event_types,
    gold_linking,
    load_fixture,
)
from atlas_ingest.evaluation.registry import register_classifier, register_clusterer, register_linker
from atlas_ingest.evaluation.scoring import (
    ClusteringScore,
    EventTypeScore,
    LinkingScore,
    score_clustering,
    score_event_type,
    score_linking,
)

__all__ = [
    "EVENT_TYPES",
    "ClusteringScore",
    "EventTypeScore",
    "HeadlineRow",
    "LinkingScore",
    "gold_clusters",
    "gold_event_types",
    "gold_linking",
    "load_fixture",
    "register_classifier",
    "register_clusterer",
    "register_linker",
    "score_clustering",
    "score_event_type",
    "score_linking",
]
