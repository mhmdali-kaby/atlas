"""Circuit-breaker consumer and keys under the Redis key prefix (ATL-186)."""

import asyncio
from typing import Any

import pytest
from atlas_shared import bus
from atlas_shared.events import EventEnvelope
from trading_core.risk.breakers import BreakerMonitor, okx_errors_key


class OneShotRedis:
    """Answers one XREAD with an entry on every requested key, then cancels the consumer."""

    def __init__(self) -> None:
        self.requested: list[list[str]] = []

    def xread(
        self, streams: dict[str, str], **_kw: Any
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        self.requested.append(list(streams))
        if len(self.requested) > 1:
            raise asyncio.CancelledError
        envelope = EventEnvelope(type="t", env="demo", source="test").model_dump_json()
        return [(key, [("1-0", {bus.FIELD: envelope})]) for key in streams]


class FakeBreakers:
    def __init__(self) -> None:
        self.rds = OneShotRedis()
        self.cfg = {"evaluate_interval_seconds": 1, "stream_block_ms": 1}
        self.seen: list[str] = []

    def on_event(self, stream: str, envelope: EventEnvelope) -> list[str]:
        self.seen.append(stream)
        return []


async def _consume(monkeypatch: pytest.MonkeyPatch, prefix: str | None) -> FakeBreakers:
    if prefix is None:
        monkeypatch.delenv(bus.PREFIX_VAR, raising=False)
    else:
        monkeypatch.setenv(bus.PREFIX_VAR, prefix)
    fake = FakeBreakers()
    monitor = BreakerMonitor(fake)  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await monitor._consume()
    return fake


async def test_without_a_prefix_breakers_read_the_plain_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = await _consume(monkeypatch, None)
    assert fake.rds.requested[0] == ["atlas.system", "atlas.market"]
    assert fake.seen == ["atlas.system", "atlas.market"]
    assert okx_errors_key("demo") == "atlas:breakers:demo:okx_errors"


async def test_with_a_prefix_breakers_read_prefixed_keys_and_see_logical_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = await _consume(monkeypatch, "p1:")
    assert fake.rds.requested[0] == ["p1:atlas.system", "p1:atlas.market"]
    assert fake.seen == ["atlas.system", "atlas.market"]
    assert okx_errors_key("demo") == "p1:atlas:breakers:demo:okx_errors"
