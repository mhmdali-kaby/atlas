"""ATL-205: signed ``POST /internal/backtest`` runs atlas_quant's walk-forward backtest on stored
candles, read-only.

Runs against this checkout's test DB (migrated by `cd backend && php artisan test`). Each test seeds
its own instruments (unique inst_ids, 1H candles in 2030, SWAP funding every 8 h) and removes them.
"""

from __future__ import annotations

import ast
import json
import math
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from atlas_quant import Costs, RuleSpec, backtest_rule, load_candles
from atlas_shared import testing
from atlas_shared.db import Candle, FundingRate, Instrument, make_engine
from atlas_shared.defaults import DEFAULTS
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, event, insert
from sqlalchemy.exc import OperationalError
from trading_core.app import create_app
from trading_core.backtest import api as backtest_api
from trading_core.config import Settings
from trading_core.scheduler import Scheduler
from trading_core.security import signed_headers

SECRET = "atlas-test-only-internal-secret"  # noqa: S105
PATH = "/internal/backtest"
TEST_DB = testing.db_settings()
ENV = "demo"
T0 = datetime(2030, 1, 1, tzinfo=UTC)
N_BARS = 1200
AT = T0 + timedelta(hours=N_BARS + 24)
PERIOD = {"start": (T0 + timedelta(hours=100)).isoformat(), "end": (T0 + timedelta(hours=N_BARS)).isoformat()}
RULE: dict[str, Any] = {
    "name": "sma_10_30_cross",
    "indicators": {"sma10": {"kind": "sma", "window": 10}, "sma30": {"kind": "sma", "window": 30}},
    "entry": [{"left": "sma10", "op": "crosses_above", "right": "sma30"}],
    "exit": [{"left": "sma10", "op": "crosses_below", "right": "sma30"}],
    "stop_pct": 3,
}


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except OperationalError as exc:
        pytest.fail(f"{TEST_DB['DB_DATABASE']} is not reachable: {exc}")
    return eng


def _candle_rows(inst_id: str, seed: int, skip: range | None = None) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.006, N_BARS)))
    o = np.concatenate(([100.0], c[:-1]))
    rows = []
    for i in range(N_BARS):
        if skip is not None and i in skip:
            continue
        hi, lo = max(o[i], c[i]) * 1.002, min(o[i], c[i]) * 0.998
        rows.append(
            {
                "env": ENV, "inst_id": inst_id, "bar": "1H", "ts": T0 + timedelta(hours=i),
                "o": Decimal(f"{o[i]:.8f}"), "h": Decimal(f"{hi:.8f}"), "l": Decimal(f"{lo:.8f}"),
                "c": Decimal(f"{c[i]:.8f}"), "vol": Decimal("10"), "confirmed": True,
            }
        )  # fmt: skip
    return rows


@pytest.fixture
def seeded(engine: Engine) -> Iterator[list[str]]:
    """Two SWAP instruments with clean candles and funding, and one with an interior gap."""
    tag = uuid.uuid4().hex[:6].upper()
    ids = [f"BT{tag}A-USDT-SWAP", f"BT{tag}B-USDT-SWAP", f"BT{tag}G-USDT-SWAP"]
    with engine.begin() as c:
        for k, inst_id in enumerate(ids):
            c.execute(
                insert(Instrument).values(
                    id=uuid.uuid4(),
                    env=ENV,
                    inst_id=inst_id,
                    inst_type="SWAP",
                    lot_sz=Decimal(1),
                    min_sz=Decimal(1),
                    tick_sz=Decimal("0.01"),
                    state="live",
                    asset_class="crypto",
                )
            )
            gap = range(600, 610) if k == 2 else None
            c.execute(insert(Candle), _candle_rows(inst_id, 7 + k, gap))
            c.execute(
                insert(FundingRate),
                [
                    {
                        "env": ENV,
                        "inst_id": inst_id,
                        "ts": T0 + timedelta(hours=h),
                        "funding_rate": Decimal("0.0001"),
                    }
                    for h in range(0, N_BARS, 8)
                ],
            )
    yield ids
    with engine.begin() as c:
        for model in (Candle, FundingRate, Instrument):
            c.execute(delete(model).where(model.env == ENV, model.inst_id.in_(ids)))


@pytest.fixture
def client(engine: Engine) -> TestClient:
    settings = Settings(
        host="127.0.0.1", port=8100, env=ENV, internal_shared_secret=SECRET,
        run_scheduler=False, run_market_ws=False, run_private_ws=False,
    )  # fmt: skip
    app = create_app(settings, scheduler=Scheduler(), engine=engine)
    app.state.clock = lambda: AT
    return TestClient(app)


def post(tc: TestClient, body: dict[str, Any] | bytes, headers: dict[str, str] | None = None) -> Any:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    hdrs = headers if headers is not None else signed_headers(SECRET, "POST", PATH, raw)
    return tc.post(PATH, content=raw, headers={**hdrs, "Content-Type": "application/json"})


