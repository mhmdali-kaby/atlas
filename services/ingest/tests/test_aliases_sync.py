"""ATL-62 `aliases sync` against the real test database (decision #239)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from atlas_ingest import aliases
from atlas_shared import testing
from atlas_shared.db import Instrument, InstrumentAlias, make_engine
from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.exc import OperationalError

ENV = "demo"
TEST_DB = testing.db_settings()
NOW = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{TEST_DB['DB_DATABASE']} is not reachable (run `php artisan test` first): {exc}")
    return eng


def _inst(inst_id: str, family: str, asset_class: str, inst_type: str = "SWAP") -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "env": ENV,
        "inst_id": inst_id,
        "inst_type": inst_type,
        "inst_family": family,
        "base_ccy": "",
        "quote_ccy": "",
        "lot_sz": Decimal("1"),
        "min_sz": Decimal("1"),
        "tick_sz": Decimal("0.1"),
        "state": "live",
        "asset_class": asset_class,
        "created_at": NOW,
        "updated_at": NOW,
    }


@pytest.fixture
def db(engine: Engine) -> Iterator[Engine]:
    def wipe() -> None:
        with engine.begin() as conn:
            conn.execute(delete(InstrumentAlias))
            conn.execute(delete(Instrument).where(Instrument.env == ENV))

    wipe()
    with engine.begin() as conn:
        conn.execute(
            insert(Instrument),
            [
                _inst("BTC-USDT-SWAP", "BTC-USDT", "crypto"),
                _inst("TSLA-USDT-SWAP", "TSLA-USDT", "stock"),
                _inst("AAPL-USDC-SWAP", "AAPL-USDC", "stock"),
            ],
        )
    yield engine
    wipe()


def _count(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(InstrumentAlias)).scalar_one())


def test_sync_twice_leaves_row_count_unchanged(db: Engine) -> None:
    first = aliases.sync(db, ENV)
    assert first > 0
    assert _count(db) == first
    second = aliases.sync(db, ENV)
    assert second == first
    assert _count(db) == first


def test_synced_aliases_link_only_env_instruments(db: Engine) -> None:
    aliases.sync(db, ENV)
    with db.connect() as conn:
        targets = set(conn.execute(select(InstrumentAlias.target)).scalars())
    assert "XAU-USDT" not in targets  # gold is curated but not listed in this env
    linker = aliases.load_linker(db, ENV)
    assert linker.link("Tesla shares jump").instruments == ("TSLA-USDT",)
    assert linker.link("Apple beats estimates").instruments == ("AAPL-USDC",)
    assert linker.link("Gold rises as bitcoin slips").instruments == ("BTC-USDT",)


def test_linker_ignores_rows_for_instruments_no_longer_listed(db: Engine) -> None:
    aliases.sync(db, ENV)
    with db.begin() as conn:
        conn.execute(delete(Instrument).where(Instrument.env == ENV, Instrument.inst_id == "TSLA-USDT-SWAP"))
    assert aliases.load_linker(db, ENV).link("Tesla shares jump").instruments == ()


def test_watcher_resyncs_only_when_instrument_count_changes(db: Engine) -> None:
    watcher = aliases.AliasWatcher(db, ENV)
    assert watcher.poll() is not None
    assert watcher.poll() is None
    with db.begin() as conn:
        conn.execute(insert(Instrument), [_inst("XAU-USDT-SWAP", "XAU-USDT", "commodity")])
    assert watcher.poll() is not None
    assert aliases.load_linker(db, ENV).link("Gold hits record").instruments == ("XAU-USDT",)


def test_cli_sync_writes_rows(db: Engine, capsys: pytest.CaptureFixture[str]) -> None:
    assert aliases.main(["sync", "--env", ENV], env_vars=TEST_DB) == 0
    assert f"rows for env {ENV}" in capsys.readouterr().out
    assert _count(db) > 0
