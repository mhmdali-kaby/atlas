"""ATL-104 AC1: Market Scout's universe filter (brief §7.1 step 1) on a golden fixture."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from atlas_agents.scout.universe import Candidate, config_from, filter_universe, market_of

AS_OF = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
OLD = AS_OF - timedelta(days=90)


def _c(
    inst_id: str,
    *,
    inst_type: str = "SWAP",
    asset_class: str = "crypto",
    state: str = "live",
    list_time: datetime | None = OLD,
    quote_ccy: str | None = None,
    volume: str | None = "50000000",
    spread: str | None = "2",
) -> Candidate:
    return Candidate(
        inst_id=inst_id,
        inst_type=inst_type,
        asset_class=asset_class,
        state=state,
        list_time=list_time,
        quote_ccy=quote_ccy,
        settle_ccy=inst_id.split("-")[1] if inst_type == "SWAP" else None,
        volume_24h_quote=None if volume is None else Decimal(volume),
        spread_bps=None if spread is None else Decimal(spread),
    )


# Six instruments: three pass every check, three fail with the reasons listed beside them.
FIXTURE = [
    _c("BTC-USDT-SWAP", volume="900000000"),
    # USDC-margined stock perp: 6M USDC counts as 6M USD, above the 5M stock-perp minimum.
    _c("AAPL-USDC-SWAP", asset_class="stock", volume="6000000"),
    _c("ETH-USDT", inst_type="SPOT", quote_ccy="USDT", volume="12000000", spread="7.5"),
    _c("DOGE-USDT-SWAP", volume="19999999.99"),  # volume (crypto perp minimum 20M)
    _c("XAU-USDT-SWAP", asset_class="commodity", state="suspend", spread="9"),  # state + spread
    _c("NEW-USDT-SWAP", list_time=AS_OF - timedelta(days=3)),  # age + blocklist
]
EXPECTED_REASONS = {
    "DOGE-USDT-SWAP": ["volume"],
    "XAU-USDT-SWAP": ["state", "spread"],
    "NEW-USDT-SWAP": ["blocklist", "age"],
}


def test_golden_fixture_keeps_three_and_states_each_rejection_reason() -> None:
    config = config_from({"blocklist": ["new-usdt-swap"]})
    result = filter_universe(FIXTURE, config, AS_OF)

    assert [k.inst_id for k in result.kept] == ["BTC-USDT-SWAP", "ETH-USDT", "AAPL-USDC-SWAP"]
    assert {r.inst_id: r.reasons for r in result.rejected} == EXPECTED_REASONS
    for r in result.rejected:
        assert len(r.detail) == len(r.reasons)
    aapl = next(k for k in result.kept if k.inst_id == "AAPL-USDC-SWAP")
    assert aapl.market == "stock_perp"
    assert aapl.volume_24h_usd == Decimal("6000000")
    assert "USDC" in result.volume_basis


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (_c("A-USDT-SWAP", volume="1000"), "volume"),
        (_c("A-USDT-SWAP", spread="8.01"), "spread"),
        (_c("A-USDT-SWAP", state="preopen"), "state"),
        (_c("A-USDT-SWAP", list_time=AS_OF - timedelta(days=7)), "age"),
        (_c("BLOCKED-USDT-SWAP"), "blocklist"),
        (_c("A-USDT-260327", inst_type="FUTURES"), "market"),
        (_c("ETH-BTC", inst_type="SPOT", quote_ccy="BTC"), "volume_unknown"),
        (_c("A-USDT-SWAP", volume=None), "volume"),
    ],
)
def test_each_check_rejects_with_its_own_reason(candidate: Candidate, reason: str) -> None:
    config = config_from({"blocklist": ["BLOCKED-USDT-SWAP"]})
    result = filter_universe([candidate], config, AS_OF)
    assert result.kept == []
    assert result.rejected[0].reasons == [reason]


def test_unknown_spread_and_listing_time_keep_with_a_note() -> None:
    result = filter_universe([_c("SOL-USDT-SWAP", spread=None, list_time=None)], config_from(None), AS_OF)
    assert result.kept[0].notes == ["age_unknown", "spread_unknown"]


def test_settings_values_override_the_defaults() -> None:
    config = config_from(
        {"min_24h_volume_usd_crypto_perp": "1000", "max_spread_bps": "20", "enabled_markets": ["spot"]},
        version=7,
    )
    assert config.min_volume_usd["crypto_perp"] == Decimal("1000")
    assert config.min_volume_usd["spot"] == Decimal("10000000")  # default kept
    assert config.max_spread_bps == Decimal("20")
    assert config.enabled_markets == frozenset({"spot"})
    result = filter_universe([_c("BTC-USDT-SWAP")], config, AS_OF)
    assert result.rejected[0].reasons == ["market"]
    assert result.settings_version == 7


def test_whitelist_keeps_only_listed_instruments_that_pass_every_other_check() -> None:
    """Decision #250 (ATL-156 AC4): the whitelist restricts; volume/spread/state still apply."""
    candidates = [
        _c("BTC-USDT-SWAP", volume="900000000"),
        _c("XAU-USDT-SWAP", asset_class="commodity", volume="6000000"),
        _c("ETH-USDT-SWAP", volume="900000000"),  # passes everything but is not listed
    ]
    config = config_from({"whitelist": ["BTC-USDT-SWAP", "xau-usdt-swap"]})
    result = filter_universe(candidates, config, AS_OF)
    assert [k.inst_id for k in result.kept] == ["BTC-USDT-SWAP", "XAU-USDT-SWAP"]
    assert {r.inst_id: r.reasons for r in result.rejected} == {"ETH-USDT-SWAP": ["whitelist"]}

    # Whitelisted but failing another filter: still rejected, for that filter only.
    failing = [
        _c("BTC-USDT-SWAP", volume="1000"),
        _c("XAU-USDT-SWAP", asset_class="commodity", state="suspend", spread="9"),
    ]
    result = filter_universe(failing, config, AS_OF)
    assert result.kept == []
    assert {r.inst_id: r.reasons for r in result.rejected} == {
        "BTC-USDT-SWAP": ["volume"],
        "XAU-USDT-SWAP": ["state", "spread"],
    }

    # The blocklist still wins over the whitelist; an empty whitelist restricts nothing.
    both = config_from({"whitelist": ["BTC-USDT-SWAP"], "blocklist": ["BTC-USDT-SWAP"]})
    assert filter_universe(candidates[:1], both, AS_OF).rejected[0].reasons == ["blocklist"]
    empty = filter_universe(candidates, config_from({"whitelist": []}), AS_OF)
    assert len(empty.kept) == 3


def test_saved_empty_enabled_markets_admits_nothing() -> None:
    """Every asset-class toggle off ([] saved) is not "use the defaults"."""
    config = config_from({"enabled_markets": []})
    assert config.enabled_markets == frozenset()
    result = filter_universe(FIXTURE, config, AS_OF)
    assert result.kept == []
    assert all("market" in r.reasons for r in result.rejected)
    # A settings row without the key still falls back to the default toggles.
    defaults = frozenset({"spot", "crypto_perp", "stock_perp", "commodity_perp"})
    assert config_from({}).enabled_markets == defaults


def test_market_mapping() -> None:
    assert market_of("SWAP", "crypto") == "crypto_perp"
    assert market_of("SWAP", "etf") == "stock_perp"
    assert market_of("SWAP", "index") == "stock_perp"
    assert market_of("SWAP", "commodity") == "commodity_perp"
    assert market_of("SWAP", "other") == "other"
    assert market_of("SPOT", "crypto") == "spot"
    assert market_of("OPTION", "crypto") == "options"
