"""Brief §10 checks 1, 2, 5, 6, 7, 8 and 9 as pure functions (checks 3 and 4 are validity.py and
sizing.py). No I/O: :mod:`trading_core.risk.engine` loads the inputs and runs them in order.

Every check returns a :class:`CheckResult`; ``as_check(n)`` is its ``risk_checks.checks`` entry.
Money and prices are ``Decimal``; percentages are in percent, as in the settings table.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from atlas_shared.defaults import DEFAULTS

from trading_core.risk.inputs import ContractType, Side

HUNDRED = Decimal(100)
ZERO = Decimal(0)

Decision = Literal["send_now", "awaiting_owner", "advise_only"]
Market = Literal["spot", "crypto_perp", "stock_perp", "commodity_perp", "futures", "options", "other"]

_STOCK_CLASSES = frozenset({"stock", "etf", "index"})


def rules_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["risk_rules"]
    return config


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    code: str | None
    reason: str
    values: dict[str, Any] = field(default_factory=dict)

    def as_check(self, n: int) -> dict[str, Any]:
        return {
            "n": n,
            "check": self.name,
            "status": "passed" if self.passed else "failed",
            "passed": self.passed,
            "code": self.code,
            "detail": self.reason,
            "values": _jsonable(self.values),
        }


def not_run(n: int, name: str) -> dict[str, Any]:
    """The entry for a check after the first hard failure."""
    return {
        "n": n,
        "check": name,
        "status": "not_run",
        "passed": None,
        "code": None,
        "detail": "Not run: an earlier check rejected the proposal.",
        "values": {},
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


# --- Settings the engine reads beyond RiskSettings ----------------------------------------------


def _dec(raw: Any, key: str) -> Decimal:
    if isinstance(raw, float):
        raise TypeError(f"settings {key} is a float; store it as a decimal string")
    if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
        raise TypeError(f"settings {key} has type {type(raw).__name__}")
    return Decimal(raw)


def _int(raw: Any, key: str) -> int:
    if isinstance(raw, bool) or isinstance(raw, float):
        raise TypeError(f"settings {key} must be an integer")
    return int(Decimal(str(raw)))


@dataclass(frozen=True, slots=True)
class EngineSettings:
    """Owner limits for checks 1, 2, 5, 6, 7, 8 and 9 from one settings version (rule 2)."""

    market_data_max_age_seconds: int
    enabled_markets: frozenset[str]
    blocklist: frozenset[str]  # upper-case
    whitelist: frozenset[str]  # upper-case; empty = no restriction (decision #250)
    avoid_stock_perps_outside_us_hours: bool
    max_open_positions: int
    max_correlated_exposure_pct: Decimal
    max_trades_per_day: int
    max_daily_loss_pct: Decimal
    max_weekly_loss_pct: Decimal
    max_consecutive_losses: int
    liq_buffer_mult: Decimal
    event_blackout_minutes: int
    autonomy_level: int
    bot_capital_cap_usdt: Decimal | None = None  # None = no cap (decision #250)

    @classmethod
    def from_settings_data(cls, data: Mapping[str, Any]) -> EngineSettings:
        """Missing keys fall back to DEFAULTS; profile fields to the row's risk_profile preset."""
        profiles: dict[str, Any] = DEFAULTS["risk_profiles"]
        preset: dict[str, Any] = profiles.get(
            data.get("risk_profile", profiles["default"]), profiles[profiles["default"]]
        )
        engine: dict[str, Any] = DEFAULTS["risk_engine"]
        markets: dict[str, Any] = DEFAULTS["markets"]

        def get(key: str, fallback: Mapping[str, Any], default_key: str | None = None) -> Any:
            return data[key] if key in data else fallback[default_key or key]

        avoid = get("avoid_stock_perps_outside_us_hours", markets)
        if not isinstance(avoid, bool):
            raise TypeError("settings avoid_stock_perps_outside_us_hours must be a boolean")
        return cls(
            market_data_max_age_seconds=_int(
                get("market_data_max_age_seconds", engine), "market_data_max_age_seconds"
            ),
            enabled_markets=frozenset(str(m) for m in get("enabled_markets", markets)),
            blocklist=frozenset(str(i).upper() for i in get("blocklist", markets)),
            whitelist=frozenset(str(i).upper() for i in (get("whitelist", markets) or [])),
            avoid_stock_perps_outside_us_hours=avoid,
            max_open_positions=_int(get("max_open_positions", preset), "max_open_positions"),
            max_correlated_exposure_pct=_dec(
                get("max_correlated_exposure_pct", preset), "max_correlated_exposure_pct"
            ),
            max_trades_per_day=_int(
                get("max_trades_per_day", DEFAULTS["money_limits"]), "max_trades_per_day"
            ),
            max_daily_loss_pct=_dec(get("max_daily_loss_pct", preset), "max_daily_loss_pct"),
            max_weekly_loss_pct=_dec(get("max_weekly_loss_pct", preset), "max_weekly_loss_pct"),
            max_consecutive_losses=_int(get("max_consecutive_losses", preset), "max_consecutive_losses"),
            liq_buffer_mult=_dec(get("liq_buffer_mult", engine), "liq_buffer_mult"),
            event_blackout_minutes=_int(
                get("block_new_trades_minutes_before_high_impact_event", engine),
                "block_new_trades_minutes_before_high_impact_event",
            ),
            autonomy_level=_int(
                get("autonomy_level", DEFAULTS["autonomy"], "default_level"), "autonomy_level"
            ),
            bot_capital_cap_usdt=_cap(get("bot_capital_cap_usdt", DEFAULTS["money_limits"])),
        )


