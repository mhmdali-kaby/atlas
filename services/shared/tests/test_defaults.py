from typing import Any

from atlas_shared.defaults import DEFAULTS


def _walk(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [leaf for v in value.values() for leaf in _walk(v)]
    if isinstance(value, list):
        return [leaf for v in value for leaf in _walk(v)]
    return [value]


def test_balanced_profile_matches_php_config() -> None:
    assert DEFAULTS["risk_profiles"]["balanced"]["max_loss_per_trade_pct"] == "1.0"
    assert DEFAULTS["risk_profiles"]["conservative"]["min_expected_r"] == "2.0"
    assert DEFAULTS["autonomy"]["default_level"] == 2
    assert DEFAULTS["env"]["default"] == "demo"


def test_no_floats_in_generated_defaults() -> None:
    assert not [leaf for leaf in _walk(DEFAULTS) if isinstance(leaf, float)]