def direct(engine: Engine, inst_id: str, splits: int = 3) -> Any:
    """atlas_quant.backtest_rule called by hand with what the endpoint should pass."""
    start, end = datetime.fromisoformat(PERIOD["start"]), datetime.fromisoformat(PERIOD["end"])
    load_from = start - timedelta(hours=int(DEFAULTS["backtest"]["warmup_bars"]))
    with engine.connect() as conn:
        candles = load_candles(conn, inst_id, "1H", load_from, end, as_of=AT, env=ENV)
    funding_ts = [
        T0 + timedelta(hours=h) for h in range(0, N_BARS, 8) if T0 + timedelta(hours=h) >= load_from
    ]
    funding = pd.Series([0.0001] * len(funding_ts), index=pd.DatetimeIndex(funding_ts), dtype="float64")
    fee = float(DEFAULTS["backtest"]["taker_fee_rates"]["SWAP"])
    return backtest_rule(
        RuleSpec.model_validate(RULE),
        {inst_id: candles.df},
        (start, end),
        Costs(taker_fee_rate=fee, funding={inst_id: funding}),
        splits=splits,
        quality={inst_id: candles.quality},
        bar="1H",
        asset_class="crypto",
    ).instruments[0]


def close(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)


METRICS = ("bars", "total_return", "cagr", "sharpe", "sortino", "max_drawdown", "n_trades", "win_rate",
           "profit_factor", "fees_paid", "funding_paid")  # fmt: skip


def assert_same_metrics(got: dict[str, Any], want: Any) -> None:
    for name in METRICS:
        assert close(got[name], getattr(want, name)), (name, got[name], getattr(want, name))


def test_metrics_match_a_direct_backtest_rule_call(
    client: TestClient, engine: Engine, seeded: list[str]
) -> None:
    a, b, _ = seeded
    res = post(client, {"rule_spec": RULE, "inst_ids": [a, b], "bar": "1H", "period": PERIOD, "splits": 3})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["env"] == ENV and body["splits"] == 3
    assert body["data_gap"] is False and body["excluded"] == []
    assert [i["inst_id"] for i in body["instruments"]] == [a, b]
    for inst in body["instruments"]:
        want = direct(engine, inst["inst_id"])
        assert inst["full"]["n_trades"] > 0
        assert_same_metrics(inst["full"], want.full)
        assert len(inst["folds"]) == 3
        for got_fold, want_fold in zip(inst["folds"], want.folds, strict=True):
            assert_same_metrics(got_fold["in_sample"], want_fold.in_sample)
            assert_same_metrics(got_fold["out_of_sample"], want_fold.out_of_sample)
        assert close(inst["is_sharpe_mean"], want.is_sharpe_mean)
        assert close(inst["oos_sharpe_mean"], want.oos_sharpe_mean)
        assert inst["overfit_warning"] == want.overfit_warning
        assert inst["fee_source"] == "config" and inst["funding_included"] is True
        assert inst["full"]["funding_paid"] != 0.0
        # The equity curve and trades are the full run's, capped by config.
        assert inst["equity_curve_points"] == inst["full"]["bars"]
        assert 0 < len(inst["equity_curve"]) <= DEFAULTS["backtest"]["equity_curve_max_points"]
        assert inst["equity_curve"][0]["equity"] == 1.0
        assert close(inst["equity_curve"][-1]["equity"], 1.0 + want.full.total_return)
        assert inst["trades_total"] == want.full.n_trades == len(inst["trades"])
        assert close(sum(t["pnl"] for t in inst["trades"]), inst["equity_curve"][-1]["equity"] - 1.0)
    assert body["n_trades"] == sum(i["full"]["n_trades"] for i in body["instruments"])


