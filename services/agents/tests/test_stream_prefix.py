"""The supervisor's stream consumer under the Redis key prefix (ATL-186)."""

import asyncio
from typing import Any

import pytest
from atlas_agents.supervisor import _consume_stream
from atlas_shared import bus
from atlas_shared.events import EventEnvelope


class FakeRedis:
    def __init__(self, stop: asyncio.Event) -> None:
        self.stop = stop
        self.keys: list[str] = []

    def xrevrange(self, key: str, *_a: Any) -> list[Any]:
        self.keys.append(key)
        return []

    def xread(self, streams: dict[str, str], **_kw: Any) -> list[Any]:
        self.keys.extend(streams)
        self.stop.set()
        envelope = EventEnvelope(type="t", env="demo", source="test").model_dump_json()
        return [(key, [("1-0", {bus.FIELD: envelope})]) for key in streams]


@pytest.mark.parametrize(("prefix", "key"), [(None, "atlas.warroom"), ("p1:", "p1:atlas.warroom")])
async def test_consumer_reads_the_physical_key(
    monkeypatch: pytest.MonkeyPatch, prefix: str | None, key: str
) -> None:
    if prefix is None:
        monkeypatch.delenv(bus.PREFIX_VAR, raising=False)
    else:
        monkeypatch.setenv(bus.PREFIX_VAR, prefix)
    stop = asyncio.Event()
    client = FakeRedis(stop)
    seen: list[EventEnvelope] = []

    async def handler(envelope: EventEnvelope) -> None:
        seen.append(envelope)

    last = await _consume_stream(client, "atlas.warroom", handler, stop=stop, block_ms=1)  # type: ignore[arg-type]
    assert client.keys == [key, key]
    assert last == "1-0"
    assert len(seen) == 1
