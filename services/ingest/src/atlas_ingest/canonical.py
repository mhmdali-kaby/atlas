"""URL canonicalisation for news dedupe (ATL-59).

``news_items.canonical_url_hash`` is sha256 of the canonical URL, so two links to the same article
must canonicalise to the same bytes: lowercase scheme and host, no default port, no fragment, no
tracking parameters (``utm_*``, ``fbclid``, ``gclid``, and Yahoo's ``.tsrc``), no trailing slash on
the path. Other query parameters keep their order, because some sites route articles by query string.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset({"fbclid", "gclid", ".tsrc"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("utm_") or lowered in TRACKING_PARAMS


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port is None or _DEFAULT_PORTS.get(scheme) == port else f"{host}:{port}"
    path = parts.path.rstrip("/")
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(canonical_url: str) -> bytes:
    """Same bytes as the DB's ``atlas_sha256_text(canonical_url)`` (sha256 of the UTF-8 text)."""
    return hashlib.sha256(canonical_url.encode("utf-8")).digest()
