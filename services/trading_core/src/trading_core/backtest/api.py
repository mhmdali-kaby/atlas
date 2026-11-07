"""Signed ``POST /internal/backtest`` (ATL-205, decision #253 L, brief §7.3 ``backtest_rule``).

Runs :func:`atlas_quant.backtest_rule_detailed` (the same engine and :class:`~atlas_quant.RuleSpec`
the Historian's ``history.backtest_rule`` tool uses) on candles stored in Postgres for the env this
trading_core runs. Read-only: the candles and funding are read inside a ``READ ONLY`` transaction,
nothing is written, nothing is sent to OKX, and this package imports neither the Executor nor an
OKX client (a test asserts both). The signature middleware already answers 401 to unsigned, stale
or wrongly signed calls.

Request body (JSON)::

    {"rule_spec": {RuleSpec}, "inst_ids": ["BTC-USDT-SWAP"], "bar": "1H",
     "period": {"start": "2026-01-01T00:00:00Z", "end": "2026-07-01T00:00:00Z"},
     "splits": 3, "slippage_bps": 2}

``splits`` and ``slippage_bps`` are optional (quant.backtest defaults). Bars ``start <= ts < end``
that had closed by now are used; ``backtest.warmup_bars`` more are loaded before ``start`` to warm
the indicators. Fees are ``backtest.taker_fee_rates`` per instType (no trade-fee rate is stored yet,
so ``fee_source`` is ``config``, or ``config_fallback`` for an unlisted instType). Funding from
``funding_rates`` is charged for SWAP instruments.

200: see :func:`response_body` and ``docs/handoff/ATL-205-backtest-endpoint.md``.
422 ``{"ok": false, "error": <code>, "reason": str, "errors"?: [...]}`` where code is ``invalid``
(body or rule_spec), ``too_many_bars``, ``timeout``, ``no_data`` or ``insufficient_data``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import numpy as np
import pandas as pd
from atlas_quant import (
    Candles,
    Costs,
    DataQuality,
    RuleSpec,
    RunDetail,
    backtest_rule_detailed,
    load_candles,
)
from atlas_quant.grid import bar_delta
from atlas_shared.db import FundingRate, Instrument
from atlas_shared.defaults import DEFAULTS
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import Connection, Engine, select, text

from trading_core.config import Settings
from trading_core.security import PROTECTED_PREFIX

logger = logging.getLogger("trading_core.backtest")

router = APIRouter(prefix=PROTECTED_PREFIX)

Bar = Literal["1m", "5m", "15m", "1H", "4H", "1D"]
FUNDING_INST_TYPES = frozenset({"SWAP"})


def config() -> Mapping[str, Any]:
    cfg: Mapping[str, Any] = DEFAULTS["backtest"]
    return cfg


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if self.start >= self.end:
            raise ValueError("period.start must be before period.end")
        return self


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_spec: RuleSpec
    inst_ids: list[str] = Field(min_length=1)
    bar: Bar
    period: Period
    splits: int | None = Field(default=None, ge=1, le=10)
    slippage_bps: float | None = Field(default=None, ge=0, le=1000)

    @model_validator(mode="after")
    def _instruments(self) -> BacktestRequest:
        limit = int(config()["max_inst_ids"])
        if len(self.inst_ids) > limit:
            raise ValueError(f"at most {limit} inst_ids per backtest")
        if len(set(self.inst_ids)) != len(self.inst_ids):
            raise ValueError("inst_ids must not repeat")
        return self


class BacktestRefused(Exception):
    """A request the endpoint answers with 422 ``{"error": code, "reason": reason}``."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class InstrumentInput:
    inst_id: str
    inst_type: str | None
    asset_class: str | None
    taker_fee_rate: float
    fee_source: Literal["config", "config_fallback"]
    candles: Candles
    funding: pd.Series[float] | None


def fee_for(inst_type: str | None) -> tuple[float, Literal["config", "config_fallback"]]:
    """Taker fee fraction for an OKX instType, and where it came from."""
    rates: Mapping[str, Any] = config()["taker_fee_rates"]
    if inst_type is not None and inst_type in rates:
        return float(Decimal(str(rates[inst_type]))), "config"
    return float(Decimal(str(config()["fallback_taker_fee_rate"]))), "config_fallback"


