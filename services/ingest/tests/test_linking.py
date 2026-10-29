"""ATL-62 entity linking: fixture targets, case rules, hard negatives, Arabic, env filtering."""

from __future__ import annotations

import pytest
from atlas_ingest.aliases import InstrumentInfo, build_alias_rows, make_linker, universe
from atlas_ingest.evaluation.fixture import gold_linking, load_fixture
from atlas_ingest.evaluation.linking_stage import link_headline, reference_linker
from atlas_ingest.evaluation.scoring import score_linking
from atlas_ingest.linking import (
    ASSET_CLASS,
    INSTRUMENT,
    AliasRow,
    Linker,
    alias_mode,
    link,
    load_curated,
    normalize_alias,
    set_default_linker,
)

TARGET_PRECISION = 0.95
TARGET_RECALL = 0.85


def links(title: str, summary: str = "") -> tuple[str, ...]:
    return reference_linker().link(title, summary).instruments


def test_fixture_precision_and_recall_meet_targets() -> None:
    rows = load_fixture()
    score = score_linking({r.id: link_headline(r) for r in rows}, gold_linking(rows))
    assert score.precision >= TARGET_PRECISION, score
    assert score.recall >= TARGET_RECALL, score


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Tesla shares jump", ("TSLA-USDT",)),
        ("gold medal for Kenya", ()),
        ("$OP rallies", ("OP-USDT",)),
        ("OP-ED: markets", ()),
        ("الذهب يرتفع", ("XAU-USDT",)),
    ],
)
def test_acceptance_examples(title: str, expected: tuple[str, ...]) -> None:
    assert links(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("SOL breaks out", ("SOL-USDT",)),
        ("sol y playa en España", ()),
        ("$sol rallies", ("SOL-USDT",)),
        ("Bitcoin Cash upgrade ships", ("BCH-USDT",)),  # longest match beats "Bitcoin"
        ("Ethereum Classic delisted", ("ETC-USDT",)),
        ("OKX will launch WLD-USDC perpetual swap", ("WLD-USDT",)),
        ("SUI slides", ("SUI-USDT",)),
        ("APPLE BEATS ON REVENUE", ("AAPL-USDT",)),
        ("بالذهب والفضة", ("XAG-USDT", "XAU-USDT")),
        ("Минфин предложил расчёты в биткоине", ("BTC-USDT",)),
        ("中国人民银行增持黄金", ("XAU-USDT",)),
        ("Apple harvest forecast falls", ()),
        ("Amazon rainforest loses trees", ()),
        ("Avalanche kills two skiers", ()),
        ("Avalanche rolls out fee cut", ("AVAX-USDT",)),
        ("Strategy shift at the ministry", ()),
        ("Tons of grain shipped", ()),
        ("Tether reports reserves; USDT supply grows", ()),
    ],
)
def test_case_context_and_script_rules(title: str, expected: tuple[str, ...]) -> None:
    assert links(title) == expected


def test_asset_class_aliases_link_classes_not_instruments() -> None:
    result = reference_linker().link("Fed cuts rates by a quarter point")
    assert result.instruments == ()
    assert "index" in result.asset_classes
    assert {e.target_kind for e in result.entities} == {ASSET_CLASS}


def test_result_carries_classes_and_entities_with_offsets() -> None:
    result = reference_linker().link("Oil and gold rise", "Spot gold rose 1.2%.")
    assert result.instruments == ("CL-USDT", "XAU-USDT")
    assert result.asset_classes == ("commodity",)
    title_gold = next(e for e in result.entities if e.field == "title" and e.target == "XAU-USDT")
    assert "Oil and gold rise"[title_gold.start : title_gold.end] == "gold"
    assert any(e.field == "summary" for e in result.entities)


def test_alias_missing_from_env_links_nothing() -> None:
    curated = load_curated()
    # An env that lists bitcoin but not gold: the curated gold aliases are dropped.
    env = [InstrumentInfo("BTC-USDT-SWAP", "SWAP", "BTC-USDT", None, None, "crypto")]
    rows = build_alias_rows(env, curated)
    assert all(r.target != "XAU-USDT" for r in rows)
    linker = make_linker(rows, env, curated)
    assert linker.link("Gold and bitcoin rise").instruments == ("BTC-USDT",)
    # Even a stale XAU row (left by another env's sync) links nothing when XAU is not listed.
    stale = Linker([*rows, AliasRow("gold", INSTRUMENT, "XAU-USDT", 10)], {"BTC-USDT": "crypto"})
    assert stale.link("Gold rises").instruments == ()


def test_canonical_family_prefers_usdt_then_usdc() -> None:
    insts = [
        InstrumentInfo("AAPL-USDC-SWAP", "SWAP", "AAPL-USDC", None, None, "stock"),
        InstrumentInfo("BTC-USD-SWAP", "SWAP", "BTC-USD", None, None, "crypto"),
        InstrumentInfo("BTC-USDT-SWAP", "SWAP", "BTC-USDT", None, None, "crypto"),
        InstrumentInfo("ETH-USDT", "SPOT", None, "ETH", "USDT", "other"),
        InstrumentInfo("ETH-USDT-SWAP", "SWAP", "ETH-USDT", None, None, "crypto"),
    ]
    assert universe(insts) == {
        "AAPL": ("AAPL-USDC", "stock"),
        "BTC": ("BTC-USDT", "crypto"),
        "ETH": ("ETH-USDT", "crypto"),
    }


def test_short_and_stoplisted_tickers_are_cashtag_only() -> None:
    curated = load_curated()
    env = [
        InstrumentInfo("AI-USDT-SWAP", "SWAP", "AI-USDT", None, None, "crypto"),
        InstrumentInfo("ZK-USDT-SWAP", "SWAP", "ZK-USDT", None, None, "crypto"),
    ]
    rows = build_alias_rows(env, curated)
    assert {r.alias for r in rows if r.target_kind == INSTRUMENT and "-" not in r.alias} == {"$AI", "$ZK"}
    linker = make_linker(rows, env, curated)
    assert linker.link("AI stocks rally; ZK proofs explained").instruments == ()
    assert linker.link("$AI and $zk pump").instruments == ("AI-USDT", "ZK-USDT")


def test_alias_rows_are_unique_on_table_key() -> None:
    from atlas_ingest.evaluation.linking_stage import reference_instruments

    rows = build_alias_rows(reference_instruments(), load_curated())
    keys = [(r.alias_normalized, r.target) for r in rows]
    assert len(keys) == len(set(keys))


def test_alias_mode_rules() -> None:
    assert alias_mode("bitcoin") == ("word", "bitcoin")
    assert alias_mode("Apple") == ("exact", "Apple")
    assert alias_mode("$ONE") == ("cashtag", "ONE")
    assert alias_mode("биткоин*") == ("prefix", "биткоин")
    assert alias_mode("比特币") == ("substring", "比特币")
    assert normalize_alias("  إيثريوم ") == "ايثريوم"


def test_module_link_uses_installed_default() -> None:
    set_default_linker(reference_linker())
    try:
        assert link("Tesla shares jump").instruments == ("TSLA-USDT",)
    finally:
        set_default_linker(None)
