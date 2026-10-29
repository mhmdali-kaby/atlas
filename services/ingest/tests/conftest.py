"""Shared test setup for atlas_ingest.

The pipeline's default enrichment hook (ATL-65) would load the real embedding model, re-sync the
alias table and publish alerts to Redis. Every test gets a default enricher with the deterministic
FakeEmbedder, an empty linker and a publisher that only records; tests about enrichment build their
own :class:`~atlas_ingest.enrichment.Enricher`.

ATL-73: ``Extractor.article_text`` resolves a feed link's host with real DNS before fetching it
(SSRF guard). Tests use respx-recorded hosts like ``www.coindesk.com`` that respx never actually
dials, so a real lookup is an unwanted, flaky network dependency here; every test gets a fake
resolver that reports a public address for any host, unless it overrides ``resolve_host`` itself
(the SSRF tests do, to exercise the real resolver against real loopback servers).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest
from atlas_ingest.embedding import FakeEmbedder
from atlas_ingest.enrichment import Enricher, set_default_enricher
from atlas_ingest.linking import Linker

PUBLIC_TEST_ADDRESS = "93.184.216.34"


async def _fake_public_resolver(_host: str, _port: int) -> Sequence[str]:
    return [PUBLIC_TEST_ADDRESS]


@pytest.fixture(autouse=True)
def _offline_default_enricher() -> Iterator[None]:
    set_default_enricher(
        Enricher(env="demo", embedder=FakeEmbedder(), linker=Linker([], {}), publish=lambda _s, _e: None)
    )
    yield
    set_default_enricher(None)


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("atlas_ingest.extract.resolve_host_addresses", _fake_public_resolver)
