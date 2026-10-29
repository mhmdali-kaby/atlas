"""ATL-64 unit tests: embeddings and story clustering in memory, on FakeEmbedder only (no model)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from atlas_ingest.clustering import (
    ClusterParams,
    StoryClusterer,
    StoryItem,
    cluster_batch,
    cluster_rows,
    extract_numbers,
    novelty_from,
    pair_score,
)
from atlas_ingest.embedding import (
    DIM,
    FakeEmbedder,
    SentenceTransformerEmbedder,
    cosine,
    item_text,
    make_embedder,
    model_is_cached,
)
from atlas_ingest.evaluation import registry
from atlas_ingest.evaluation.fixture import load_fixture

T0 = datetime(2026, 10, 4, 3, 0, tzinfo=UTC)
EMB = FakeEmbedder()
STORY = "Cross-chain bridge drained of $48 million in exploit on Ethereum and Arbitrum"


def item(key: str, title: str, at: datetime, **kw: object) -> StoryItem:
    (vec,) = EMB.embed([title])
    return StoryItem.build(key, title, at, vec, **kw)  # type: ignore[arg-type]


# --- embeddings -----------------------------------------------------------------------------------


def test_fake_embedder_is_deterministic_normalised_and_384_d() -> None:
    a, b = FakeEmbedder().embed([STORY, STORY])
    assert len(a) == DIM == 384
    assert a == b == FakeEmbedder().embed([STORY])[0]
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0, rel_tol=1e-9)
    unrelated = EMB.embed(["Tesla third-quarter deliveries rise 9%, beating forecasts"])[0]
    assert cosine(a, unrelated) < 0.3


def test_item_text_adds_summary_only_when_present() -> None:
    assert item_text(" Gold hits record ", None) == "Gold hits record"
    assert item_text("Gold hits record", "Bullion tops $4,050.") == "Gold hits record. Bullion tops $4,050."


def test_real_embedder_loads_nothing_until_first_use(tmp_path: Path) -> None:
    emb = SentenceTransformerEmbedder(cache_dir=tmp_path / "models")
    assert emb.dim == 384
    assert emb._model is None
    assert emb.embed([]) == []
    assert emb._model is None
    assert not (tmp_path / "models").exists()


def test_auto_embedder_is_fake_without_a_cached_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_MODEL_CACHE", str(tmp_path))
    assert not model_is_cached()
    assert isinstance(make_embedder("auto"), FakeEmbedder)
    assert isinstance(make_embedder("real"), SentenceTransformerEmbedder)  # constructed, not loaded
    with pytest.raises(ValueError):
        make_embedder("gpu")


def test_model_is_cached_looks_for_a_snapshot(tmp_path: Path) -> None:
    snap = tmp_path / "models--sentence-transformers--foo" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    assert not model_is_cached("foo", tmp_path)
    (snap / "config.json").write_text("{}")
    assert model_is_cached("foo", tmp_path)


# --- figures ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("$48 million stolen", "$48M exits"),
        ("Gold above $4,050 an ounce", "الذهب فوق 4050 دولاراً"),
        ("$1.9B in positions wiped out", "Liquidaciones superan 1.900 millones de dólares"),
        ("BTC above $126K", "البيتكوين فوق 126 ألف دولار"),
        ("inflows top $1.2 billion", "biggest inflow at $1.2B"),
    ],
)
def test_same_figure_in_different_notations(a: str, b: str) -> None:
    assert extract_numbers(a) & extract_numbers(b)


def test_years_and_small_integers_are_not_figures() -> None:
    assert extract_numbers("Meta lifts 2027 outlook, Q4 and 3 analysts") == frozenset()
    assert extract_numbers("rates at 2.5% and 25 basis points") == frozenset({2.5, 25.0})


# --- the rule ---------------------------------------------------------------------------------------


def test_two_items_13_hours_apart_never_join() -> None:
    first = item("a", STORY, T0)
    later = item("b", STORY, T0 + timedelta(hours=13))
    assert cosine(first.embedding, later.embedding) == pytest.approx(1.0)
    labels = cluster_batch([first, later])
    assert labels["a"] != labels["b"]

    within = item("c", STORY, T0 + timedelta(hours=11))
    labels = cluster_batch([first, within])
    assert labels["a"] == labels["c"]


def test_a_cluster_never_spans_more_than_the_window() -> None:
    # B is 8 h after A and C is 8 h after B: C may not join, or A and C (16 h apart) would share one.
    a = item("a", STORY, T0)
    b = item("b", STORY, T0 + timedelta(hours=8))
    c = item("c", STORY, T0 + timedelta(hours=16))
    labels = cluster_batch([a, b, c])
    assert labels["a"] == labels["b"]
    assert labels["c"] != labels["a"]


def test_cluster_keeps_earliest_published_at_as_first_seen() -> None:
    clusterer = StoryClusterer()
    first = clusterer.add(item("late", STORY, T0 + timedelta(hours=2), importance=2))
    # Arrives later but was published earlier (a slow feed): first_seen moves back to it.
    second = clusterer.add(item("early", STORY + " - Reuters", T0, importance=4, instruments={"ETH-USDT"}))
    assert first.created and not second.created
    assert second.cluster_id == first.cluster_id
    c = clusterer.clusters[first.cluster_id]
    assert c.first_seen == T0
    assert c.last_seen == T0 + timedelta(hours=2)
    assert c.item_count == 2
    assert c.max_importance == 4
    assert c.instruments == {"ETH-USDT"}


def test_novelty_is_one_for_a_new_story_and_low_for_a_near_duplicate() -> None:
    clusterer = StoryClusterer()
    first = clusterer.add(item("a", STORY, T0))
    dup = clusterer.add(item("b", STORY.replace("drained", "emptied"), T0 + timedelta(minutes=20)))
    other = clusterer.add(item("c", "ECB holds rates at 2% for third meeting", T0 + timedelta(minutes=30)))
    assert first.novelty == 1.0
    assert dup.novelty < 0.2
    assert dup.cluster_id == first.cluster_id
    assert other.novelty > 0.5
    assert other.created


def test_novelty_only_looks_back_72_hours() -> None:
    clusterer = StoryClusterer()
    clusterer.add(item("a", STORY, T0))
    again = clusterer.add(item("b", STORY, T0 + timedelta(hours=73)))
    assert again.novelty == 1.0
    assert novelty_from(None) == 1.0
    assert novelty_from(1.2) == 0.0


def test_unrelated_items_stay_apart_and_conflicting_instruments_cost() -> None:
    p = ClusterParams.from_defaults()
    a = item("a", "Silver surges past $54 as London vaults run short", T0, instruments={"XAG-USDT"})
    b = item("b", "Silver surges past $54 as London vaults run short", T0, instruments={"XAU-USDT"})
    c = item("c", "Silver surges past $54 as London vaults run short", T0, instruments={"XAG-USDT"})
    assert pair_score(a, c, 1.0, p) - pair_score(a, b, 1.0, p) == pytest.approx(
        p.instrument_weight + p.conflict_penalty
    )
    labels = cluster_batch([a, item("d", "Tesla deliveries rise 9%", T0)])
    assert labels["a"] != labels["d"]


def test_params_override_and_defaults() -> None:
    p = ClusterParams.from_defaults()
    assert p.window == timedelta(hours=12)
    assert p.novelty_window == timedelta(hours=72)
    assert ClusterParams.from_defaults({"window_hours": 1}).window == timedelta(hours=1)


def test_cluster_rows_is_the_registered_stage_and_runs_on_the_fixture() -> None:
    registry.clear()
    try:
        registry.load_entry_points()
        stage: object = registry.get_clusterer()
        assert stage is cluster_rows
        rows = load_fixture()
        labels = cluster_rows(rows, embedder=FakeEmbedder())
        assert set(labels) == {r.id for r in rows}
    finally:
        registry.clear()
