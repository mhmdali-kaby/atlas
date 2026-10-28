"""Fixture mode (``ATLAS_OKX_FIXTURES=1``): the OKX client serves recorded JSON instead of calling OKX.

For the Playwright flows (ATL-25) and local demos with no network. Nothing leaves the machine: every
request is answered by an ``httpx.MockTransport`` from the recorded files in
``services/trading_core/tests/fixtures/okx`` (or ``ATLAS_OKX_FIXTURES_DIR``).

A request for ``/api/v5/<group>/<name>`` is served from ``<group>_<name>.json`` (dashes become
underscores), preferring ``<group>_<name>_<insttype>.json`` when the query has ``instType``. A request
with a paging cursor (``after``/``before``) gets an empty page, so paging loops end after one page. An
endpoint with no recorded file returns an empty ``data`` list. ``public/time`` is answered with the
current time, so the client's clock offset stays near zero.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

ENABLE_VAR = "ATLAS_OKX_FIXTURES"
DIR_VAR = "ATLAS_OKX_FIXTURES_DIR"
API_PREFIX = "/api/v5/"

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "okx"


def fixtures_enabled() -> bool:
    return os.environ.get(ENABLE_VAR, "").strip().lower() in ("1", "true", "yes")


def fixtures_dir() -> Path:
    override = os.environ.get(DIR_VAR, "").strip()
    return Path(override) if override else DEFAULT_DIR


def _empty() -> dict[str, object]:
    return {"code": "0", "data": [], "msg": ""}


def fixture_body(path: str, params: httpx.QueryParams, directory: Path) -> dict[str, object]:
    """The JSON body fixture mode returns for one GET."""
    if not path.startswith(API_PREFIX):
        return {"code": "50000", "data": [], "msg": "not an OKX v5 path"}
    endpoint = path[len(API_PREFIX) :].strip("/")
    if endpoint == "public/time":
        return {"code": "0", "data": [{"ts": str(time.time_ns() // 1_000_000)}], "msg": ""}
    if params.get("after") or params.get("before"):
        return _empty()
    stem = endpoint.replace("/", "_").replace("-", "_")
    candidates = []
    inst_type = params.get("instType")
    if inst_type:
        candidates.append(f"{stem}_{inst_type.lower()}.json")
    candidates.append(f"{stem}.json")
    for name in candidates:
        file = directory / name
        if file.is_file():
            loaded: dict[str, object] = json.loads(file.read_text(encoding="utf-8"))
            return loaded
    return _empty()


def fixture_transport(directory: Path | None = None) -> httpx.MockTransport:
    root = directory or fixtures_dir()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return httpx.Response(405, json={"code": "50000", "data": [], "msg": "fixture mode is read-only"})
        return httpx.Response(200, json=fixture_body(request.url.path, request.url.params, root))

    return httpx.MockTransport(handler)


__all__ = ["ENABLE_VAR", "fixture_body", "fixture_transport", "fixtures_dir", "fixtures_enabled"]
