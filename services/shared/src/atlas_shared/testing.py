"""Per-checkout Postgres test database (ATL-29).

Every checkout (worktree) gets its own test database, ``atlas_test_<first 8 hex of
sha1(realpath of the repository root)>``, so parallel runs in different checkouts never reset each
other's schema. ``DB_TEST_DATABASE``, when set and non-empty, overrides the derived name.

The same rule lives in PHP in ``backend/tests/Support/TestDatabase.php``; ``php artisan test``
creates and migrates the database that these helpers point pytest at.

Redis (ATL-186): every checkout shares the test Redis DB (``REDIS_TEST_DB``, default 15) but gets its
own key prefix, ``test_<the same 8 hex>:``. ``services/conftest.py`` sets it as ``ATLAS_REDIS_PREFIX``
for the whole pytest run, so every stream and key the code under test writes is prefixed, and
fixtures delete only their own prefixed keys. ``REDIS_TEST_PREFIX``, when set and non-empty,
overrides the derived prefix.
"""

import hashlib
import os
from pathlib import Path

PREFIX = "atlas_test_"
OVERRIDE_VAR = "DB_TEST_DATABASE"
REDIS_PREFIX_OVERRIDE_VAR = "REDIS_TEST_PREFIX"
REDIS_DB_VAR = "REDIS_TEST_DB"


def _is_repo_root(path: Path) -> bool:
    return (path / "backend" / "artisan").is_file() and (path / "services" / "pyproject.toml").is_file()


def repo_root() -> Path:
    """The checkout this package was loaded from (uv installs workspace members editable)."""
    # services/shared/src/atlas_shared/testing.py -> parents[4] is the repository root.
    here = Path(os.path.realpath(__file__))
    candidate = here.parents[4]
    if _is_repo_root(candidate):
        return candidate
    for path in (Path(os.path.realpath(os.getcwd())), *Path(os.path.realpath(os.getcwd())).parents):
        if _is_repo_root(path):
            return path
    raise RuntimeError(f"Cannot find the Atlas repository root from {here} or the current directory")


def checkout_hash(root: str | os.PathLike[str] | None = None) -> str:
    """First 8 hex chars of sha1 of the real path of the repository root."""
    path = os.path.realpath(root) if root is not None else str(repo_root())
    return hashlib.sha1(path.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


def derived_database_name(root: str | os.PathLike[str] | None = None) -> str:
    """``atlas_test_`` + first 8 hex chars of sha1 of the real path of the repository root."""
    return PREFIX + checkout_hash(root)


def derived_redis_prefix(root: str | os.PathLike[str] | None = None) -> str:
    """``test_`` + the checkout hash + ``:``."""
    return f"test_{checkout_hash(root)}:"


def redis_prefix() -> str:
    """This checkout's Redis key prefix, honouring the ``REDIS_TEST_PREFIX`` override."""
    override = os.environ.get(REDIS_PREFIX_OVERRIDE_VAR, "")
    return override if override else derived_redis_prefix()


def database_name() -> str:
    """The test database for this checkout, honouring the ``DB_TEST_DATABASE`` override."""
    override = os.environ.get(OVERRIDE_VAR, "")
    return override if override else derived_database_name()


def db_settings() -> dict[str, str]:
    """Connection settings for ``atlas_shared.db.make_engine``; mirrors backend/phpunit.xml."""
    return {
        "DB_HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "DB_PORT": os.environ.get("DB_PORT", "5433"),
        "DB_DATABASE": database_name(),
        "DB_USERNAME": os.environ.get("DB_USERNAME", "atlas"),
        "DB_PASSWORD": os.environ.get("DB_PASSWORD", "atlas"),
    }


def redis_settings() -> dict[str, str]:
    """``os.environ`` with ``REDIS_DB`` pointed at the test DB, for ``atlas_shared.bus.make_client``."""
    return {**os.environ, "REDIS_DB": os.environ.get(REDIS_DB_VAR, "15")}
