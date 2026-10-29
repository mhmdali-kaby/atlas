"""Shared test setup for atlas_ingest.

The pipeline's default enrichment hook (ATL-65) would load the real embedding model, re-sync the
alias table and publish alerts to Redis. Every test gets a default enricher with the deterministic
FakeEmbedder, an empty linker and a publisher that only records; tests about enrichment build their
own :class:`~atlas_ingest.enrichment.Enricher`.

ATL-73: ``Extractor.article_text`` resolves a feed link's host with real DNS before fetching it,
then re-verifies the connection's actual peer against that resolved address (the DNS-rebinding
guard) before reading anything (SSRF guard). Tests use respx-recorded hosts like
``www.coindesk.com`` that respx never actually dials, so a real lookup is an unwanted, flaky
network dependency here, and a mocked response has no real socket to verify a peer on at all.
Every test gets a fake resolver that reports a public address for any host, and a peer check that
trusts any connection, unless it overrides ``resolve_host``/``verify_peer`` itself (the SSRF tests
do, to exercise the real resolver and the real peer check against real loopback servers).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import httpx
import pytest
from atlas_ingest.embedding import FakeEmbedder
from atlas_ingest.enrichment import Enricher, set_default_enricher
from atlas_ingest.linking import Linker

PUBLIC_TEST_ADDRESS = "93.184.216.34"


async def _fake_public_resolver(_host: str, _port: int) -> Sequence[str]:
    return [PUBLIC_TEST_ADDRESS]


def _trust_any_peer(_response: httpx.Response, _allowed: Sequence[str]) -> bool:
    return True


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
    monkeypatch.setattr("atlas_ingest.extract.peer_matches", _trust_any_peer)