def _cap(raw: Any) -> Decimal | None:
    """bot_capital_cap_usdt: JSON null (or a non-positive value) means no cap."""
    if raw is None:
        return None
    cap = _dec(raw, "bot_capital_cap_usdt")
    return cap if cap > 0 else None


def sizing_equity(equity: Decimal, cap: Decimal | None) -> tuple[Decimal, bool]:
    """Decision #250: the base for every percentage limit is min(account equity, bot capital cap);
    with no cap it is the account equity. The flag is true only when the cap is below equity."""
    if cap is not None and cap < equity:
        return cap, True
    return equity, False


# --- Check 1: environment and kill switch -------------------------------------------------------


def check_environment(
    *,
    paused: bool,
    pause_reason: str | None,
    active_breakers: Sequence[str],
    connection_verified: bool,
    env: str,
    ticker_at: datetime | None,
    now: datetime,
    max_age_seconds: int,
) -> CheckResult:
    name = "env_and_kill_switch"
    age = None if ticker_at is None else (now - ticker_at).total_seconds()
    values: dict[str, Any] = {
        "env": env,
        "paused": paused,
        "active_breakers": list(active_breakers),
        "connection_verified": connection_verified,
        "market_data_age_seconds": None if age is None else round(age, 3),
        "market_data_max_age_seconds": max_age_seconds,
    }
    if paused:
        return CheckResult(
            name,
            False,
            "trading_paused",
            f"Trading is paused{': ' + pause_reason if pause_reason else ''}.",
            values,
        )
    if active_breakers:
        return CheckResult(
            name,
            False,
            "circuit_breaker_active",
            f"Circuit breaker active: {', '.join(active_breakers)}.",
            values,
        )
    if not connection_verified:
        return CheckResult(
            name, False, "connection_not_verified", f"There is no verified OKX connection for {env}.", values
        )
    if age is None or age > max_age_seconds:
        what = "no ticker received" if age is None else f"last ticker {age:.1f} s ago"
        return CheckResult(
            name,
            False,
            "market_data_stale",
            f"Market data is stale ({what}; limit {max_age_seconds} s).",
            values,
        )
    return CheckResult(name, True, None, "Trading is on and market data is fresh.", values)


# --- Check 2: instrument allowed ---------------------------------------------------------------


def market_of(inst_type: str, asset_class: str) -> Market:
    """The Settings -> Markets toggle an instrument falls under (same rule as the Market Scout)."""
    kind = inst_type.upper()
    if kind == "SPOT":
        return "spot"
    if kind == "FUTURES":
        return "futures"
    if kind == "OPTION":
        return "options"
    if kind == "SWAP":
        if asset_class == "crypto":
            return "crypto_perp"
        if asset_class in _STOCK_CLASSES:
            return "stock_perp"
        if asset_class == "commodity":
            return "commodity_perp"
    return "other"


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def us_session_open(now: datetime, config: Mapping[str, Any] | None = None) -> bool:
    """True inside the US cash session, in the session's own timezone (DST from the tz database)."""
    session = (config or rules_config())["us_session"]
    local = now.astimezone(ZoneInfo(str(session["timezone"])))
    if local.isoweekday() not in {int(d) for d in session["weekdays"]}:
        return False
    return _hhmm(str(session["open"])) <= local.time() < _hhmm(str(session["close"]))


