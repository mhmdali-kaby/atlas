"""ATL-64 acceptance: with the real embedding model, clustering reaches pairwise F1 >= 0.90 on the
300-headline fixture. Skipped when the model is not in the local cache, so no test ever downloads it
(fetch it once with `python -m atlas_ingest.evaluation report --embedder real`)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from atlas_ingest.clustering import cluster_rows
from atlas_ingest.embedding import SentenceTransformerEmbedder, model_is_cached
from atlas_ingest.evaluation import registry
from atlas_ingest.evaluation.__main__ import TARGET_CLUSTER_F1
from atlas_ingest.evaluation.fixture import gold_clusters, load_fixture
from atlas_ingest.evaluation.scoring import score_clustering

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(not model_is_cached(), reason="embedding model not cached (ATLAS_MODEL_CACHE)"),
]


@pytest.fixture
def stages() -> Iterator[None]:
    # The same linker/classifier the report would use, from their entry points.
    registry.clear()
    registry.load_entry_points()
    yield
    registry.clear()


def test_real_model_clusters_fixture_at_target_f1(stages: None) -> None:
    rows = load_fixture()
    labels = cluster_rows(rows, embedder=SentenceTransformerEmbedder())
    score = score_clustering(labels, gold_clusters(rows))
    assert score.pairwise_f1 >= TARGET_CLUSTER_F1, score
