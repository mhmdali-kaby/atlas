"""Publishes to the real local Redis (REDIS_HOST/REDIS_PORT, default 127.0.0.1:6380), on DB 15 like Pest."""

import os
from collections.abc import Iterator

import pytest
import redis
from atlas_shared import bus
from atlas_shared.events import EventEnvelope

STREAM = "atlas.warroom"


@pytest.fixture
def client() -> Iterator[redis.Redis]:
    conn = bus.make_client({**os.environ, "REDIS_DB": os.environ.get("REDIS_TEST_DB", "15")})
    try:
        conn.ping()
    except redis.ConnectionError:
        pytest.skip("Redis is not running on REDIS_HOST:REDIS_PORT")
    conn.delete(STREAM)
    yield conn
    conn.delete(STREAM)
    conn.close()


def test_envelope_round_trips_through_the_stream(client: redis.Redis) -> None:
    envelope = EventEnvelope(
        type="warroom.message",
        env="demo",
        source="agent:historian",
        correlation_id="cycle-2026-10-29",
        payload={"content_md": "hello", "n": 1.5},
    )

    entry_id = bus.publish(STREAM, envelope, client=client)

    entries = client.xrange(STREAM)
    assert entries is not None
    assert len(entries) == 1
    got_id, fields = entries[0]
    assert got_id == entry_id
    assert fields is not None
    assert list(fields) == [bus.FIELD]
    again = EventEnvelope.model_validate_json(fields[bus.FIELD])
    assert again == envelope
    assert set(again.model_dump()) == {"id", "type", "ts", "env", "source", "correlation_id", "payload"}


def test_stream_is_trimmed_approximately(client: redis.Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bus, "stream_maxlen", lambda: 10)
    for i in range(400):
        envelope = EventEnvelope(type="t", env="demo", source="test", payload={"i": i})
        bus.publish(STREAM, envelope, client=client)
    assert 10 <= client.xlen(STREAM) < 400


def test_unknown_stream_is_refused(client: redis.Redis) -> None:
    with pytest.raises(bus.UnknownStreamError, match=r"atlas\.nope"):
        bus.publish("atlas.nope", EventEnvelope(type="t", env="demo", source="test"), client=client)
    assert client.exists("atlas.nope") == 0


def test_unknown_stream_is_refused_before_connecting() -> None:
    unreachable = bus.make_client({"REDIS_HOST": "127.0.0.1", "REDIS_PORT": "1"})
    with pytest.raises(bus.UnknownStreamError):
        bus.publish("atlas.nope", EventEnvelope(type="t", env="demo", source="test"), client=unreachable)
