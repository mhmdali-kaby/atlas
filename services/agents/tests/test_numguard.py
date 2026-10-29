"""Numeric hallucination guard (brief §7.11, ATL-91): golden Historian answers, ignores, cards and
the configured tolerances."""

from __future__ import annotations

from typing import Any

import pytest
from atlas_agents.numguard import check, extract_numbers, load_config
from atlas_shared.defaults import DEFAULTS


def envelope(data: dict[str, Any], source: str = "atlas.candles") -> dict[str, Any]:
    """A tool result as the model sees it (ATL-84's {as_of, source, truncated, data} envelope)."""
    return {"as_of": "2026-10-29T07:00:00+00:00", "source": source, "truncated": False, "data": data}


# Realistic outputs of one Historian turn on XAU-USDT-SWAP (shapes from the ATL-87 history tools).
HISTORIAN_TOOLS: list[dict[str, Any]] = [
    envelope(
        {
            "sample": {
                "inst_id": "XAU-USDT-SWAP",
                "bar": "1D",
                "n": 365,
                "n_meaning": "daily bars",
                "period": {"start": "2025-10-29T00:00:00+00:00", "end": "2026-10-29T00:00:00+00:00"},
                "data_quality": {"gaps": 0},
            },
            "status": "ok",
            "result": {"atr": 41.83, "realised_vol_annual": 0.1874, "max_drawdown": -0.0912, "skew": -0.42},
        }
    ),
    envelope(
        {
            "sample": {"inst_id": "XAU-USDT-SWAP", "bar": "1H", "n": 20, "n_meaning": "analogs"},
            "status": "ok",
            "result": {
                "horizons": {"24h": {"median": 0.0041, "p10": -0.0123, "p90": 0.0198, "pct_positive": 0.6}}
            },
        }
    ),
    envelope(
        {
            "status": "ok",
            "result": {"label": "trend", "probability": 0.8264, "days_in_regime": 12},
        }
    ),
]

# Ten numbers: 365, 83% (0.8264 as a rounded %), 12, 20, +0.41%, -1.23%, +1.98%, 60% (0.6 as a %),
# 41.8 (41.83 rounded) and one invented: -14.6%.
HISTORIAN_ANSWER = """**XAU-USDT-SWAP, 1D, 2025-10-29 to 2026-10-29 (n = 365 bars).**
1. Regime: trend, probability 83%, 12 days in regime (as_of 2026-10-29T07:00:00Z).
2. Analogs (n = 20): 24h median +0.41%, p10 \u22121.23%, p90 +1.98%, 60% positive.
3. ATR(14) is 41.8 and the worst drawdown since 2024 was \u221214.6%.
History weakly supports the long: n = 20 is the minimum."""


def test_historian_answer_flags_exactly_the_invented_number() -> None:
    result = check(HISTORIAN_ANSWER, None, HISTORIAN_TOOLS)

    assert result.checked == 11  # the ten above plus the closing "n = 20"
    assert [n.raw for n in result.unmatched] == ["\u221214.6%"]
    invented = result.unmatched[0]
    assert invented.value == pytest.approx(-14.6)
    assert invented.span is not None
    assert HISTORIAN_ANSWER[invented.span[0] : invented.span[1]].strip() == "\u221214.6%"

    how = {n.raw: n.matched_by for n in result.matched}
    assert how["83%"] == "percent_of_ratio"  # 0.8264 x 100 = 82.64, rounded as written
    assert how["60%"] == "percent_of_ratio"
    assert how["41.8"] == "tool_value"  # 41.83 rounded
    assert how["\u22121.23%"] == "percent_of_ratio"  # sign kept: -1.23 against -0.0123 x 100
    assert how["365"] == "tool_value"


def test_ten_numbers_nine_present_one_invented() -> None:
    body = HISTORIAN_ANSWER.rsplit("\n", 1)[0]  # drop the closing line: exactly ten numbers
    result = check(body, None, HISTORIAN_TOOLS)
    assert result.checked == 10
    assert len(result.matched) == 9
    assert [n.raw for n in result.unmatched] == ["\u221214.6%"]


def test_dates_times_years_list_markers_and_instrument_ids_are_not_numbers() -> None:
    message = (
        "As of 2026-10-29T07:30:00Z (29 Oct 2026, 10:30 Baghdad; 2026-10-29 07:30:00.123+00:00):\n"
        "1. BTC-USDT-SWAP and ETH-USD-261225 since 2024, on 1H and 4H bars, ATR(14), MACD(12,26,9).\n"
        "2) XAU-USDT-SWAP before 15:30 UTC on Oct 30, 10/29/2026, 7:30 pm, December 2025.\n"
        "- 3. BTC-USD-251226-60000-C over the next 24h and 7d; GARCH(1,1) on 15m bars.\n"
    )
    assert extract_numbers(message) == []
    result = check(message, None, [])
    assert result.checked == 0
    assert result.to_meta() == {"unmatched_numbers": [], "checked": 0}


@pytest.mark.parametrize(
    ("text", "value", "kind"),
    [
        ("close at 4,062.5", 4062.5, "number"),
        ("spread 3.5 bps", 3.5, "bps"),
        ("expected 2.4R", 2.4, "number"),
        ("stop 1.3x ATR away", 1.3, "number"),
        ("about 1.4 ATR", 1.4, "number"),
        ("volume $1.2M", 1_200_000, "number"),
        ("OI 850k", 850_000, "number"),
        ("cap 3B", 3_000_000_000, "number"),
        ("funding -0.012%", -0.012, "percent"),
        ("down \u22122.5%", -2.5, "percent"),
        ("down 7 %", 7, "percent"),
        ("2.5bp", 2.5, "bps"),
    ],
)
def test_number_forms(text: str, value: float, kind: str) -> None:
    [number] = extract_numbers(text)
    assert number.value == pytest.approx(value)
    assert number.kind == kind


