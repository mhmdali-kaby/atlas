"""The per-checkout test database name rule (ATL-29); must match backend/tests/Support/TestDatabase.php."""

import hashlib
import os
from pathlib import Path

import pytest
from atlas_shared import testing


def test_derived_name_is_prefix_plus_sha1_of_repo_root() -> None:
    root = testing.repo_root()
    expected = "atlas_test_" + hashlib.sha1(str(root).encode()).hexdigest()[:8]  # noqa: S324
    assert testing.derived_database_name() == expected
    assert (root / "backend" / "artisan").is_file()
    assert str(root) == os.path.realpath(Path(__file__).parents[3])


def test_known_path_gives_the_same_name_as_php() -> None:
    # PHP: 'atlas_test_'.substr(sha1('/Users/owner/atlas'), 0, 8)
    assert testing.derived_database_name("/Users/owner/atlas") == "atlas_test_" + (
        hashlib.sha1(b"/Users/owner/atlas").hexdigest()[:8]  # noqa: S324
    )
    assert testing.derived_database_name("/a") != testing.derived_database_name("/b")


def test_override_wins_and_empty_override_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_TEST_DATABASE", "atlas_test")
    assert testing.database_name() == "atlas_test"
    assert testing.db_settings()["DB_DATABASE"] == "atlas_test"
    monkeypatch.setenv("DB_TEST_DATABASE", "")
    assert testing.database_name() == testing.derived_database_name()
    monkeypatch.delenv("DB_TEST_DATABASE")
    assert testing.database_name() == testing.derived_database_name()
