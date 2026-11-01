"""Per-checkout Redis isolation for tests (ATL-186).

Every checkout shares the test Redis DB but prefixes every key with ``test_<8 hex>:``, derived like
the Postgres test DB name (#239). Production, with no prefix configured, keeps the plain names.
"""

import ast
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from atlas_shared import bus, market_data, testing
from atlas_shared.events import STREAMS, EventEnvelope

SERVICES = Path(__file__).resolve().parents[2]

# Redis methods whose arguments name keys.
KEY_METHODS = frozenset(
    {"xrange", "xrevrange", "xlen", "xadd", "xdel", "xread", "xtrim", "xinfo_stream", "delete", "exists"}
)


def test_prefix_is_test_plus_sha1_of_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_TEST_PREFIX", raising=False)
    root = testing.repo_root()
    expected = "test_" + hashlib.sha1(str(root).encode()).hexdigest()[:8] + ":"  # noqa: S324
    assert testing.redis_prefix() == expected
    assert testing.derived_redis_prefix() == expected
    # Same 8 hex as the checkout's Postgres test DB.
    assert expected == "test_" + testing.derived_database_name().removeprefix("atlas_test_") + ":"
    assert testing.derived_redis_prefix("/a") != testing.derived_redis_prefix("/b")


def test_override_wins_and_empty_override_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_TEST_PREFIX", "ci_one:")
    assert testing.redis_prefix() == "ci_one:"
    monkeypatch.setenv("REDIS_TEST_PREFIX", "")
    assert testing.redis_prefix() == testing.derived_redis_prefix()


def test_pytest_run_uses_this_checkouts_prefix() -> None:
    assert bus.key_prefix() == testing.redis_prefix()
    assert bus.key_prefix() != ""
    assert bus.stream_key("atlas.system") == testing.redis_prefix() + "atlas.system"
    assert market_data.md_key("demo", "BTC-USDT-SWAP").startswith(testing.redis_prefix())


def test_redis_settings_point_at_the_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_TEST_DB", raising=False)
    assert testing.redis_settings()["REDIS_DB"] == "15"
    monkeypatch.setenv("REDIS_TEST_DB", "14")
    assert testing.redis_settings()["REDIS_DB"] == "14"


# --- With no prefix, production uses the plain names -------------------------------------------


class RecordingRedis:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def xadd(self, key: str, fields: dict[str, str], **_kw: Any) -> str:
        self.keys.append(key)
        return "1-0"


@pytest.fixture
def no_prefix(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(bus.PREFIX_VAR, raising=False)
    yield


@pytest.mark.usefixtures("no_prefix")
def test_without_a_prefix_streams_and_keys_are_plain() -> None:
    assert bus.key_prefix() == ""
    for stream in STREAMS:
        assert bus.stream_key(stream) == stream
        assert bus.logical_stream(stream) == stream
    assert market_data.md_key("demo", "BTC-USDT-SWAP") == "atlas:md:demo:BTC-USDT-SWAP"
    assert market_data.private_key("demo") == "atlas:pws:demo"

    rec = RecordingRedis()
    bus.publish("atlas.warroom", EventEnvelope(type="t", env="demo", source="test"), client=rec)  # type: ignore[arg-type]
    assert rec.keys == ["atlas.warroom"]


def test_with_a_prefix_publish_writes_the_prefixed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bus.PREFIX_VAR, "p1:")
    rec = RecordingRedis()
    bus.publish("atlas.orders", EventEnvelope(type="t", env="demo", source="test"), client=rec)  # type: ignore[arg-type]
    assert rec.keys == ["p1:atlas.orders"]
    assert bus.logical_stream("p1:atlas.orders") == "atlas.orders"
    with pytest.raises(bus.UnknownStreamError):
        bus.stream_key("atlas.nope")


# --- No test touches an unprefixed atlas.* stream ----------------------------------------------


def _stream_like(node: ast.expr) -> bool:
    """A literal ``atlas.*`` stream name, or a bare stream-name variable, used as a Redis key."""
    if isinstance(node, ast.Starred):
        return _stream_like(node.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.startswith("atlas.")
    if isinstance(node, ast.Name):
        return "stream" in node.id.lower()
    return False


def unprefixed_stream_uses(source: str) -> list[int]:
    """Line numbers where a Redis key method gets an unprefixed stream name (not via stream_key)."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in KEY_METHODS:
            continue
        args: list[ast.expr] = list(node.args)
        for arg in node.args:
            if isinstance(arg, ast.Dict):  # xread({stream: id})
                args.extend(k for k in arg.keys if k is not None)
        if any(_stream_like(a) for a in args):
            lines.append(node.lineno)
    return lines


def test_scanner_catches_unprefixed_stream_access() -> None:
    bad = (
        'conn.delete("atlas.system")\n'
        "conn.delete(*STREAMS)\n"
        "rds.xrange(stream)\n"
        'rds.xread({"atlas.market": "$"})\n'
        "rds.xlen(SYSTEM_STREAM)\n"
    )
    assert unprefixed_stream_uses(bad) == [1, 2, 3, 4, 5]
    good = (
        'conn.delete(bus.stream_key("atlas.system"))\n'
        "conn.delete(*map(bus.stream_key, STREAMS))\n"
        "rds.xrange(bus.stream_key(stream))\n"
        "session.delete(row)\n"
    )
    assert unprefixed_stream_uses(good) == []


def test_no_python_test_touches_an_unprefixed_stream() -> None:
    files = [SERVICES / "conftest.py", *sorted(SERVICES.glob("*/tests/**/*.py"))]
    offenders: list[str] = []
    for path in files:
        if ".venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.relative_to(SERVICES)}:{n}" for n in unprefixed_stream_uses(text)]
        # Clients come from testing.redis_settings(), not a hand-built REDIS_TEST_DB env.
        if "os.environ.get(" + '"REDIS_TEST_DB"' in text:
            offenders.append(f"{path.relative_to(SERVICES)}: builds its own REDIS_TEST_DB client")
    assert len(files) > 50
    assert offenders == []