def period_bars(req: BacktestRequest) -> int:
    return int((req.period.end - req.period.start) / bar_delta(req.bar))


def _funding(conn: Connection, env: str, inst_id: str, start: datetime, end: datetime) -> pd.Series[float]:
    rows = conn.execute(
        select(FundingRate.ts, FundingRate.funding_rate)
        .where(
            FundingRate.env == env,
            FundingRate.inst_id == inst_id,
            FundingRate.ts >= start,
            FundingRate.ts < end,
        )
        .order_by(FundingRate.ts)
    ).all()
    index = pd.DatetimeIndex([r[0] for r in rows], tz=UTC) if rows else pd.DatetimeIndex([], tz=UTC)
    return pd.Series([float(r[1]) for r in rows], index=index, dtype="float64")


def load_inputs(conn: Connection, req: BacktestRequest, env: str, now: datetime) -> list[InstrumentInput]:
    """Candles (warm-up included), funding and fee per instrument, read from Postgres only."""
    step = bar_delta(req.bar)
    load_from = req.period.start.astimezone(UTC) - step * int(config()["warmup_bars"])
    end = req.period.end.astimezone(UTC)
    meta = {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            select(Instrument.inst_id, Instrument.inst_type, Instrument.asset_class).where(
                Instrument.env == env, Instrument.inst_id.in_(req.inst_ids)
            )
        ).all()
    }
    out: list[InstrumentInput] = []
    for inst_id in req.inst_ids:
        inst_type, asset_class = meta.get(inst_id, (None, None))
        fee, source = fee_for(inst_type)
        candles = load_candles(conn, inst_id, req.bar, load_from, end, as_of=now, env=env)
        funding = None
        if inst_type in FUNDING_INST_TYPES:
            series = _funding(conn, env, inst_id, load_from, end)
            funding = series if len(series) else None
        out.append(InstrumentInput(inst_id, inst_type, asset_class, fee, source, candles, funding))
    return out


def downsample(detail: RunDetail, max_points: int) -> list[dict[str, Any]]:
    """At most ``max_points`` evenly spaced points of the equity curve, first and last kept."""
    points = detail.equity_curve
    if len(points) > max_points:
        keep = np.unique(np.linspace(0, len(points) - 1, max_points).round().astype(int))
        points = [points[int(i)] for i in keep]
    return [p.model_dump(mode="json") for p in points]