def test_percent_bps_difference_and_ratio_are_derivable() -> None:
    tools = [
        envelope({"close": 4062.5, "stop": 4010.0, "basis": 0.00035, "ret": -0.023}),
        envelope({"atr": 41.83, "stop_distance": 52.5}, source="atlas.history"),
    ]
    text = "Stop is 52.5 below the close (4,062.5 - 4,010), 1.26x ATR; basis 3.5 bps; it fell 2.3%."
    result = check(text, None, tools)
    assert result.unmatched == ()
    how = {n.raw: n.matched_by for n in result.matched}
    assert how["52.5"] == "tool_value"
    assert how["1.26x ATR"] == "ratio"  # 52.5 / 41.83 = 1.255, rounded as written
    assert how["3.5 bps"] == "bps_of_ratio"
    assert how["2.3%"] == "percent_of_ratio"  # positive stated number matches by magnitude


def test_a_negative_number_must_match_the_sign() -> None:
    tools = [envelope({"ret": 0.023})]
    assert check("it fell \u22122.3%", None, tools).unmatched[0].raw == "\u22122.3%"
    assert check("it rose 2.3%", None, tools).ok


def test_numbers_in_tool_strings_and_decimal_strings_count() -> None:
    tools = [
        envelope({"fee": "0.0005", "headline": "US CPI rose 3.2% in September"}, source="atlas.news"),
    ]
    assert check("CPI printed 3.2%; taker fee 0.05%", None, tools).ok


def test_card_numbers_are_checked_and_to_meta_reports_them() -> None:
    card = {
        "verdict": "weakly_supports",
        "key_stat": "24h median +0.41%",
        "n": 20,
        "distribution": {"24h": {"median": 0.0041, "p90": 0.0731}},
        "regime": {"label": "trend", "probability": 0.83},
    }
    result = check("History weakly supports it.", card, HISTORIAN_TOOLS)

    assert result.checked == 5  # 0.41%, 20, 0.0041, 0.0731, 0.83
    assert [(n.location, n.path, n.raw) for n in result.unmatched] == [
        ("card", "distribution.24h.p90", "0.0731")
    ]
    meta = result.to_meta()
    assert meta["checked"] == 5
    assert meta["unmatched_numbers"] == [
        {
            "raw": "0.0731",
            "value": 0.0731,
            "kind": "number",
            "location": "card",
            "span": None,
            "path": "distribution.24h.p90",
        }
    ]
    key_stat = next(n for n in result.matched if n.path == "key_stat")
    assert key_stat.raw == "+0.41%"
    assert key_stat.span is not None


def test_text_numbers_carry_raw_text_and_span_in_meta() -> None:
    result = check("ATR is 99.9", None, [envelope({"atr": 41.83})])
    [item] = result.to_meta()["unmatched_numbers"]
    assert item == {
        "raw": "99.9",
        "value": 99.9,
        "kind": "number",
        "location": "text",
        "span": [7, 11],
        "path": None,
    }


def test_defaults_come_from_config() -> None:
    config = load_config()
    raw = DEFAULTS["agents"]["numguard"]
    assert config.rel_tolerance == float(raw["rel_tolerance"]) == 0.005
    assert config.abs_floor == float(raw["abs_floor"])
    assert config.rounding_aware is raw["rounding_aware"] is True


def test_relative_tolerance_flips_a_borderline_number(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = [envelope({"close": 100.0})]
    assert not check("close 100.6", None, tools).ok  # 0.6 % away, default 0.5 %

    monkeypatch.setitem(DEFAULTS["agents"]["numguard"], "rel_tolerance", "0.01")
    assert check("close 100.6", None, tools).ok

    monkeypatch.setitem(DEFAULTS["agents"]["numguard"], "rel_tolerance", "0.001")
    assert not check("close 100.4", None, tools).ok  # 0.4 % passes at 0.5 %, not at 0.1 %


def test_absolute_floor_flips_a_small_number(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = [envelope({"funding": 0.0012})]
    assert not check("funding 0.0015", None, tools).ok
    monkeypatch.setitem(DEFAULTS["agents"]["numguard"], "abs_floor", "0.0005")
    assert check("funding 0.0015", None, tools).ok


def test_rounding_awareness_is_configurable() -> None:
    tools = [envelope({"ret": 0.04132})]
    assert check("up 4.1%", None, tools).ok
    strict = {**DEFAULTS["agents"]["numguard"], "rounding_aware": False}
    assert not check("up 4.1%", None, tools, config=strict).ok  # 0.8 % off 4.132


def test_difference_of_two_tool_numbers() -> None:
    tools = [envelope({"close": 4062.5, "stop": 4010.0})]
    [number] = check("risk 52.5", None, tools).matched
    assert number.matched_by == "difference"


def test_two_step_derivations_do_not_match() -> None:
    tools = [envelope({"close": 4062.5, "stop": 4010.0, "atr": 41.83})]
    # (4062.5 - 4010) / 41.83 = 1.255 needs a difference then a ratio: not one step.
    assert not check("stop is 1.26x ATR", None, tools).ok


def test_pair_derivation_can_be_switched_off() -> None:
    tools = [envelope({"close": 4062.5, "stop": 4010.0})]
    assert check("risk 52.5", None, tools).ok
    no_pairs = {**DEFAULTS["agents"]["numguard"], "derive_pairs": False}
    assert not check("risk 52.5", None, tools, config=no_pairs).ok
