"""Shared test setup for atlas_ingest.

The pipeline's default enrichment hook (ATL-65) would load the real embedding model, re-sync the
alias table and publish alerts to Redis. Every test gets a default enricher with the deterministic
FakeEmbedder, an empty linker and a publisher that only records; tests about enrichment build their
own :class:`~atlas_ingest.enrichment.Enricher`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from atlas_ingest.embedding import FakeEmbedder
from atlas_ingest.enrichment import Enricher, set_default_enricher
from atlas_ingest.linking import Linker


@pytest.fixture(autouse=True)
def _offline_default_enricher() -> Iterator[None]:
    set_default_enricher(
        Enricher(env="demo", embedder=FakeEmbedder(), linker=Linker([], {}), publish=lambda _s, _e: None)
    )
    yield
    set_default_enricher(None)