def run_backtest(conn: Connection, req: BacktestRequest, env: str, now: datetime) -> dict[str, Any]:
    """Load, backtest and shape the 200 body. Raises :class:`BacktestRefused` for a 422."""
    inputs = load_inputs(conn, req, env, now)
    excluded = [
        {"inst_id": i.inst_id, "reason": f"no {req.bar} candles in {env} for the period"}
        for i in inputs
        if i.candles.df.empty
    ]
    usable = [i for i in inputs if not i.candles.df.empty]
    if not usable:
        raise BacktestRefused("no_data", f"no {req.bar} candles in {env} for {', '.join(req.inst_ids)}")

    period = (req.period.start.astimezone(UTC), req.period.end.astimezone(UTC))
    # One quant call per (fee, asset class): Costs carries one fee, and the asset class sets the
    # bars-per-year of CAGR/Sharpe. Each instrument's metrics are what a direct call gives it.
    groups: dict[tuple[float, str | None], list[InstrumentInput]] = {}
    for i in usable:
        groups.setdefault((i.taker_fee_rate, i.asset_class), []).append(i)
    by_inst: dict[str, dict[str, Any]] = {}
    splits = slippage = None
    for (fee, asset_class), members in groups.items():
        costs = Costs(
            taker_fee_rate=fee,
            slippage_bps=req.slippage_bps,
            funding={m.inst_id: m.funding for m in members if m.funding is not None},
        )
        try:
            detailed = backtest_rule_detailed(
                req.rule_spec,
                {m.inst_id: m.candles.df for m in members},
                period,
                costs,
                splits=req.splits,
                quality={m.inst_id: m.candles.quality for m in members},
                bar=req.bar,
                asset_class=asset_class,
            )
        except ValueError as exc:
            raise BacktestRefused("insufficient_data", str(exc)) from exc
        splits, slippage = detailed.result.splits, detailed.result.slippage_bps
        for inst in detailed.result.instruments:
            by_inst[inst.inst_id] = {"result": inst, "detail": detailed.details[inst.inst_id]}

    cfg = config()
    max_points, max_rows = int(cfg["equity_curve_max_points"]), int(cfg["trades_max_rows"])
    instruments: list[dict[str, Any]] = []
    for i in usable:
        inst = by_inst[i.inst_id]["result"]
        detail: RunDetail = by_inst[i.inst_id]["detail"]
        instruments.append(
            {
                **inst.model_dump(mode="json"),
                "inst_type": i.inst_type,
                "asset_class": i.asset_class,
                "taker_fee_rate": i.taker_fee_rate,
                "fee_source": i.fee_source,
                "funding_included": i.funding is not None,
                # Brief §7.3: a backtest over an unrepaired gap cannot support any claim.
                "verdict_cap": "does_not_support" if inst.data_gap else None,
                "data_quality": _quality(i.candles.quality),
                "equity_curve": downsample(detail, max_points),
                "equity_curve_points": len(detail.equity_curve),
                "trades": [t.model_dump(mode="json") for t in detail.trades[:max_rows]],
                "trades_total": len(detail.trades),
                "trades_truncated": len(detail.trades) > max_rows,
            }
        )
    data_as_of = max(i.candles.df.index[-1] for i in usable) + bar_delta(req.bar)
    return {
        "ok": True,
        "env": env,
        "rule": req.rule_spec.model_dump(mode="json"),
        "bar": req.bar,
        "period": {"start": period[0].isoformat(), "end": period[1].isoformat()},
        "splits": splits,
        "slippage_bps": slippage,
        "n_trades": sum(int(x["full"]["n_trades"]) for x in instruments),
        "data_gap": any(bool(x["data_gap"]) for x in instruments),
        "overfit_warning": any(bool(x["overfit_warning"]) for x in instruments),
        "instruments": instruments,
        "excluded": excluded,
        "data_as_of": min(data_as_of.to_pydatetime(), now).isoformat(),
    }


def _quality(q: DataQuality) -> dict[str, Any]:
    return q.model_dump(mode="json")


def _read_only_run(engine: Engine, req: BacktestRequest, env: str, now: datetime) -> dict[str, Any]:
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        try:
            return run_backtest(conn, req, env, now)
        finally:
            conn.rollback()


def _invalid(exc: ValidationError) -> JSONResponse:
    errors = json.loads(exc.json(include_url=False, include_input=False, include_context=False))
    first = errors[0] if errors else {"loc": [], "msg": "invalid body"}
    reason = f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}".lstrip(": ")
    return JSONResponse(
        status_code=422, content={"ok": False, "error": "invalid", "reason": reason, "errors": errors}
    )


def _refused(code: str, reason: str) -> JSONResponse:
    return JSONResponse(status_code=422, content={"ok": False, "error": code, "reason": reason})


@router.post("/backtest", response_model=None)
async def backtest(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    try:
        req = BacktestRequest.model_validate_json(await request.body())
    except ValidationError as exc:
        return _invalid(exc)
    cfg = config()
    bars, max_bars = period_bars(req), int(cfg["max_bars"])
    if bars > max_bars:
        return _refused(
            "too_many_bars",
            f"the period is {bars} {req.bar} bars per instrument; backtest.max_bars is {max_bars}",
        )
    engine: Engine = request.app.state.engine
    clock: Callable[[], datetime] = getattr(request.app.state, "clock", None) or (lambda: datetime.now(UTC))
    timeout = float(cfg["timeout_seconds"])
    try:
        body = await asyncio.wait_for(
            asyncio.to_thread(_read_only_run, engine, req, settings.env, clock()), timeout
        )
    except TimeoutError:
        logger.warning("backtest timed out", extra={"inst_ids": req.inst_ids, "bar": req.bar})
        return _refused("timeout", f"the backtest took longer than backtest.timeout_seconds ({timeout:g} s)")
    except BacktestRefused as exc:
        return _refused(exc.code, exc.reason)
    return JSONResponse(status_code=200, content=body)