def check_instrument(
    *,
    inst_id: str,
    found: bool,
    inst_type: str | None,
    asset_class: str | None,
    state: str | None,
    settings: EngineSettings,
    now: datetime,
    config: Mapping[str, Any] | None = None,
) -> CheckResult:
    name = "instrument_allowed"
    if not found or inst_type is None:
        return CheckResult(
            name,
            False,
            "instrument_unknown",
            f"{inst_id} is not in this env's universe.",
            {"inst_id": inst_id},
        )
    market = market_of(inst_type, asset_class or "other")
    values: dict[str, Any] = {"inst_id": inst_id, "market": market, "state": state}
    if market not in settings.enabled_markets:
        return CheckResult(
            name, False, "market_disabled", f"The {market} market is turned off in Settings.", values
        )
    if inst_id.upper() in settings.blocklist:
        return CheckResult(name, False, "blocklisted", f"{inst_id} is on the blocklist.", values)
    if settings.whitelist and inst_id.upper() not in settings.whitelist:
        return CheckResult(
            name,
            False,
            "not_whitelisted",
            f"{inst_id} is not on the Markets whitelist; only listed instruments can be traded.",
            values,
        )
    if state != "live":
        return CheckResult(
            name, False, "instrument_not_live", f"{inst_id} state is {state}, not live.", values
        )
    if market == "stock_perp" and settings.avoid_stock_perps_outside_us_hours:
        cfg = (config or rules_config())["us_session"]
        local = now.astimezone(ZoneInfo(str(cfg["timezone"])))
        values["us_local_time"] = local.isoformat()
        if not us_session_open(now, config):
            return CheckResult(
                name,
                False,
                "outside_us_hours",
                f"Stock perps are avoided outside US hours ({cfg['open']}-{cfg['close']} "
                f"{cfg['timezone']}); it is {local:%a %H:%M} there.",
                values,
            )
    return CheckResult(name, True, None, f"{inst_id} is allowed.", values)


# --- Check 5: portfolio ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenPosition:
    inst_id: str
    direction: int  # +1 long, -1 short
    notional_usd: Decimal  # absolute
    asset_class: str


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def correlation(a: Mapping[datetime, float], b: Mapping[datetime, float], min_obs: int) -> float | None:
    """Pearson correlation of two return series on their common timestamps; None below min_obs."""
    common = sorted(set(a) & set(b))
    if len(common) < min_obs:
        return None
    return pearson([a[t] for t in common], [b[t] for t in common])


def daily_log_returns(closes: Iterable[tuple[datetime, Decimal]]) -> dict[datetime, float]:
    series = sorted(closes)
    out: dict[datetime, float] = {}
    for (prev_ts, prev), (ts, cur) in itertools.pairwise(series):
        if prev > 0 and cur > 0 and ts - prev_ts == timedelta(days=1):
            out[ts] = math.log(float(cur) / float(prev))
    return out


def check_portfolio(
    *,
    inst_id: str,
    direction: int,
    notional_usd: Decimal,
    asset_class: str,
    positions: Sequence[OpenPosition],
    correlations: Mapping[str, float | None],
    equity: Decimal,
    trades_today: int,
    settings: EngineSettings,
    threshold: Decimal,
) -> CheckResult:
    """Trades today < max; open positions < max; same-direction exposure in instruments with
    30-day corr > threshold (the instrument itself included) and the asset class's net exposure,
    each with the new trade, <= max_correlated_exposure_pct of ``equity``, the sizing equity
    (decision #250: min(account equity, bot capital cap))."""
    name = "portfolio"
    cap = equity * settings.max_correlated_exposure_pct / HUNDRED
    held = {p.inst_id for p in positions}
    correlated = [
        p
        for p in positions
        if p.direction == direction
        and (
            p.inst_id == inst_id
            or (correlations.get(p.inst_id) is not None and Decimal(str(correlations[p.inst_id])) > threshold)
        )
    ]
    correlated_usd = sum((p.notional_usd for p in correlated), ZERO) + notional_usd
    net_class = (
        sum((p.direction * p.notional_usd for p in positions if p.asset_class == asset_class), ZERO)
        + direction * notional_usd
    )
    values: dict[str, Any] = {
        "trades_today": trades_today,
        "max_trades_per_day": settings.max_trades_per_day,
        "open_positions": len(held),
        "max_open_positions": settings.max_open_positions,
        "correlated_with": sorted({p.inst_id for p in correlated}),
        "correlation_unknown": sorted(k for k, v in correlations.items() if v is None),
        "correlations": {k: (None if v is None else round(v, 4)) for k, v in correlations.items()},
        "correlated_exposure_usd": correlated_usd,
        "asset_class": asset_class,
        "asset_class_net_exposure_usd": net_class,
        "exposure_cap_usd": cap,
        "sizing_equity": equity,
    }
    if trades_today >= settings.max_trades_per_day:
        return CheckResult(
            name,
            False,
            "max_trades_per_day",
            f"{trades_today} trades today; the limit is {settings.max_trades_per_day}.",
            values,
        )
    if inst_id not in held and len(held) >= settings.max_open_positions:
        return CheckResult(
            name,
            False,
            "max_open_positions",
            f"{len(held)} positions are open; the limit is {settings.max_open_positions}.",
            values,
        )
    if correlated_usd > cap:
        return CheckResult(
            name,
            False,
            "correlated_exposure",
            f"Correlated exposure would be {correlated_usd:.2f} USD, above the cap "
            f"{cap:.2f} USD ({settings.max_correlated_exposure_pct}% of sizing equity {equity:.2f} USD).",
            values,
        )
    if abs(net_class) > cap:
        return CheckResult(
            name,
            False,
            "asset_class_exposure",
            f"Net {asset_class} exposure would be {net_class:.2f} USD, above the cap {cap:.2f} USD.",
            values,
        )
    return CheckResult(name, True, None, "Portfolio limits hold with this trade.", values)


