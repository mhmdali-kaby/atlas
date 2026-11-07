"""CryptoPanic posts collector (ATL-60, source key ``cryptopanic``).

Request: ``GET <source url>?auth_token=<key>&public=true&kind=news`` (the seeded URL is the v1
``/api/v1/posts/`` endpoint; the developer v2 path ``/api/developer/v2/posts/`` answers in the same
shape and works by changing the source's URL). ``public=true`` is the documented "public feed"
filter: news for everyone rather than the key owner's portfolio. CryptoPanic only accepts the key
as the ``auth_token`` query parameter, so the URL carries it: it is never logged (httpx's request
log is pinned to WARNING), never put in ``raw`` and never in an error message; the runner also
redacts it from ``last_error``.

Terms (from CryptoPanic's developer page; not re-read at build time because the build had no
network access, so re-check on a networked run): the free tier is rate limited per key (a few
requests per second, with a monthly request quota) and returns 429 when exceeded, and posts may be
delayed for free keys. One request per poll at the seeded 180 s keeps far inside both. A 429 is one
failure and the next poll waits at least ``Retry-After`` (else
``news.collectors.cryptopanic.rate_limit_window_seconds``).

Response (documented shape)::

    {"count": 200, "next": "...", "previous": null, "results": [
      {"kind": "news", "domain": "coindesk.com", "title": "...", "published_at": "2026-10-29T07:12:00Z",
       "slug": "...", "id": 21000001, "url": "https://cryptopanic.com/news/21000001/...",
       "original_url": "https://www.coindesk.com/...",          # paid plans only
       "source": {"title": "CoinDesk", "region": "en", "domain": "coindesk.com", "path": null},
       "currencies": [{"code": "BTC", "title": "Bitcoin", "slug": "bitcoin", "url": "..."}],
       "votes": {...}}]}

The article link is ``original_url`` when the plan provides it, else CryptoPanic's own ``url``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from atlas_shared.defaults import DEFAULTS

from atlas_ingest.collectors import (
    CollectContext,
    CollectError,
    RateLimited,
    SourceConfig,
    read_capped,
    register,
    retry_after_seconds,
)
from atlas_ingest.items import RawItem

DEFAULT_URL = "https://cryptopanic.com/api/v1/posts/"


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def parse_posts(body: Any) -> list[RawItem]:
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise CollectError("unexpected response shape")
    items: list[RawItem] = []
    for post in body["results"]:
        if not isinstance(post, dict):
            continue
        title = str(post.get("title") or "").strip()
        url = str(post.get("original_url") or post.get("url") or "").strip()
        if not title or not url.startswith(("http://", "https://")):
            continue
        src_value = post.get("source")
        src: dict[str, Any] = src_value if isinstance(src_value, dict) else {}
        currencies = [
            str(c["code"]) for c in post.get("currencies") or [] if isinstance(c, dict) and c.get("code")
        ]
        region = src.get("region") if isinstance(src.get("region"), str) else None
        items.append(
            RawItem(
                url=url,
                title=title,
                published_at=parse_time(post.get("published_at")),
                language=region,
                raw={
                    "provider": "cryptopanic",
                    "id": post.get("id"),
                    "kind": post.get("kind"),
                    "publisher": src.get("title"),
                    "domain": post.get("domain") or src.get("domain"),
                    "currencies": currencies,
                    "cryptopanic_url": post.get("url"),
                },
            )
        )
    return items


@register(keys=("cryptopanic",))
class CryptoPanicCollector:
    def __init__(self) -> None:
        conf: dict[str, Any] = DEFAULTS["news"]["collectors"]["cryptopanic"]
        self.rate_limit_window = float(conf["rate_limit_window_seconds"])

    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]:
        if not source.api_key:
            raise CollectError("api key missing")
        async with ctx.http.stream(
            "GET",
            source.url or DEFAULT_URL,
            params={"auth_token": source.api_key, "public": "true", "kind": "news"},
            headers={"Accept": "application/json"},
        ) as response:
            if response.status_code == 429:
                raise RateLimited(retry_after_seconds(response, self.rate_limit_window))
            response.raise_for_status()
            raw = await read_capped(response)
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise CollectError("response is not JSON") from exc
        return parse_posts(body)
