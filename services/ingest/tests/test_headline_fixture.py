from collections import Counter, defaultdict
from datetime import timedelta

import pytest
from atlas_ingest.evaluation.fixture import (
    DEFAULT_FIXTURE_PATH,
    EVENT_TYPES,
    HeadlineRow,
    load_fixture,
)


@pytest.fixture(scope="module")
def rows() -> list[HeadlineRow]:
    return load_fixture()


def test_fixture_has_exactly_300_valid_rows(rows: list[HeadlineRow]) -> None:
    raw_lines = [ln for ln in DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(raw_lines) == 300
    assert len(rows) == 300
    assert len({r.id for r in rows}) == 300


def test_seventeen_event_types_each_used_at_least_five_times(rows: list[HeadlineRow]) -> None:
    assert len(EVENT_TYPES) == 17
    counts = Counter(r.expected_event_type for r in rows)
    assert set(counts) == set(EVENT_TYPES)
    assert min(counts.values()) >= 5, counts


def test_coverage_counts(rows: list[HeadlineRow]) -> None:
    by_category = Counter(r.category for r in rows)
    assert by_category["crypto"] >= 120
    assert by_category["equity"] >= 70
    assert by_category["commodity"] >= 40
    assert by_category["macro"] >= 40
    assert sum(r.language != "en" for r in rows) >= 10
    assert any(r.language == "ar" for r in rows)
    assert sum(r.notes.startswith("hard negative") for r in rows) >= 15


def test_equity_rows_link_stock_or_etf(rows: list[HeadlineRow]) -> None:
    for r in rows:
        if r.category == "equity":
            assert {"stock", "etf", "index"} & set(r.expected_asset_classes), r.id


def test_at_least_40_multi_source_clusters_within_12_hours(rows: list[HeadlineRow]) -> None:
    clusters: dict[str, list[HeadlineRow]] = defaultdict(list)
    for r in rows:
        clusters[r.cluster_label].append(r)
    multi = {label: members for label, members in clusters.items() if len(members) >= 2}
    assert len(multi) >= 40
    for label, members in multi.items():
        assert len({m.source for m in members}) >= 2, f"{label} needs two sources"
        times = [m.published_at for m in members]
        assert max(times) - min(times) <= timedelta(hours=12), label


def test_hard_negatives_include_the_named_traps(rows: list[HeadlineRow]) -> None:
    titles = " ".join(r.title for r in rows if r.category == "negative").lower()
    for trap in ("apple harvest", "gold medal", "amazon rainforest", "meta-analysis", "ripple effect"):
        assert trap in titles, trap


def test_model_rejects_bad_rows() -> None:
    good = {
        "id": "h001",
        "source": "Reuters",
        "published_at": "2026-10-01T04:10:00Z",
        "title": "Bitcoin rises above a round number",
        "language": "en",
        "category": "crypto",
        "expected_instruments": ["BTC-USDT"],
        "expected_asset_classes": ["crypto"],
        "expected_event_type": "other",
        "cluster_label": "s-h001",
    }
    HeadlineRow.model_validate(good)
    for bad in (
        {"expected_event_type": "rumour"},
        {"published_at": "2026-10-01T07:10:00+03:00"},
        {"expected_instruments": ["BTC-USDT-SWAP"]},
        {"expected_asset_classes": ["stock"]},
        {"category": "negative"},
    ):
        with pytest.raises(ValueError):
            HeadlineRow.model_validate({**good, **bad})