def test_equity_curve_and_trades_are_capped(
    client: TestClient, seeded: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(DEFAULTS["backtest"], "equity_curve_max_points", 50)
    monkeypatch.setitem(DEFAULTS["backtest"], "trades_max_rows", 2)
    body = post(client, {"rule_spec": RULE, "inst_ids": [seeded[0]], "bar": "1H", "period": PERIOD}).json()
    inst = body["instruments"][0]
    assert len(inst["equity_curve"]) == 50
    assert inst["equity_curve"][0]["ts"] == PERIOD["start"].replace("+00:00", "Z")
    assert len(inst["trades"]) == 2 and inst["trades_total"] > 2 and inst["trades_truncated"] is True


def test_unrepaired_gap_gives_a_note_and_the_gap_verdict(
    client: TestClient, engine: Engine, seeded: list[str]
) -> None:
    gapped = seeded[2]
    body = post(client, {"rule_spec": RULE, "inst_ids": [gapped], "bar": "1H", "period": PERIOD}).json()
    inst = body["instruments"][0]
    want = direct(engine, gapped)
    assert want.data_gap is True
    assert body["data_gap"] is True and inst["data_gap"] is True
    assert inst["gap_notes"] == want.gap_notes and inst["gap_notes"]
    assert any(n.startswith("missing bars between") for n in inst["gap_notes"])
    assert inst["verdict_cap"] == "does_not_support"
    assert inst["data_quality"]["interior_gap"] is True
    interior = [g for g in inst["data_quality"]["gaps"] if g["position"] == "interior"]
    assert [g["missing_bars"] for g in interior] == [10]
    assert inst["overfit_warning"] == want.overfit_warning
    assert_same_metrics(inst["full"], want.full)


def test_unknown_instrument_is_excluded_and_all_unknown_is_422(client: TestClient, seeded: list[str]) -> None:
    body = post(
        client, {"rule_spec": RULE, "inst_ids": [seeded[0], "NOPE-USDT-SWAP"], "bar": "1H", "period": PERIOD}
    ).json()
    assert [i["inst_id"] for i in body["instruments"]] == [seeded[0]]
    assert body["excluded"][0]["inst_id"] == "NOPE-USDT-SWAP"
    res = post(client, {"rule_spec": RULE, "inst_ids": ["NOPE-USDT-SWAP"], "bar": "1H", "period": PERIOD})
    assert res.status_code == 422 and res.json()["error"] == "no_data"


def test_missing_stale_or_wrong_signature_is_401(client: TestClient) -> None:
    raw = json.dumps({"rule_spec": RULE, "inst_ids": ["X"], "bar": "1H", "period": PERIOD}).encode()
    assert post(client, raw, headers={}).status_code == 401
    stale = signed_headers(SECRET, "POST", PATH, raw, timestamp=int(datetime.now(UTC).timestamp()) - 120)
    assert post(client, raw, headers=stale).status_code == 401
    assert post(client, raw, headers=signed_headers("wrong-secret", "POST", PATH, raw)).status_code == 401
    other_body = signed_headers(SECRET, "POST", PATH, b"{}")
    assert post(client, raw, headers=other_body).status_code == 401


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"rule_spec": {**RULE, "entry": []}}, "rule_spec.entry"),
        (
            {"rule_spec": {**RULE, "entry": [{"left": "sma99", "op": "above", "right": 1}]}},
            "unknown indicator",
        ),
        ({"rule_spec": {**RULE, "side": "sideways"}}, "rule_spec.side"),
        ({"inst_ids": []}, "inst_ids"),
        ({"inst_ids": [f"I{n}" for n in range(6)]}, "at most 5"),
        ({"bar": "2H"}, "bar"),
        ({"period": {"start": PERIOD["end"], "end": PERIOD["start"]}}, "before period.end"),
        ({"period": {"start": "2030-01-01T00:00:00", "end": PERIOD["end"]}}, "period.start"),
        ({"extra": 1}, "extra"),
    ],
)
def test_invalid_body_is_422_with_a_reason(client: TestClient, change: dict[str, Any], fragment: str) -> None:
    body = {"rule_spec": RULE, "inst_ids": ["X-USDT-SWAP"], "bar": "1H", "period": PERIOD, **change}
    res = post(client, body)
    assert res.status_code == 422, res.text
    out = res.json()
    assert out["ok"] is False and out["error"] == "invalid"
    assert fragment in out["reason"] or fragment in json.dumps(out["errors"])


def test_over_max_bars_is_422(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(DEFAULTS["backtest"], "max_bars", 500)
    res = post(client, {"rule_spec": RULE, "inst_ids": ["X-USDT-SWAP"], "bar": "1H", "period": PERIOD})
    assert res.status_code == 422
    assert res.json()["error"] == "too_many_bars"
    assert "1100 1H bars" in res.json()["reason"] and "500" in res.json()["reason"]


def test_too_few_bars_for_the_splits_is_422(client: TestClient, seeded: list[str]) -> None:
    short = {"start": (T0 + timedelta(hours=100)).isoformat(), "end": (T0 + timedelta(hours=104)).isoformat()}
    res = post(
        client, {"rule_spec": RULE, "inst_ids": [seeded[0]], "bar": "1H", "period": short, "splits": 3}
    )
    assert res.status_code == 422 and res.json()["error"] == "insufficient_data"


def test_timeout_is_422(client: TestClient, seeded: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(DEFAULTS["backtest"], "timeout_seconds", 0)
    res = post(client, {"rule_spec": RULE, "inst_ids": [seeded[0]], "bar": "1H", "period": PERIOD})
    assert res.status_code == 422 and res.json()["error"] == "timeout"


def test_a_backtest_writes_nothing(client: TestClient, engine: Engine, seeded: list[str]) -> None:
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        res = post(client, {"rule_spec": RULE, "inst_ids": seeded, "bar": "1H", "period": PERIOD})
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert res.status_code == 200
    assert statements and statements[0].strip().upper() == "SET TRANSACTION READ ONLY"
    words = {s.strip().split()[0].upper() for s in statements}
    assert words <= {"SET", "SELECT"}, words


def test_the_backtest_package_imports_no_execution_or_okx_code() -> None:
    package = Path(backtest_api.__file__).parent
    imported: list[str] = []
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
    assert imported
    forbidden = ("trading_core.execution", "trading_core.okx", "trading_core.risk", "okx", "httpx")
    assert not [m for m in imported if m.startswith(forbidden)], imported
