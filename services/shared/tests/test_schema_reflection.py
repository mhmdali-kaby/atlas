"""Reflects the live test-database schema (migrated by Laravel) and compares it with the models.

Run `cd backend && php artisan test` first: it creates and migrates this checkout's test DB, so the
schema exists. Connection settings mirror backend/phpunit.xml and can be overridden with DB_*.
"""

from typing import Any

import pytest
from atlas_shared import testing
from atlas_shared.db import Base, make_engine
from sqlalchemy import Column, Engine, Float, MetaData, Table, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from sqlalchemy.types import TypeEngine

TEST_DB = testing.db_settings()

DIALECT = postgresql.dialect()  # type: ignore[no-untyped-call]


def _type_sql(col_type: TypeEngine[Any]) -> str:
    return str(col_type.compile(dialect=DIALECT))


def schema_drift(engine: Engine, metadata: MetaData) -> list[str]:
    """Every difference between the models in ``metadata`` and the live database."""
    inspector = inspect(engine)
    problems: list[str] = []
    for table in metadata.sorted_tables:
        if not inspector.has_table(table.name):
            problems.append(f"{table.name}: table missing in database")
            continue
        live = {c["name"]: c for c in inspector.get_columns(table.name)}
        model = {c.name: c for c in table.columns}
        for name in sorted(live.keys() - model.keys()):
            problems.append(f"{table.name}.{name}: in database, missing from model")
        for name in sorted(model.keys() - live.keys()):
            problems.append(f"{table.name}.{name}: in model, missing from database")
        for name in sorted(model.keys() & live.keys()):
            want, got = _type_sql(model[name].type), _type_sql(live[name]["type"])
            if want != got:
                problems.append(f"{table.name}.{name}: model {want}, database {got}")
            if bool(model[name].nullable) != bool(live[name]["nullable"]):
                problems.append(
                    f"{table.name}.{name}: model nullable={model[name].nullable}, "
                    f"database nullable={live[name]['nullable']}"
                )
        live_pk = set(inspector.get_pk_constraint(table.name)["constrained_columns"])
        model_pk = {c.name for c in table.primary_key.columns}
        if live_pk != model_pk:
            problems.append(f"{table.name}: primary key model {sorted(model_pk)}, database {sorted(live_pk)}")
        live_uniques = {
            tuple(sorted(str(c) for c in u["column_names"]))
            for u in inspector.get_unique_constraints(table.name)
        }
        for constraint in table.constraints:
            if constraint.__class__.__name__ == "UniqueConstraint":
                cols = tuple(sorted(c.name for c in constraint.columns))  # type: ignore[attr-defined]
                if cols not in live_uniques:
                    problems.append(f"{table.name}: unique {list(cols)} missing in database")
    return problems


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(
            f"{TEST_DB['DB_DATABASE']} is not reachable on {TEST_DB['DB_HOST']}:{TEST_DB['DB_PORT']}: {exc}"
        )
    return eng


def test_models_match_live_schema(engine: Engine) -> None:
    assert schema_drift(engine, Base.metadata) == []


def test_drift_detected_when_a_model_column_is_removed(engine: Engine) -> None:
    trimmed = MetaData()
    for table in Base.metadata.sorted_tables:
        keep = [c._copy() for c in table.columns if not (table.name == "instruments" and c.name == "tick_sz")]
        Table(table.name, trimmed, *keep)
    assert "instruments.tick_sz: in database, missing from model" in schema_drift(engine, trimmed)


def test_drift_detected_when_a_model_type_is_wrong(engine: Engine) -> None:
    trimmed = MetaData()
    for table in Base.metadata.sorted_tables:
        swap = table.name == "balances_snapshots"
        cols = [
            Column("eq", Float(), nullable=False) if swap and c.name == "eq" else c._copy()
            for c in table.columns
        ]
        Table(table.name, trimmed, *cols)
    drift = schema_drift(engine, trimmed)
    assert any(p.startswith("balances_snapshots.eq: model FLOAT") for p in drift), drift