# --- Check 6: loss limits ----------------------------------------------------------------------


def check_loss_limits(
    *,
    day_loss_usd: Decimal,
    day_base_usd: Decimal | None,
    week_loss_usd: Decimal,
    week_base_usd: Decimal | None,
    consecutive_losses: int,
    settings: EngineSettings,
) -> CheckResult:
    """Loss = start equity + net deposits since then - current equity (realised + unrealised), as %
    of the period's sizing equity: min(start equity, bot capital cap) (decision #250), so a 300 USD
    loss with a 10,000 USD cap is 3% however large the account is. At or above the limit rejects
    (the brief passes only loss < limit). With no start equity (no equity point since the period
    began) the loss reads 0% and ``values`` lists the period in ``base_unknown`` so the gap is
    visible. The breakers (breakers.py) reuse this check, so they trip on the same base."""
    name = "loss_limits"

    cap = settings.bot_capital_cap_usdt

    def capped(base: Decimal | None) -> Decimal | None:
        return None if base is None or base <= 0 else sizing_equity(base, cap)[0]

    day_sizing, week_sizing = capped(day_base_usd), capped(week_base_usd)

    def pct(loss: Decimal, base: Decimal | None) -> Decimal:
        return ZERO if base is None else loss / base * HUNDRED

    day_pct, week_pct = pct(day_loss_usd, day_sizing), pct(week_loss_usd, week_sizing)
    values: dict[str, Any] = {
        "day_sizing_equity": day_sizing,
        "week_sizing_equity": week_sizing,
        "bot_capital_cap_usdt": cap,
        "day_loss_usd": day_loss_usd,
        "day_loss_pct": day_pct,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "week_loss_usd": week_loss_usd,
        "week_loss_pct": week_pct,
        "max_weekly_loss_pct": settings.max_weekly_loss_pct,
        "consecutive_losses": consecutive_losses,
        "max_consecutive_losses": settings.max_consecutive_losses,
        "base_unknown": [
            period
            for period, base in (("day", day_base_usd), ("week", week_base_usd))
            if base is None or base <= 0
        ],
    }
    if day_pct >= settings.max_daily_loss_pct:
        return CheckResult(
            name,
            False,
            "daily_loss_limit",
            f"Today's loss is {day_pct:.2f}% of sizing equity; the limit is {settings.max_daily_loss_pct}%.",
            values,
        )
    if week_pct >= settings.max_weekly_loss_pct:
        return CheckResult(
            name,
            False,
            "weekly_loss_limit",
            f"This week's loss is {week_pct:.2f}% of sizing equity; the limit is "
            f"{settings.max_weekly_loss_pct}%.",
            values,
        )
    if consecutive_losses >= settings.max_consecutive_losses:
        return CheckResult(
            name,
            False,
            "consecutive_losses",
            f"{consecutive_losses} losses in a row; the limit is {settings.max_consecutive_losses}.",
            values,
        )
    return CheckResult(name, True, None, "Loss limits are not reached.", values)


