"""ATL-87: the history tools load candles through atlas_quant at as_of and carry the sample block.

DB tests run against this checkout's test DB (atlas_shared.testing), migrated by
`cd backend && php artisan test`. Instrument ids are unique to this module.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pytest
from atlas_agents.tools import ToolContext, fq_name
from atlas_agents.tools import framework as fw
from atlas_quant import find_analogs, load_candles
from atlas_shared.db import Candle, EconomicEvent
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session, sessionmaker

ENV = "demo"
INST = "ATL87A-USDT-SWAP"
INST_B = "ATL87B-USDT-SWAP"
GAPPED = "ATL87G-USDT-SWAP"
EMPTY = "ATL87NONE-USDT-SWAP"
ALL = (INST, INST_B, GAPPED)
SOURCE = "atl87-test"
HOUR = timedelta(hours=1)
T0 = datetime(2026, 8, 1, tzinfo=UTC)
BARS = 900
REPLAY_AS_OF = T0 + HOUR * 700  # 200 bars exist after the cutoff
BIG = {"max_result_tokens": 10**7, "chars_per_token": 4, "trim_rows": 10**6}
SMA_RULE = {
    "name": "sma_5_20",
    "side": "long",
    "indicators": {"f": {"kind": "sma", "window": 5}, "s": {"kind": "sma", "window": 20}},
    "entry": [{"left": "f", "op": "crosses_above", "right": "s"}],
    "exit": [{"left": "f", "op": "crosses_below", "right": "s"}],
}


def _rows(inst: str, seed: int, skip: range = range(0)) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, BARS)))
    rows = []
    for i in range(BARS):
        if i in skip:
            continue
        c = Decimal(f"{close[i]:.4f}")
        o = Decimal(f"{(close[i - 1] if i else close[0]):.4f}")
        rows.append(
            {
                "env": ENV,
                "inst_id": inst,
                "bar": "1H",
                "ts": T0 + HOUR * i,
                "o": o,
                "h": max(o, c) * Decimal("1.004"),
                "l": min(o, c) * Decimal("0.996"),
                "c": c,
                "vol": Decimal(int(rng.integers(50, 500))),
                "vol_ccy": None,
                "vol_ccy_quote": None,
                "confirmed": True,
            }
        )
    return rows


def _wipe(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(delete(Candle).where(Candle.inst_id.in_(ALL)))
        conn.execute(delete(EconomicEvent).where(EconomicEvent.source == SOURCE))


@pytest.fixture(scope="module")
def seeded(engine: Engine) -> Iterator[Engine]:
    _wipe(engine)
    with engine.begin() as conn:
        conn.execute(Candle.__table__.insert(), _rows(INST, 1))
        conn.execute(Candle.__table__.insert(), _rows(INST_B, 2))
        conn.execute(Candle.__table__.insert(), _rows(GAPPED, 3, skip=range(300, 306)))
        conn.execute(
            EconomicEvent.__table__.insert(),
            [
                {
                    "source": SOURCE,
                    "external_id": f"cpi-{i}",
                    "country": "US",
                    "name": "CPI y/y",
                    "importance": 3,
                    "scheduled_at": T0 + HOUR * (100 + 96 * i) + timedelta(minutes=30),
                }
                for i in range(6)
            ],
        )
    yield engine
    _wipe(engine)


def _ctx(engine: Engine, replay_as_of: datetime | None = REPLAY_AS_OF) -> ToolContext:
    return ToolContext(
        env=ENV,
        session_factory=sessionmaker(bind=engine),
        agent="historian",
        replay_as_of=replay_as_of,
        clock=lambda: T0 + HOUR * BARS,
    )


async def _call(ctx: ToolContext, server: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await fw.make_handler(fw.get_tool(fq_name(server, name)), ctx, BIG)(args)
    body: dict[str, Any] = json.loads(result["content"][0]["text"])
    assert not result.get("is_error"), body
    return body


async def test_find_analogs_tool_in_replay_matches_atlas_quant_called_with_as_of(seeded: Engine) -> None:
    args = {"inst_id": INST, "bar": "1H", "window": 12, "k": 5, "horizons": ["1h", "4h", "24h"]}
    body = await _call(_ctx(seeded), "history", "find_analogs", args)

    with Session(seeded) as s:
        df, _q = load_candles(
            s, INST, "1H", REPLAY_AS_OF - timedelta(days=365 * 3), REPLAY_AS_OF, as_of=REPLAY_AS_OF, env=ENV
        )
    direct = find_analogs(df, "1H", 12, as_of=REPLAY_AS_OF, k=5, horizons=["1h", "4h", "24h"])

    assert body["data"]["status"] == "ok"
    assert body["data"]["result"] == direct.model_dump(mode="json")
    assert body["data"]["sample"]["n"] == len(direct.neighbours) == 5
    assert datetime.fromisoformat(body["as_of"]) == REPLAY_AS_OF
    assert datetime.fromisoformat(body["data"]["result"]["query_end"]) < REPLAY_AS_OF


# Arguments per tool for the seeded instrument; the no-data test swaps in EMPTY.
CASES: dict[str, dict[str, Any]] = {
    "compute_stats": {"inst_id": INST, "bar": "1H", "lookback": 500},
    "detect_regime": {"inst_id": INST, "bar": "1H", "lookback": 600},
    "volatility_forecast": {"inst_id": INST, "bar": "1H", "lookback": 600, "horizon": 24},
    "drawdown_profile": {"inst_id": INST, "bar": "1H", "lookback": 600},
    "seasonality": {"inst_id": INST, "bar": "1H", "lookback": 600, "by": "weekday"},
    "find_analogs": {"inst_id": INST, "bar": "1H", "window": 12, "k": 5},
    "cross_asset_lead_lag": {"a": INST, "b": INST_B, "bar": "1H", "lookback": 600, "max_lag": 3},
    "event_study": {"inst_id": INST, "event_type": "CPI", "bar": "1H", "window": [-4, 8]},
    "backtest_rule": {
        "inst_ids": [INST],
        "rule_spec": SMA_RULE,
        "bar": "1H",
        "costs": {"taker_fee_rate": 0.0005},
    },
}


def test_cases_cover_every_history_tool() -> None:
    from atlas_agents.tools.catalogue import CATALOGUE

    assert set(CASES) == set(CATALOGUE["history"])


def _check_sample(sample: dict[str, Any], expect_inst: list[str]) -> None:
    assert sample["bar"] == "1H"
    assert {"start", "end"} <= set(sample["period"])
    assert isinstance(sample["n"], int)
    assert sample["reference_data"] is False
    assert [q["inst_id"] for q in sample["data_quality"]] == expect_inst
    for q in sample["data_quality"]:
        assert {"expected_bars", "returned_bars", "missing_bars", "interior_gap", "gaps"} <= set(q)


@pytest.mark.parametrize("tool", list(CASES))
async def test_every_history_tool_returns_the_sample_block(seeded: Engine, tool: str) -> None:
    body = await _call(_ctx(seeded), "history", tool, CASES[tool])
    data = body["data"]
    assert data["status"] == "ok", data.get("note")
    expect = [INST, INST_B] if tool == "cross_asset_lead_lag" else [INST]
    _check_sample(data["sample"], expect)
    assert data["sample"]["n"] > 0
    assert data["sample"]["period"]["end"] is not None
    assert datetime.fromisoformat(data["sample"]["period"]["end"]) <= REPLAY_AS_OF
    assert datetime.fromisoformat(body["as_of"]) <= REPLAY_AS_OF
    assert data["result"] is not None


def _swap(args: dict[str, Any]) -> dict[str, Any]:
    out = dict(args)
    for key in ("inst_id", "a"):
        if key in out:
            out[key] = EMPTY
    if "inst_ids" in out:
        out["inst_ids"] = [EMPTY]
    return out


@pytest.mark.parametrize("tool", list(CASES))
async def test_a_tool_on_an_instrument_with_no_candles_says_no_data(seeded: Engine, tool: str) -> None:
    body = await _call(_ctx(seeded), "history", tool, _swap(CASES[tool]))
    data = body["data"]
    assert data["status"] == "no_data"
    assert data["result"] is None
    assert "no 1H candles" in data["note"]
    sample = data["sample"]
    assert sample["n"] == 0
    if tool != "cross_asset_lead_lag":  # b still has candles there, so its period shows
        assert sample["period"] == {"start": None, "end": None}
    assert sample["weak_sample"] is True
    assert sample["data_quality"][0]["inst_id"] == EMPTY
    assert sample["data_quality"][0]["returned_bars"] == 0
    assert datetime.fromisoformat(body["as_of"]) == REPLAY_AS_OF


async def test_live_mode_uses_the_clock_and_sees_every_closed_bar(seeded: Engine) -> None:
    body = await _call(
        _ctx(seeded, None), "history", "compute_stats", {"inst_id": INST, "bar": "1H", "lookback": 2000}
    )
    assert body["data"]["sample"]["bars_loaded"] == BARS
    replay = await _call(
        _ctx(seeded), "history", "compute_stats", {"inst_id": INST, "bar": "1H", "lookback": 2000}
    )
    assert replay["data"]["sample"]["bars_loaded"] == 700


async def test_backtest_on_gapped_data_reports_the_unrepaired_gap(seeded: Engine) -> None:
    args = {"inst_ids": [GAPPED], "rule_spec": SMA_RULE, "bar": "1H"}
    data = (await _call(_ctx(seeded), "history", "backtest_rule", args))["data"]
    assert data["sample"]["unrepaired_gap"] is True
    interior = [g for g in data["sample"]["data_quality"][0]["gaps"] if g["position"] == "interior"]
    assert [g["missing_bars"] for g in interior] == [6]
    assert data["result"]["data_gap"] is True
    assert data["taker_fee_source"] == "config"


async def test_event_study_counts_only_events_before_the_cutoff(seeded: Engine) -> None:
    data = (await _call(_ctx(seeded), "history", "event_study", CASES["event_study"]))["data"]
    assert data["event_type"] == "cpi"
    used = [datetime.fromisoformat(t) for t in data["events_used"]]
    assert used and all(t <= REPLAY_AS_OF for t in used)
    assert len(used) == 6  # all six seeded events precede the cutoff
    assert data["sample"]["n"] == data["result"]["n_cases"]


async def test_bad_inputs_are_tool_errors_not_exceptions(seeded: Engine) -> None:
    ctx = _ctx(seeded)
    handler = fw.make_handler(fw.get_tool(fq_name("history", "event_study")), ctx, BIG)
    res = await handler({"inst_id": INST, "event_type": "eclipse"})
    assert res["is_error"] is True
    assert json.loads(res["content"][0]["text"])["error"] == "unknown_event_type"
    handler = fw.make_handler(fw.get_tool(fq_name("history", "backtest_rule")), ctx, BIG)
    res = await handler({"inst_ids": [INST], "rule_spec": {"name": "x"}})
    assert json.loads(res["content"][0]["text"])["error"] == "invalid_rule"
