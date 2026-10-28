import pytest
from atlas_shared.events import EventEnvelope
from pydantic import ValidationError


def test_envelope_round_trips_as_json() -> None:
    event = EventEnvelope(type="warroom.message", env="demo", source="agent:market_scout")
    again = EventEnvelope.model_validate_json(event.model_dump_json())
    assert again == event
    assert again.ts.tzinfo is not None


def test_envelope_rejects_unknown_env() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope(type="x", env="paper", source="test")  # type: ignore[arg-type]
