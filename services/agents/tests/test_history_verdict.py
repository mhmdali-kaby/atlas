"""ATL-87: HistoryVerdict (brief §7.3) and the Historian's verdict rules enforced in code."""

from __future__ import annotations

from typing import Any

import pytest
from atlas_agents.schemas import HistoryVerdict, VerdictEvidence, enforce_verdict_rules
from pydantic import ValidationError

# The brief §7.3 HistoryVerdict fields, filled the way the Historian would.
EXAMPLE: dict[str, Any] = {
    "question": "Does history support a long XAU after a 20-day high breakout?",
    "inst_id": "XAU-USDT-SWAP",
    "bar": "1H",
    "period": "2024-10-29..2026-10-29",
    "n": 42,
    "distribution": {
        "4h": {"median": 0.0021, "p10": -0.006, "p90": 0.009, "pct_positive": 61.9, "n": 42},
        "24h": {"median": 0.004, "p10": -0.012, "p90": 0.02, "pct_positive": 59.5},
    },
    "base_rate": {"4h": {"median": 0.0002, "p10": -0.007, "p90": 0.007, "pct_positive": 51.2}},
    "regime": {"label": "trend_up", "probability": 0.83, "days_in_regime": 6.5},
    "stop_noise_check": {"stop_distance_atr": 1.2, "median_4h_range_atr": 1.9, "verdict": "inside_noise"},
    "overfit_warning": False,
    "verdict": "supports",
    "key_stat": "4h median +0.21% vs base +0.02%, 62% positive (n=42)",
    "data_quality_notes": [],
    "data_as_of": "2026-10-29T07:00:00Z",
}


def _verdict(**changes: Any) -> HistoryVerdict:
    return HistoryVerdict.model_validate({**EXAMPLE, **changes})


def test_the_brief_example_shape_is_accepted() -> None:
    v = HistoryVerdict.model_validate(EXAMPLE)
    assert v.verdict == "supports"
    assert v.distribution["4h"].pct_positive == 61.9
    assert v.stop_noise_check is not None and v.stop_noise_check.verdict == "inside_noise"
    # Optional blocks may be null or omitted.
    minimal = {
        k: EXAMPLE[k]
        for k in ("question", "inst_id", "bar", "period", "n", "verdict", "key_stat", "data_as_of")
    }
    assert HistoryVerdict.model_validate(minimal).regime is None


@pytest.mark.parametrize("missing", ["verdict", "data_as_of"])
def test_a_payload_missing_verdict_or_data_as_of_is_rejected(missing: str) -> None:
    payload = {k: v for k, v in EXAMPLE.items() if k != missing}
    with pytest.raises(ValidationError, match=missing):
        HistoryVerdict.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [("verdict", "strongly_supports"), ("data_as_of", "2026-10-29T07:00:00"), ("unknown", 1), ("n", -1)],
)
def test_invalid_values_are_rejected(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        _verdict(**{field: value})


def test_small_sample_caps_supports_at_weakly_supports_and_records_it() -> None:
    original = _verdict(n=12)
    fixed, corrections = enforce_verdict_rules(original)
    assert fixed.verdict == "weakly_supports"
    assert [(c.rule, c.from_verdict, c.to_verdict) for c in corrections] == [
        ("small_sample", "supports", "weakly_supports")
    ]
    assert "n=12" in corrections[0].reason
    assert original.verdict == "supports"  # input untouched


@pytest.mark.parametrize("verdict", ["weakly_supports", "does_not_support"])
def test_small_sample_leaves_weaker_verdicts_alone(verdict: str) -> None:
    fixed, corrections = enforce_verdict_rules(_verdict(n=5, verdict=verdict))
    assert fixed.verdict == verdict
    assert corrections == []


def test_small_sample_caps_contradicts_at_does_not_support_and_records_it() -> None:
    """Brief §7.3's n<20 cap applies against an idea too: a 5-case "contradicts" cannot carry the
    Strategist's veto (§7.6 item 4), so it is softened the same way "supports" is."""
    original = _verdict(n=5, verdict="contradicts")
    fixed, corrections = enforce_verdict_rules(original)
    assert fixed.verdict == "does_not_support"
    assert [(c.rule, c.from_verdict, c.to_verdict) for c in corrections] == [
        ("small_sample", "contradicts", "does_not_support")
    ]
    assert "n=5" in corrections[0].reason
    assert original.verdict == "contradicts"  # input untouched


def test_small_sample_cap_on_contradicts_respects_the_n_floor() -> None:
    fixed, corrections = enforce_verdict_rules(_verdict(n=20, verdict="contradicts"))
    assert fixed.verdict == "contradicts"
    assert corrections == []


def test_evidence_n_overrides_the_verdicts_own_n() -> None:
    fixed, corrections = enforce_verdict_rules(_verdict(n=40), VerdictEvidence(n=19))
    assert fixed.verdict == "weakly_supports"
    assert len(corrections) == 1
    untouched, none = enforce_verdict_rules(_verdict(n=20))
    assert untouched.verdict == "supports" and none == []


def test_backtest_claim_on_gapped_data_is_forced_to_does_not_support() -> None:
    ev = VerdictEvidence(backtest_based=True, unrepaired_gap=True, gap_note="XAU 1H: 6 bars missing")
    fixed, corrections = enforce_verdict_rules(_verdict(), ev)
    assert fixed.verdict == "does_not_support"
    assert [c.rule for c in corrections] == ["backtest_on_gapped_data"]
    assert any("unrepaired gap" in n and "6 bars missing" in n for n in fixed.data_quality_notes)


def test_both_rules_apply_in_order() -> None:
    ev = VerdictEvidence(n=8, backtest_based=True, unrepaired_gap=True)
    fixed, corrections = enforce_verdict_rules(_verdict(), ev)
    assert fixed.verdict == "does_not_support"
    assert [(c.from_verdict, c.to_verdict) for c in corrections] == [
        ("supports", "weakly_supports"),
        ("weakly_supports", "does_not_support"),
    ]


def test_contradicts_on_gapped_backtest_is_also_does_not_support() -> None:
    ev = VerdictEvidence(backtest_based=True, unrepaired_gap=True)
    fixed, _ = enforce_verdict_rules(_verdict(verdict="contradicts"), ev)
    assert fixed.verdict == "does_not_support"


@pytest.mark.parametrize(("backtest", "gap"), [(True, False), (False, True)])
def test_gap_rule_needs_both_a_backtest_and_a_gap(backtest: bool, gap: bool) -> None:
    fixed, corrections = enforce_verdict_rules(
        _verdict(), VerdictEvidence(backtest_based=backtest, unrepaired_gap=gap)
    )
    assert fixed.verdict == "supports"
    assert corrections == []
