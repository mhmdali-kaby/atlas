"""OKX announcements collector (ATL-60, source key ``okx.announcements``).

Request: ``GET https://www.okx.com/api/v5/support/announcements`` (public, no key; the first page,
newest first, every ``annType``), with ``Accept-Language`` from
``news.collectors.okx_announcements.language``. OKX's documented limit for the endpoint is a few
requests per 2 seconds per IP; one request per poll at the seeded 120 s is far inside it.

Response (documented shape)::

    {"code": "0", "msg": "", "data": [{"totalPage": "90", "details": [
      {"annType": "announcements-new-listings", "pTime": "1761721200000",
       "title": "OKX to list ...", "url": "https://www.okx.com/help/okx-to-list-..."}]}]}

When OKX answers with an error (HTTP error, or ``code`` other than ``"0"``) the poll fails with a
short ``OKX error <code>`` and yields nothing: there is deliberately no fallback to scraping the
announcements web pages.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from atlas_shared.defaults import DEFAULTS

from atlas_ingest.collectors import (
    CollectContext,
    CollectError,
    SourceConfig,
    read_capped,
    register,
)
from atlas_ingest.items import RawItem

DEFAULT_URL = "https://www.okx.com/api/v5/support/announcements"


def parse_announcements(body: Any) -> list[RawItem]:
    if not isinstance(body, dict):
        raise CollectError("unexpected response shape")
    code = str(body.get("code", ""))
    if code != "0":
        raise CollectError(f"OKX error {code[:16] or 'unknown'}")
    pages = body.get("data")
    if not isinstance(pages, list):
        raise CollectError("unexpected response shape")
    items: list[RawItem] = []
    for page in pages:
        details = page.get("details") if isinstance(page, dict) else None
        for row in details or []:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            url = str(row.get("url") or "").strip()
            if not title or not url.startswith(("http://", "https://")):
                continue
            published: datetime | None = None
            try:
                published = datetime.fromtimestamp(int(str(row.get("pTime"))) / 1000, UTC)
            except (TypeError, ValueError, OverflowError):
                published = None
            items.append(
                RawItem(
                    url=url,
                    title=title,
                    published_at=published,
                    language="en",
                    raw={"provider": "okx", "ann_type": row.get("annType")},
                )
            )
    return items


@register(keys=("okx.announcements",))
class OkxAnnouncementsCollector:
    def __init__(self) -> None:
        conf: dict[str, Any] = DEFAULTS["news"]["collectors"]["okx_announcements"]
        self.language = str(conf["language"])

    async def collect(self, source: SourceConfig, ctx: CollectContext) -> list[RawItem]:
        async with ctx.http.stream(
            "GET",
            source.url or DEFAULT_URL,
            headers={"Accept": "application/json", "Accept-Language": self.language},
        ) as response:
            response.raise_for_status()
            raw = await read_capped(response)
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise CollectError("response is not JSON") from exc
        return parse_announcements(body)
