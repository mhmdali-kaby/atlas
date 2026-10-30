"""Portfolio limit usage for the Portfolio Manager's warnings (brief §7.8 step 4, ATL-169).

Computed in code from the owner's settings (rule 2: limits come only from the settings table, with
the risk profile's preset for a key the row does not set, as the Risk Engine reads them) and the
synced account: today's and this week's loss (start equity of the Baghdad day / week + net deposits
since then - current equity, the Risk Engine's check 6 formula) and the open-position count.

:func:`due_warnings` returns the limits at or above ``limit_warning_pct`` (70) % of use that have not
been warned about in the last ``limit_warning_interval_minutes`` (60): one warning per limit per hour,
not one per 5-minute check.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from atlas_shared.db import DepositWithdrawal, EquityPoint
from atlas_shared.defaults import DEFAULTS
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents import proposals as props
from atlas_agents.pm import inputs as inp
from atlas_agents.tools.framework import ToolContext

USD_LIKE: Final = frozenset({"USD", "USDT", "USDC"})
HUNDRED: Final = Decimal(100)


@dataclass(frozen=True)
class LimitUsage:
    limit: str
    """The settings key: max_daily_loss_pct, max_weekly_loss_pct or max_open_positions."""
    used: Decimal
    """Loss % of start equity, or open positions."""
    cap: Decimal
    usage_pct: Decimal
    """used / cap x 100."""

    def card(self) -> dict[str, Any]:
        return {
            "type": "limit_warning",
            "limit": self.limit,
            "used": format(self.used.quantize(Decimal("0.01")), "f"),
            "cap": format(self.cap, "f"),
            "usage_pct": format(self.usage_pct.quantize(Decimal("0.1")), "f"),
        }


def _owner_start(now: datetime, *, week: bool) -> datetime:
    local = now.astimezone(ZoneInfo(str(DEFAULTS["timezone"]["display"])))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if week:
        start -= timedelta(days=start.weekday())
    return start


def _equity_now(session: Session, ctx: ToolContext) -> Decimal | None:
    return session.scalar(
        select(EquityPoint.total_eq_usd)
        .where(EquityPoint.env == ctx.env, ctx.bound(EquityPoint.ts))
        .order_by(EquityPoint.ts.desc())
        .limit(1)
    )


def _equity_since(session: Session, ctx: ToolContext, since: datetime) -> Decimal | None:
    return session.scalar(
        select(EquityPoint.total_eq_usd)
        .where(EquityPoint.env == ctx.env, EquityPoint.ts >= since, ctx.bound(EquityPoint.ts))
        .order_by(EquityPoint.ts)
        .limit(1)
    )


def _net_flows(session: Session, ctx: ToolContext, since: datetime) -> Decimal:
    total = Decimal(0)
    for kind, ccy, amount in session.execute(
        select(DepositWithdrawal.kind, DepositWithdrawal.ccy, DepositWithdrawal.amount).where(
            DepositWithdrawal.env == ctx.env, DepositWithdrawal.ts >= since, ctx.bound(DepositWithdrawal.ts)
        )
    ).all():
        if str(ccy).upper() in USD_LIKE:
            total += amount if kind == "deposit" else -amount
    return total


def loss_pct(session: Session, ctx: ToolContext, since: datetime) -> Decimal | None:
    """Loss since ``since`` as % of the first equity point since then; None without one."""
    base, now_eq = _equity_since(session, ctx, since), _equity_now(session, ctx)
    if base is None or now_eq is None or base <= 0:
        return None
    return (base + _net_flows(session, ctx, since) - now_eq) / base * HUNDRED


def limit_values(session: Session, ctx: ToolContext) -> dict[str, Decimal]:
    """The three caps from the settings row, else the row's risk profile preset."""
    data = props.settings_data(session, ctx).data
    profiles: Mapping[str, Any] = DEFAULTS["risk_profiles"]
    name = data.get("risk_profile", profiles["default"])
    preset: Mapping[str, Any] = profiles.get(name, profiles[profiles["default"]])
    out: dict[str, Decimal] = {}
    for key in ("max_daily_loss_pct", "max_weekly_loss_pct", "max_open_positions"):
        raw = data.get(key, preset.get(key))
        try:
            value = Decimal(str(raw))
        except ArithmeticError:
            continue
        if value.is_finite() and value > 0:
            out[key] = value
    return out


def usage(session: Session, ctx: ToolContext) -> list[LimitUsage]:
    caps = limit_values(session, ctx)
    now = ctx.now()
    used: dict[str, Decimal | None] = {
        "max_daily_loss_pct": loss_pct(session, ctx, _owner_start(now, week=False)),
        "max_weekly_loss_pct": loss_pct(session, ctx, _owner_start(now, week=True)),
        "max_open_positions": Decimal(len(inp.held_snapshots(session, ctx))),
    }
    out: list[LimitUsage] = []
    for key, cap in caps.items():
        value = used.get(key)
        if value is None:
            continue
        out.append(LimitUsage(limit=key, used=value, cap=cap, usage_pct=value / cap * HUNDRED))
    return out


_lock = threading.Lock()
_warned: dict[tuple[str, str], datetime] = {}
"""(env, limit) -> when this process last warned."""


def due_warnings(session: Session, ctx: ToolContext, cfg: Mapping[str, Any]) -> list[LimitUsage]:
    """Limits at or above ``limit_warning_pct`` % of use not warned in the last interval. Marks the
    returned ones as warned now."""
    threshold = Decimal(str(cfg["limit_warning_pct"]))
    interval = timedelta(minutes=int(cfg["limit_warning_interval_minutes"]))
    now = ctx.now()
    due: list[LimitUsage] = []
    with _lock:
        for item in usage(session, ctx):
            if item.usage_pct < threshold:
                continue
            last = _warned.get((ctx.env, item.limit))
            if last is not None and now - last < interval:
                continue
            _warned[(ctx.env, item.limit)] = now
            due.append(item)
    return due


def reset() -> None:
    """Forget every warning time (tests)."""
    with _lock:
        _warned.clear()


LABELS: Final = {
    "max_daily_loss_pct": "daily loss limit",
    "max_weekly_loss_pct": "weekly loss limit",
    "max_open_positions": "open-position limit",
}


def warning_text(item: LimitUsage) -> str:
    label = LABELS.get(item.limit, item.limit)
    if item.limit == "max_open_positions":
        detail = f"{item.used:.0f} of {item.cap:.0f} positions open"
    else:
        detail = f"loss {item.used:.2f}% of equity against a {item.cap}% limit"
    return f"**Limit warning:** the {label} is at {item.usage_pct:.0f}% ({detail})."


__all__ = [
    "LABELS",
    "LimitUsage",
    "due_warnings",
    "limit_values",
    "loss_pct",
    "reset",
    "usage",
    "warning_text",
]
