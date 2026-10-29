"""ATL-63: rule-based event type, sentiment and importance (brief §7.2 step 4, decision #243)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from atlas_ingest import classify
from atlas_ingest.classify import (
    classify_headline,
    event_rules,
    event_type,
    headline_sentiment,
    importance,
    sentiment,
)
from atlas_ingest.evaluation import registry
from atlas_ingest.evaluation.__main__ import TARGET_EVENT_ACCURACY, report_lines
from atlas_ingest.evaluation.fixture import EVENT_TYPES, gold_event_types, load_fixture
from atlas_ingest.evaluation.scoring import score_event_type
from hypothesis import given
from hypothesis import strategies as st

# ------------------------------------------------------------------------------- event type


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Exchange X hacked for $40M", "hack_exploit"),
        ("Fed holds rates", "central_bank"),
        ("CPI rises 0.4%", "macro_release"),
        ("Binance to delist ABC", "delisting"),
    ],
)
def test_acceptance_examples(title: str, expected: str) -> None:
    assert event_type(title, "") == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Unseen headlines (not in the fixture), one per common type, to catch rules that only
        # memorise the fixture.
        ("DeFi protocol loses $12 million in reentrancy exploit", "hack_exploit"),
        ("Aptos to unlock 11 million tokens next week", "token_unlock"),
        ("Bank of England cuts rates to 3.75%", "central_bank"),
        ("US unemployment rate rises to 4.5% in November", "macro_release"),
        ("Netflix fourth-quarter profit beats estimates", "earnings"),
        ("Salesforce guidance disappoints as AI spending slows", "guidance"),
        ("Ether ETFs record $500 million of inflows", "ETF_flow"),
        ("Kraken to list SUI perpetual futures", "listing"),
        ("Cisco to acquire observability firm for $3 billion", "M&A"),
        ("SEC charges crypto lender with selling unregistered securities", "regulation"),
        ("Shareholders sue Tesla over pay package", "lawsuit"),
        ("Solana partners with payments giant on merchant checkout", "partnership"),
        ("Arbitrum launches new developer SDK", "product"),
        ("EU slaps tariffs on Chinese electric vehicles", "geopolitics"),
        ("OPEC+ agrees deeper output cuts", "supply_shock"),
        ("Bitcoin climbs to two-week high", "other"),
    ],
)
def test_unseen_headlines(title: str, expected: str) -> None:
    assert event_type(title, "") == expected


def test_title_decides_before_summary() -> None:
    # The title matches earnings ("annual forecast"); the summary's "outlook" must not override it.
    assert event_type("Palantir raises annual forecast", "lifted its revenue outlook") == "earnings"
    # A title with no rule falls back to the summary, then to `other`.
    assert event_type("Markets this morning", "Bitcoin ETFs saw inflows") == "ETF_flow"
    assert event_type("Markets this morning", "") == "other"


def test_every_rule_type_is_a_brief_type() -> None:
    assert {r.type for r in event_rules()} <= set(EVENT_TYPES)


def test_fixture_event_type_accuracy_meets_target() -> None:
    rows = load_fixture()
    score = score_event_type({r.id: classify_headline(r) for r in rows}, gold_event_types(rows))
    assert score.accuracy >= TARGET_EVENT_ACCURACY, score.confusion


def test_classifier_is_registered_with_the_report() -> None:
    registry.clear()
    try:
        registry.load_entry_points()
        assert registry.get_classifier() is classify_headline
        lines = report_lines(load_fixture())
        event_line = next(line for line in lines if line.startswith("event_type:"))
        assert "PASS" in event_line
    finally:
        registry.clear()


# -------------------------------------------------------------------------------- sentiment


def test_sentiment_direction_and_negation() -> None:
    assert headline_sentiment("Bitcoin surges") > 0
    assert headline_sentiment("Bitcoin plunges") < 0
    assert headline_sentiment("Bitcoin does not fall") > 0
    assert headline_sentiment("Bitcoin falls") < 0
    assert headline_sentiment("Bitcoin is unchanged") == 0


def test_intensifier_strengthens() -> None:
    assert headline_sentiment("Gold rises sharply") > headline_sentiment("Gold rises")


def test_sentiment_maps_every_instrument_to_a_three_place_decimal() -> None:
    out = sentiment("Solana surges", "", ["SOL-USDT", "SOL-USDT", "BTC-USDT"])
    assert list(out) == ["SOL-USDT", "BTC-USDT"]
    for value in out.values():
        assert isinstance(value, Decimal)
        assert value > 0
        assert value == value.quantize(Decimal("0.001"))
    assert sentiment("Solana surges", "", []) == {}


@given(st.text(max_size=300), st.text(max_size=300))
def test_sentiment_always_within_bounds(title: str, summary: str) -> None:
    for value in sentiment(title, summary, ["BTC-USDT"]).values():
        assert Decimal(-1) <= value <= Decimal(1)


def test_sentiment_bounds_on_extreme_text() -> None:
    assert headline_sentiment(" ".join(["skyrockets"] * 500)) <= 1
    assert headline_sentiment(" ".join(["massive crash"] * 500)) >= -1


# ------------------------------------------------------------------------------- importance


_numbers = st.one_of(
    st.decimals(allow_nan=True, allow_infinity=True),
    st.floats(allow_nan=True, allow_infinity=True),
    st.integers(min_value=-10, max_value=10),
)


@given(
    etype=st.one_of(st.sampled_from(EVENT_TYPES), st.text(max_size=20)),
    source=st.one_of(
        st.sampled_from(["Reuters", "bloomberg", "okx.announcements", "CoinDesk"]), st.text(max_size=30)
    ),
    linked=st.integers(min_value=-5, max_value=1000),
    sent=_numbers,
    novelty=st.one_of(st.none(), _numbers),
)
def test_importance_is_always_one_to_five(
    etype: str, source: str, linked: int, sent: Decimal | float | int, novelty: Decimal | float | int | None
) -> None:
    score = importance(etype, source, linked, sent, novelty)
    assert isinstance(score, int)
    assert 1 <= score <= 5


@pytest.mark.parametrize("source", ["Reuters", "BLOOMBERG", " okx.announcements "])
def test_tier1_hack_scores_five(source: str) -> None:
    assert importance("hack_exploit", source, 1, Decimal("0.2"), None) == 5


def test_importance_formula_pieces() -> None:
    assert importance("hack_exploit", "SomeBlog", 1, Decimal(0), None) == 4
    assert importance("other", "SomeBlog", 0, Decimal(0), None) == 1
    # A low-impact type is lifted by strong sentiment and many linked instruments.
    assert importance("product", "SomeBlog", 1, Decimal(0), None) == 2
    assert importance("product", "SomeBlog", 3, Decimal("0.8"), None) == 4
    # Novelty: a rehash loses a point, a genuinely new story gains one.
    assert importance("earnings", "SomeBlog", 1, Decimal(0), Decimal("0.05")) == 2
    assert importance("earnings", "SomeBlog", 1, Decimal(0), Decimal("0.9")) == 4
    # Market-wide types are not penalised for linking nothing.
    assert importance("central_bank", "SomeBlog", 0, Decimal(0), None) == 4
    assert importance("lawsuit", "SomeBlog", 0, Decimal(0), None) == 2


def test_config_dir_override(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path))
    assert classify.config_dir() == tmp_path


def test_curly_apostrophe_negation() -> None:
    assert headline_sentiment("Bitcoin doesn’t fall") > 0
