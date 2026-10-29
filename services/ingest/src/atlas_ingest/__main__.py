"""`uv run --package atlas-ingest python -m atlas_ingest` runs the news/calendar ingest loop
(decision #243). ``./atlas up`` starts it beside trading_core; SIGTERM/SIGINT stop it cleanly."""

from __future__ import annotations

import asyncio
import logging
import signal

import httpx
from atlas_shared.db import make_engine

from atlas_ingest.aliases import active_env
from atlas_ingest.collectors import discover
from atlas_ingest.config import IngestSettings
from atlas_ingest.enrichment import Enricher
from atlas_ingest.extract import Extractor
from atlas_ingest.logs import configure_logging
from atlas_ingest.pipeline import Pipeline
from atlas_ingest.runner import IngestRunner, RunnerConfig

logger = logging.getLogger("atlas_ingest")

# Feeds (Yahoo answers browser agents with 429) use the plain agent; article pages (CoinDesk answers
# unknown agents with 429) use a browser one. Checked against the live sites on 2026-10-29.
USER_AGENT = "Atlas-Ingest/0.1 (+local news reader)"
ARTICLE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.0 Safari/605.1.15"
)


async def amain(settings: IngestSettings) -> None:
    discover()
    engine = make_engine()
    env = active_env()
    # ATL-65: link → classify → cluster → importance, then news.alert on atlas.warroom.
    enricher = Enricher(env=env)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as http:
        pipeline = Pipeline(
            engine=engine,
            extractor=Extractor(
                http=http,
                timeout_seconds=settings.extract_timeout_seconds,
                max_bytes=settings.extract_max_bytes,
                user_agent=ARTICLE_USER_AGENT,
            ),
            max_extract_per_poll=settings.extract_max_per_poll,
            enrich=enricher,
        )
        runner = IngestRunner(
            engine=engine,
            http=http,
            pipeline=pipeline,
            config=RunnerConfig(
                reload_seconds=settings.reload_seconds,
                failure_threshold=settings.failure_threshold,
                disable_minutes=settings.disable_minutes,
            ),
            app_key=settings.app_key,
            env=env,
        )
        logger.info("ingest started", extra={"tick_seconds": settings.tick_seconds})
        await runner.run_forever(stop, settings.tick_seconds)
    engine.dispose()
    logger.info("ingest stopped")


def main() -> None:
    configure_logging()
    asyncio.run(amain(IngestSettings.from_env()))


if __name__ == "__main__":
    main()
