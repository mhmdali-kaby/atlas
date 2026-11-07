"""Market-data hypertables (ATL-44) written through the SQLAlchemy models.

Needs the checkout's test DB migrated: run `cd backend && php artisan test` first.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from atlas_shared import testing
from atlas_shared.db import Candle, FundingRate, TakerVolume, make_engine
from sqlalchemy import Engine, delete, func, inspect, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

TS = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
# A per-run instrument id so concurrent runs and leftovers never collide.
INST = f"T{uuid.uuid4().hex[:8].upper()}-USDT-SWAP"


@pytest.fixture(scope="module")
def engine() -> Engine:
    return make_engine(testing.db_settings())


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s
        s.rollback()
        for model in (Candle, FundingRate):
            s.execute(delete(model).where(model.inst_id == INST))
        s.execute(delete(TakerVolume).where(TakerVolume.ccy == INST))
        s.commit()


def _upsert_candle(session: Session, **values: Any) -> None:
    row = {
        "env": "demo",
        "inst_id": INST,
        "bar": "1m",
        "ts": TS,
        "o": Decimal("1"),
        "h": Decimal("2"),
        "l": Decimal("0.5"),
        "c": Decimal("1.5"),
        "vol": Decimal("10"),
        "confirmed": False,
        **values,
    }
    stmt = insert(Candle).values(row)
    mapper = inspect(Candle)
    updatable = {c.name: stmt.excluded[c.name] for c in mapper.columns if not c.primary_key}
    pk = list(mapper.primary_key)
    session.execute(stmt.on_conflict_do_update(index_elements=pk, set_=updatable))
    session.commit()


def test_same_candle_upserted_twice_leaves_one_row(session: Session) -> None:
    _upsert_candle(session)
    _upsert_candle(session, c=Decimal("1.234567890123456789"), vol_ccy_quote=Decimal("42.5"), confirmed=True)

    rows = session.scalars(select(Candle).where(Candle.inst_id == INST)).all()
    assert len(rows) == 1
    assert rows[0].c == Decimal("1.234567890123456789")
    assert rows[0].vol_ccy_quote == Decimal("42.5")
    assert rows[0].confirmed is True


def test_other_bar_or_env_is_a_separate_candle(session: Session) -> None:
    _upsert_candle(session)
    _upsert_candle(session, bar="5m")
    _upsert_candle(session, env="live")

    count = session.scalar(select(func.count()).select_from(Candle).where(Candle.inst_id == INST))
    assert count == 3


def test_funding_and_taker_volume_round_trip_as_decimal(session: Session) -> None:
    session.add(
        FundingRate(env="demo", inst_id=INST, ts=TS, funding_rate=Decimal("0.0001234"), realized_rate=None)
    )
    session.add(
        TakerVolume(
            env="demo", ccy=INST, period="1H", ts=TS, buy_vol=Decimal("12.5"), sell_vol=Decimal("7.25")
        )
    )
    session.commit()

    funding = session.scalars(select(FundingRate).where(FundingRate.inst_id == INST)).one()
    taker = session.scalars(select(TakerVolume).where(TakerVolume.ccy == INST)).one()
    assert funding.funding_rate == Decimal("0.0001234")
    assert isinstance(taker.buy_vol, Decimal) and taker.sell_vol == Decimal("7.25")