# --- Check 7: liquidation buffer ---------------------------------------------------------------


def estimated_liq_price(side: Side, entry: Decimal, leverage: int, mmr: Decimal) -> Decimal | None:
    """Isolated-margin estimate: long entry x (1 - 1/lev + mmr), short entry x (1 + 1/lev - mmr).
    None for an unlevered long (it cannot be liquidated)."""
    inv = Decimal(1) / Decimal(leverage)
    if side == "buy":
        return None if leverage <= 1 else entry * (1 - inv + mmr)
    return entry * (1 + inv - mmr)


def check_liquidation(
    *,
    side: Side,
    entry: Decimal,
    stop: Decimal,
    leverage: int,
    ct_type: ContractType,
    mmr: Decimal,
    settings: EngineSettings,
) -> CheckResult:
    name = "liquidation_buffer"
    if ct_type == "spot":
        return CheckResult(name, True, None, "Spot has no liquidation price.", {"leverage": 1})
    liq = estimated_liq_price(side, entry, leverage, mmr)
    stop_dist = abs(entry - stop)
    required = settings.liq_buffer_mult * stop_dist
    values: dict[str, Any] = {
        "entry": entry,
        "stop": stop,
        "leverage": leverage,
        "maint_margin_ratio": mmr,
        "estimated_liq_price": liq,
        "required_buffer": required,
        "liq_buffer_mult": settings.liq_buffer_mult,
    }
    if liq is None:
        return CheckResult(name, True, None, "An unlevered long has no liquidation price.", values)
    beyond = (stop - liq) if side == "buy" else (liq - stop)
    values["buffer_beyond_stop"] = beyond
    if beyond < required:
        return CheckResult(
            name,
            False,
            "liquidation_too_close",
            f"Estimated liquidation {liq:.8f} is {beyond:.8f} beyond the stop; at least "
            f"{settings.liq_buffer_mult} x stop distance = {required:.8f} is required.",
            values,
        )
    return CheckResult(name, True, None, "The liquidation price is far enough beyond the stop.", values)


# --- Check 8: event blackout -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    name: str
    country: str
    importance: int
    scheduled_at: datetime


def check_event_blackout(
    *, events: Sequence[ScheduledEvent], now: datetime, settings: EngineSettings, min_importance: int
) -> CheckResult:
    name = "event_blackout"
    minutes = settings.event_blackout_minutes
    values: dict[str, Any] = {"window_minutes": minutes, "min_importance": min_importance}
    if minutes <= 0:
        return CheckResult(name, True, None, "The event blackout is off.", values)
    end = now + timedelta(minutes=minutes)
    hits = sorted(
        (e for e in events if e.importance >= min_importance and now <= e.scheduled_at <= end),
        key=lambda e: e.scheduled_at,
    )
    values["events"] = [
        {"name": e.name, "country": e.country, "importance": e.importance, "scheduled_at": e.scheduled_at}
        for e in hits
    ]
    if hits:
        first = hits[0]
        mins = (first.scheduled_at - now).total_seconds() / 60
        return CheckResult(
            name,
            False,
            "event_blackout",
            f"{first.country} {first.name} (importance {first.importance}) is in {mins:.0f} min; "
            f"new trades are blocked {minutes} min before it.",
            values,
        )
    return CheckResult(
        name, True, None, f"No importance-{min_importance} event in the next {minutes} min.", values
    )


# --- Check 9: autonomy -------------------------------------------------------------------------


def decide_autonomy(level: int, max_enabled_level: int) -> tuple[CheckResult, Decision]:
    """1 advise_only, 2 awaiting_owner, 3-4 send_now only when enabled (Phase 3); a level above
    max_enabled_level falls back to awaiting_owner so nothing is sent without the owner."""
    name = "autonomy"
    values: dict[str, Any] = {"autonomy_level": level, "max_enabled_level": max_enabled_level}
    if level <= 1:
        return CheckResult(name, True, None, "Advise only: the proposal is not sent.", values), "advise_only"
    if level >= 3 and level <= max_enabled_level:
        return CheckResult(name, True, None, f"Autonomy {level}: sent within limits.", values), "send_now"
    if level >= 3:
        values["note"] = "level_not_enabled"
        return (
            CheckResult(
                name,
                True,
                None,
                f"Autonomy {level} is not enabled yet (max {max_enabled_level}); the owner approves.",
                values,
            ),
            "awaiting_owner",
        )
    return CheckResult(name, True, None, "The owner approves each trade.", values), "awaiting_owner"
