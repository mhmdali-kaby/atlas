"""Circuit breakers (brief §10, ATL-145): conditions that pause every new order until cleared.

One API, :class:`Breakers`: ``trip(name, reason, detail)`` and ``clear(name)``. Active breakers live
in ``trading_state.breakers`` (``{name: {"active", "since", "reason", "detail", "key", ...}}``); any
active one makes Risk Engine check 1 reject (``circuit_breaker_active``) and the Executor refuse
(``breaker_active``), because both read that column. Each trip publishes one
``system.circuit_breaker`` envelope on ``atlas.system`` and posts one War Room ``system_alert`` with
the plain reason; a clear publishes ``system.circuit_breaker`` with ``state: "cleared"`` and a short
War Room text line.

What trips what:

- ``daily_loss`` / ``weekly_loss`` / ``consecutive_losses``: the Risk Engine's check-6 figures
  (:func:`trading_core.risk.context.loss_figures`) against the Settings limits, every loop.
- ``okx_errors``: ``circuit_breakers.okx_errors.threshold`` errors from the trade client within
  ``window_seconds`` (fed by :func:`trading_core.okx.trade_read.add_error_hook`), counted in Redis.
- ``data_stale``: the newest ticker of a held or watchlist instrument is older than
  ``market_data_max_age_seconds``. The only breaker that clears itself: once every such instrument
  has been fresh for ``data_stale.clear_after_fresh_seconds`` (60 s).
- ``volatility_halt``: the newest closed 1m bar of a held instrument returned more than
  ``volatility_halt_sigma`` x sigma, sigma from the previous 24 h of 1m log returns.
- ``reconciliation_mismatch``: tripped by the reconciler with ``trip()``.
- ``agent_budget``: ``system.agent_state`` with ``reason: over_budget`` from the agents runtime.

Every breaker but ``data_stale`` stays until the owner resumes (``clear_all(by="owner")``, called by
the resume endpoint). A trip may carry a ``key`` naming its cause (the Baghdad day for daily_loss,
the newest losing close for consecutive_losses, the bar for volatility_halt, the agent and day for
agent_budget). After the owner clears it the same cause does not trip it again, so a resume is not
undone 15 s later by the loss that was already acknowledged; a new cause trips it as usual.

:class:`BreakerMonitor` runs :meth:`Breakers.evaluate` every ``evaluate_interval_seconds`` (15) and
reacts to events on ``atlas.system`` (agent budget) and ``atlas.market`` (1m candle closed,
market.stale / market.fresh).
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

import numpy as np
import redis
from atlas_quant.data import load_candles
from atlas_shared import bus
from atlas_shared.db import Bill, TradingState
from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from atlas_shared.market_data import read_snapshot
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from trading_core.okx.errors import OkxClientError, OkxError
from trading_core.okx.trade_read import OkxTradeError, add_error_hook, remove_error_hook
from trading_core.risk.context import LossFigures, ensure_trading_state, loss_figures
from trading_core.risk.rules import EngineSettings, check_loss_limits
from trading_core.ws.subscriptions import held_instruments, watchlist_instruments

log = logging.getLogger("trading_core.risk.breakers")

DAILY_LOSS: Final = "daily_loss"
WEEKLY_LOSS: Final = "weekly_loss"
CONSECUTIVE_LOSSES: Final = "consecutive_losses"
OKX_ERRORS: Final = "okx_errors"
DATA_STALE: Final = "data_stale"
VOLATILITY_HALT: Final = "volatility_halt"
RECONCILIATION_MISMATCH: Final = "reconciliation_mismatch"
AGENT_BUDGET: Final = "agent_budget"

NAMES: Final = (
    DAILY_LOSS,
    WEEKLY_LOSS,
    CONSECUTIVE_LOSSES,
    OKX_ERRORS,
    DATA_STALE,
    VOLATILITY_HALT,
    RECONCILIATION_MISMATCH,
    AGENT_BUDGET,
)
SELF_CLEARING: Final = frozenset({DATA_STALE})

LABELS: Final[Mapping[str, str]] = {
    DAILY_LOSS: "Daily loss limit",
    WEEKLY_LOSS: "Weekly loss limit",
    CONSECUTIVE_LOSSES: "Consecutive losses",
    OKX_ERRORS: "OKX errors",
    DATA_STALE: "Stale market data",
    VOLATILITY_HALT: "Volatility halt",
    RECONCILIATION_MISMATCH: "Reconciliation mismatch",
    AGENT_BUDGET: "Agent budget exceeded",
}

SYSTEM_STREAM: Final = "atlas.system"
MARKET_STREAM: Final = "atlas.market"
WARROOM_STREAM: Final = "atlas.warroom"
EVENT_TYPE: Final = "system.circuit_breaker"
SOURCE: Final = "service:trading_core.breakers"
WARROOM_SOURCE: Final = "system:circuit_breaker"
WARROOM_AUTHOR: Final = "circuit_breaker"


def breaker_config() -> Mapping[str, Any]:
    value: Mapping[str, Any] = DEFAULTS["circuit_breakers"]
    return value


def okx_errors_key(env: str) -> str:
    return f"atlas:breakers:{env}:okx_errors"


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    return value


def _fmt_pct(value: Decimal) -> str:
    return f"{value:.2f}"


class Breakers:
    """Trip, clear and evaluate the circuit breakers of one env.

    ``now`` is a zero-arg clock (tests pass a fixed one). Every write is one short transaction that
    locks the env's trading_state row, so a concurrent trip and clear cannot lose each other.
    """

    def __init__(
        self,
        engine: Engine,
        env: str,
        *,
        redis_client: redis.Redis | None = None,
        now: Callable[[], datetime] | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self.engine = engine
        self.env = env
        self.rds = redis_client if redis_client is not None else bus.make_client()
        self._now = now or (lambda: datetime.now(UTC))
        self.cfg = config or breaker_config()

    # -- state -------------------------------------------------------------------------------------

    def _locked_state(self, session: Session, at: datetime) -> TradingState:
        ensure_trading_state(session, self.env, at)
        row = session.scalar(
            select(TradingState)
            .where(TradingState.env == self.env)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert row is not None
        return row

    def entries(self) -> dict[str, dict[str, Any]]:
        """Every breaker entry stored for the env, active or cleared."""
        with Session(self.engine) as s:
            raw = s.scalar(select(TradingState.breakers).where(TradingState.env == self.env))
        if not isinstance(raw, dict):
            return {}
        return {k: dict(v) for k, v in raw.items() if isinstance(v, dict)}

    def active(self) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in self.entries().items() if v.get("active")}

    def _update(self, name: str, **fields: Any) -> None:
        at = self._now()
        with Session(self.engine) as s, s.begin():
            state = self._locked_state(s, at)
            breakers = dict(state.breakers) if isinstance(state.breakers, dict) else {}
            entry = dict(breakers.get(name) or {})
            entry.update(_json_safe(fields))
            breakers[name] = entry
            state.breakers = breakers
            state.updated_at = at

    # -- the API -----------------------------------------------------------------------------------

    def trip(
        self, name: str, reason: str, detail: Mapping[str, Any] | None = None, *, key: str | None = None
    ) -> bool:
        """Activate ``name``. Returns False (and announces nothing) when it is already active, or
        when ``key`` is the cause it was last tripped for (that cause was already acknowledged)."""
        if name not in NAMES:
            raise ValueError(f"unknown circuit breaker {name!r}; expected one of {NAMES}")
        at = self._now()
        with Session(self.engine) as s, s.begin():
            state = self._locked_state(s, at)
            breakers = dict(state.breakers) if isinstance(state.breakers, dict) else {}
            current = breakers.get(name) if isinstance(breakers.get(name), dict) else {}
            assert isinstance(current, dict)
            if current.get("active"):
                return False
            if key is not None and current.get("key") == key:
                return False
            entry: dict[str, Any] = {
                "active": True,
                "since": _iso(at),
                "reason": reason,
                "detail": _json_safe(dict(detail or {})),
                "key": key,
                "self_clearing": name in SELF_CLEARING,
                "fresh_since": None,
            }
            breakers[name] = entry
            state.breakers = breakers
            state.updated_at = at
        log.warning("circuit breaker tripped", extra={"breaker": name, "env": self.env, "reason": reason})
        self._announce_trip(name, entry)
        return True

    def clear(self, name: str, *, by: str = "owner") -> bool:
        """Deactivate ``name``. Returns False when it was not active. Clearing ``okx_errors`` also
        forgets the counted errors, so the next error starts a new window."""
        if name not in NAMES:
            raise ValueError(f"unknown circuit breaker {name!r}; expected one of {NAMES}")
        at = self._now()
        with Session(self.engine) as s, s.begin():
            state = self._locked_state(s, at)
            breakers = dict(state.breakers) if isinstance(state.breakers, dict) else {}
            current = breakers.get(name)
            if not isinstance(current, dict) or not current.get("active"):
                return False
            entry = {**current, "active": False, "cleared_at": _iso(at), "cleared_by": by}
            breakers[name] = entry
            state.breakers = breakers
            state.updated_at = at
        if name == OKX_ERRORS:
            try:
                self.rds.delete(okx_errors_key(self.env))
            except redis.RedisError:
                log.warning("could not reset the OKX error counter")
        log.info("circuit breaker cleared", extra={"breaker": name, "env": self.env, "by": by})
        self._announce_clear(name, entry, by)
        return True

    def clear_all(self, *, by: str = "owner") -> list[str]:
        """Clear every active breaker (the owner's resume). Returns the names cleared."""
        return [name for name in sorted(self.active()) if self.clear(name, by=by)]

    # -- announcements -----------------------------------------------------------------------------

    def _publish(self, stream: str, type_: str, payload: dict[str, Any], source: str) -> None:
        envelope = EventEnvelope.model_validate(
            {"type": type_, "env": self.env, "source": source, "payload": payload}
        )
        try:
            bus.publish(stream, envelope, client=self.rds)
        except redis.RedisError:
            log.warning("event publish failed", extra={"type": type_, "stream": stream})

    def _warroom(self, kind: str, content_md: str, card: dict[str, Any] | None) -> None:
        self._publish(
            WARROOM_STREAM,
            "warroom.message",
            {
                "thread": {"kind": "main", "key": "main"},
                "author_type": "system",
                "author": WARROOM_AUTHOR,
                "kind": kind,
                "content_md": content_md,
                "card": card,
                "reply_to": None,
                "mentions": [],
                "reasoning_md": None,
                "tool_calls": None,
                "tokens_in": None,
                "tokens_out": None,
                "cost_usd": None,
            },
            WARROOM_SOURCE,
        )

    def _payload(self, name: str, entry: Mapping[str, Any], state: str) -> dict[str, Any]:
        return {
            "name": name,
            "label": LABELS[name],
            "state": state,
            "reason": entry.get("reason"),
            "detail": entry.get("detail") or {},
            "since": entry.get("since"),
            "self_clearing": name in SELF_CLEARING,
            "cleared_at": entry.get("cleared_at"),
            "cleared_by": entry.get("cleared_by"),
        }

    def _announce_trip(self, name: str, entry: Mapping[str, Any]) -> None:
        payload = self._payload(name, entry, "tripped")
        self._publish(SYSTEM_STREAM, EVENT_TYPE, payload, SOURCE)
        if name in SELF_CLEARING:
            until = (
                f"It clears itself once data has been fresh for "
                f"{int(self.cfg['data_stale']['clear_after_fresh_seconds'])} s."
            )
        else:
            until = "It stays on until you resume trading."
        content = (
            f"**Circuit breaker: {LABELS[name]}** (`{name}`). {entry.get('reason')} "
            f"New orders are paused. {until}"
        )
        self._warroom("system_alert", content, {"type": "circuit_breaker", "code": name, **payload})

    def _announce_clear(self, name: str, entry: Mapping[str, Any], by: str) -> None:
        self._publish(SYSTEM_STREAM, EVENT_TYPE, self._payload(name, entry, "cleared"), SOURCE)
        why = "market data has been fresh again" if by == "system" else f"cleared by the {by}"
        self._warroom("text", f"Circuit breaker {LABELS[name]} (`{name}`) is off: {why}.", None)

    # -- OKX errors (the trade client's hook) -------------------------------------------------------

    def okx_error_hook(self, env: str, error: OkxClientError) -> None:
        """Count one trade-client error; trip ``okx_errors`` at the threshold within the window."""
        if env != self.env:
            return
        cfg = self.cfg["okx_errors"]
        code = error.code if isinstance(error, OkxError) else type(error).__name__
        if code in {str(c) for c in cfg["ignored_codes"]}:
            return
        at = self._now()
        window = int(cfg["window_seconds"])
        now_ms = int(at.timestamp() * 1000)
        key = okx_errors_key(self.env)
        try:
            pipe = self.rds.pipeline()
            pipe.zadd(key, {f"{now_ms}:{uuid.uuid4().hex[:8]}:{code}": now_ms})
            pipe.zremrangebyscore(key, "-inf", now_ms - window * 1000)
            pipe.zcard(key)
            pipe.expire(key, window)
            count = int(pipe.execute()[2])
        except redis.RedisError:
            log.warning("could not count an OKX error")
            return
        if count < int(cfg["threshold"]):
            return
        message = error.human_message if isinstance(error, OkxTradeError) else str(error)
        self.trip(
            OKX_ERRORS,
            f"{count} OKX errors in the last {window // 60} minutes; the latest: {message}",
            {
                "count": count,
                "window_seconds": window,
                "last_code": code,
                "last_path": getattr(error, "path", None),
                "last_message": message,
            },
        )

    # -- evaluation --------------------------------------------------------------------------------

    def _settings(self) -> tuple[EngineSettings, Decimal]:
        with self.engine.connect() as c:
            row = c.execute(text("SELECT data FROM settings WHERE env = :env"), {"env": self.env}).first()
        data: Mapping[str, Any] = row[0] if row is not None and isinstance(row[0], dict) else {}
        sigma = Decimal(
            str(data.get("volatility_halt_sigma", DEFAULTS["risk_engine"]["volatility_halt_sigma"]))
        )
        return EngineSettings.from_settings_data(data), sigma

    def evaluate(self) -> list[str]:
        """One pass of the loop: loss limits, stale data, volatility. Returns the names tripped."""
        at = self._now()
        settings, sigma = self._settings()
        with Session(self.engine) as s:
            losses = loss_figures(s, self.env, at)
            newest_loss = self._newest_losing_close(s)
        with self.engine.connect() as c:
            held = held_instruments(c, self.env)
            watched = watchlist_instruments(c, self.env, at)
        tripped = self.check_losses(losses, settings, newest_loss)
        tripped += self.check_stale(held | watched, settings)
        tripped += self.check_volatility(held, sigma)
        return tripped

    def _newest_losing_close(self, session: Session) -> datetime | None:
        return session.scalar(
            select(Bill.ts)
            .where(Bill.env == self.env, Bill.type == "2", Bill.pnl.is_not(None), Bill.pnl < 0)
            .order_by(Bill.ts.desc())
            .limit(1)
        )

    def check_losses(
        self, losses: LossFigures, settings: EngineSettings, newest_loss: datetime | None
    ) -> list[str]:
        values = check_loss_limits(
            day_loss_usd=losses.day_loss_usd,
            day_base_usd=losses.day_base_usd,
            week_loss_usd=losses.week_loss_usd,
            week_base_usd=losses.week_base_usd,
            consecutive_losses=losses.consecutive_losses,
            settings=settings,
        ).values
        day_pct, week_pct = Decimal(values["day_loss_pct"]), Decimal(values["week_loss_pct"])
        out: list[str] = []
        if day_pct >= settings.max_daily_loss_pct and self.trip(
            DAILY_LOSS,
            f"Today's loss is {_fmt_pct(day_pct)}% of equity; the limit is {settings.max_daily_loss_pct}%.",
            {
                "loss_usd": losses.day_loss_usd,
                "loss_pct": day_pct,
                "limit_pct": settings.max_daily_loss_pct,
                "since": losses.day_start,
            },
            key=f"day:{_iso(losses.day_start)}",
        ):
            out.append(DAILY_LOSS)
        if week_pct >= settings.max_weekly_loss_pct and self.trip(
            WEEKLY_LOSS,
            f"This week's loss is {_fmt_pct(week_pct)}% of equity; the limit is "
            f"{settings.max_weekly_loss_pct}%.",
            {
                "loss_usd": losses.week_loss_usd,
                "loss_pct": week_pct,
                "limit_pct": settings.max_weekly_loss_pct,
                "since": losses.week_start,
            },
            key=f"week:{_iso(losses.week_start)}",
        ):
            out.append(WEEKLY_LOSS)
        if losses.consecutive_losses >= settings.max_consecutive_losses and self.trip(
            CONSECUTIVE_LOSSES,
            f"{losses.consecutive_losses} losing trades in a row; the limit is "
            f"{settings.max_consecutive_losses}.",
            {
                "consecutive_losses": losses.consecutive_losses,
                "limit": settings.max_consecutive_losses,
                "newest_loss_at": newest_loss,
            },
            key=f"loss:{_iso(newest_loss)}" if newest_loss is not None else None,
        ):
            out.append(CONSECUTIVE_LOSSES)
        return out

    def check_stale(self, instruments: set[str], settings: EngineSettings) -> list[str]:
        """Trip on any stale held/watchlist instrument; clear after the data has been fresh long
        enough. With no held or watchlist instrument nothing can be stale."""
        at = self._now()
        max_age = settings.market_data_max_age_seconds
        stale: dict[str, float | None] = {}
        for inst in sorted(instruments):
            ticker_at = read_snapshot(self.rds, self.env, inst).channel_at.get("tickers")
            age = None if ticker_at is None else (at - ticker_at).total_seconds()
            if age is None or age > max_age:
                stale[inst] = None if age is None else round(age, 1)
        entry = self.entries().get(DATA_STALE, {})
        if stale:
            if entry.get("active"):
                if entry.get("fresh_since") is not None:
                    self._update(DATA_STALE, fresh_since=None)
                return []
            names = ", ".join(sorted(stale))
            tripped = self.trip(
                DATA_STALE,
                f"No market data for more than {max_age} s on {names}.",
                {"instruments": stale, "max_age_seconds": max_age},
            )
            return [DATA_STALE] if tripped else []
        if entry.get("active"):
            fresh_since = _parse(entry.get("fresh_since"))
            hold = float(self.cfg["data_stale"]["clear_after_fresh_seconds"])
            if fresh_since is None:
                self._update(DATA_STALE, fresh_since=at)
            elif (at - fresh_since).total_seconds() >= hold:
                self.clear(DATA_STALE, by="system")
        return []

    def one_minute_move(self, inst_id: str) -> dict[str, Any] | None:
        """The newest closed 1m bar's log return and sigma of the 1m returns before it, or None when
        there is not enough recent, gap-free data to judge."""
        cfg = self.cfg["volatility"]
        at = self._now()
        bar = str(cfg["bar"])
        step = timedelta(minutes=1)
        start = at - timedelta(hours=int(cfg["lookback_hours"])) - 2 * step
        with Session(self.engine) as s:
            df = load_candles(s, inst_id, bar, start, at, as_of=at, env=self.env).df
        if len(df) < int(cfg["min_returns"]) + 2:
            return None
        last_ts = df.index[-1].to_pydatetime()
        if (at - (last_ts + step)).total_seconds() > float(cfg["max_bar_age_seconds"]):
            return None
        if df.index[-1] - df.index[-2] != step:
            return None
        closes = df["c"].to_numpy(dtype=float)
        if np.any(~np.isfinite(closes)) or np.any(closes <= 0):
            return None
        rets = np.diff(np.log(closes))
        base = rets[:-1]
        if len(base) < int(cfg["min_returns"]):
            return None
        sigma = float(np.std(base, ddof=1))
        if not math.isfinite(sigma) or sigma <= 0:
            return None
        last = float(rets[-1])
        return {"return": last, "sigma": sigma, "z": abs(last) / sigma, "bar_ts": last_ts, "n": len(base)}

    def check_volatility(self, held: set[str], sigma_limit: Decimal) -> list[str]:
        """Trip ``volatility_halt`` when a held instrument's newest 1m move exceeds the limit."""
        for inst in sorted(held):
            move = self.one_minute_move(inst)
            if move is None or move["z"] <= float(sigma_limit):
                continue
            pct = (math.exp(move["return"]) - 1) * 100
            if self.trip(
                VOLATILITY_HALT,
                f"{inst} moved {pct:+.2f}% in one minute, {move['z']:.1f} sigma; the limit is "
                f"{sigma_limit} sigma.",
                {
                    "inst_id": inst,
                    "return_pct": round(pct, 4),
                    "z": round(move["z"], 2),
                    "sigma_pct": round(move["sigma"] * 100, 4),
                    "limit_sigma": sigma_limit,
                    "bar_ts": move["bar_ts"],
                    "n_returns": move["n"],
                },
                key=f"{inst}:{_iso(move['bar_ts'])}",
            ):
                return [VOLATILITY_HALT]
        return []

    # -- events ------------------------------------------------------------------------------------

    def on_event(self, stream: str, envelope: EventEnvelope) -> list[str]:
        """React to one bus event. Returns the names tripped."""
        if envelope.env != self.env:
            return []
        payload = envelope.payload
        if stream == SYSTEM_STREAM and envelope.type == "system.agent_state":
            if payload.get("reason") != "over_budget":
                return []
            agent = str(payload.get("agent") or "unknown")
            day = self._now().astimezone(ZoneInfo(str(DEFAULTS["timezone"]["display"]))).date()
            tripped = self.trip(
                AGENT_BUDGET,
                f"The {agent} agent reached its daily budget"
                + (f": {payload['last_error']}" if payload.get("last_error") else "."),
                {"agent": agent, "state": payload.get("state"), "last_error": payload.get("last_error")},
                key=f"{agent}:{day.isoformat()}",
            )
            return [AGENT_BUDGET] if tripped else []
        if stream != MARKET_STREAM:
            return []
        if envelope.type == "candle.closed" and payload.get("bar") == self.cfg["volatility"]["bar"]:
            inst = payload.get("inst_id")
            with self.engine.connect() as c:
                held = held_instruments(c, self.env)
            if isinstance(inst, str) and inst in held:
                return self.check_volatility({inst}, self._settings()[1])
            return []
        if envelope.type in ("market.stale", "market.fresh"):
            at = self._now()
            settings, _ = self._settings()
            with self.engine.connect() as c:
                wanted = held_instruments(c, self.env) | watchlist_instruments(c, self.env, at)
            return self.check_stale(wanted, settings)
        return []


class BreakerMonitor:
    """Runs :meth:`Breakers.evaluate` every ``evaluate_interval_seconds``, feeds it bus events and
    registers the OKX trade-client error hook while running."""

    def __init__(self, breakers: Breakers, *, interval_seconds: float | None = None) -> None:
        self.breakers = breakers
        cfg = breakers.cfg
        self.interval = float(interval_seconds or cfg["evaluate_interval_seconds"])
        self.block_ms = int(cfg["stream_block_ms"])
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self._tasks)

    async def start(self) -> None:
        if self.running:
            return
        add_error_hook(self.breakers.okx_error_hook)
        self._tasks = [
            asyncio.create_task(self._loop(), name="breakers:loop"),
            asyncio.create_task(self._consume(), name="breakers:events"),
        ]

    async def stop(self) -> None:
        remove_error_hook(self.breakers.okx_error_hook)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.breakers.evaluate)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("circuit breaker evaluation failed")
            await asyncio.sleep(self.interval)

    async def _consume(self) -> None:
        last: dict[str, str] = {SYSTEM_STREAM: "$", MARKET_STREAM: "$"}
        rds = self.breakers.rds
        while True:
            try:
                rows = cast(
                    "list[tuple[str, list[tuple[str, dict[str, str]]]]] | None",
                    await asyncio.to_thread(rds.xread, last, count=100, block=self.block_ms),  # type: ignore[arg-type]
                )
            except asyncio.CancelledError:
                raise
            except redis.RedisError:
                log.warning("circuit breaker event read failed")
                await asyncio.sleep(self.interval)
                continue
            for stream, entries in rows or []:
                for entry_id, fields in entries:
                    last[stream] = entry_id
                    raw = fields.get(bus.FIELD)
                    if raw is None:
                        continue
                    try:
                        envelope = EventEnvelope.model_validate_json(raw)
                        await asyncio.to_thread(self.breakers.on_event, stream, envelope)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("circuit breaker event failed", extra={"stream": stream})


__all__ = [
    "AGENT_BUDGET",
    "CONSECUTIVE_LOSSES",
    "DAILY_LOSS",
    "DATA_STALE",
    "LABELS",
    "NAMES",
    "OKX_ERRORS",
    "RECONCILIATION_MISMATCH",
    "SELF_CLEARING",
    "VOLATILITY_HALT",
    "WEEKLY_LOSS",
    "BreakerMonitor",
    "Breakers",
    "okx_errors_key",
]
