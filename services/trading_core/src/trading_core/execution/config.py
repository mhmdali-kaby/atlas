"""Executor configuration, constants and the deterministic id/price helpers."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from atlas_shared.defaults import DEFAULTS

ORDERS_STREAM = "atlas.orders"
SYSTEM_STREAM = "atlas.system"
WARROOM_STREAM = "atlas.warroom"
SOURCE = "service:trading_core.executor"
WARROOM_SOURCE = "system:executor"
WARROOM_AUTHOR = "executor"
OWNER = "owner"

EXECUTABLE_RESULTS = frozenset({"approved", "resized"})
EXECUTABLE_PROPOSAL_STATUSES = frozenset({"risk_checked", "awaiting_owner"})
ORDER_NOT_FOUND = "51603"
# OKX answers to a cancel that has nothing left to cancel (already gone, cancelled or filled).
CANCEL_NOOP_CODES = frozenset({"51400", "51401", "51402"})
LONG_SHORT_MODE = "long_short_mode"
WITHDRAW = "withdraw"


@dataclass(frozen=True)
class ExecutorConfig:
    td_mode_perps: str
    stop_verify_seconds: float
    order_max_retries: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    poll_seconds: float
    attach_refused_codes: frozenset[str]
    retryable_codes: frozenset[str]

    @classmethod
    def from_settings(cls, data: Mapping[str, Any]) -> ExecutorConfig:
        d: Mapping[str, Any] = DEFAULTS["executor"]
        rest: Mapping[str, Any] = DEFAULTS["okx_rest"]

        def pick(key: str) -> Any:
            value = data.get(key)
            return d[key] if value is None else value

        return cls(
            td_mode_perps=str(pick("td_mode_perps")),
            stop_verify_seconds=float(pick("stop_verify_seconds")),
            order_max_retries=max(1, int(pick("order_max_retries"))),
            backoff_base_seconds=float(d["retry_backoff_base_seconds"]),
            backoff_max_seconds=float(d["retry_backoff_max_seconds"]),
            poll_seconds=float(d["stop_verify_poll_seconds"]),
            attach_refused_codes=frozenset(str(c) for c in d["attach_refused_codes"]),
            retryable_codes=frozenset(str(c) for c in [*rest["retryable_codes"], *rest["rate_limit_codes"]]),
        )

    def backoff(self, retry: int) -> float:
        """Delay before retry number ``retry`` (1-based): base x 2^(retry-1), capped."""
        return float(min(self.backoff_max_seconds, self.backoff_base_seconds * (2 ** (retry - 1))))


def derived_id(prefix: str, risk_check_id: uuid.UUID) -> str:
    """A 32-alphanumeric OKX client id fixed by the risk check: one letter + its last 31 hex digits."""
    if len(prefix) != 1 or not prefix.isalpha():
        raise ValueError("prefix must be one letter")
    return prefix + risk_check_id.hex[1:]


def round_px(px: Decimal, tick: Decimal | None, side: str) -> Decimal:
    """Round a price to ``tick``: down for a buy, up for a sell (a buy's stop never moves closer,
    a buy's limit never pays more; mirrored for a sell)."""
    if tick is None or tick <= 0:
        return px.normalize()
    steps = (px / tick).to_integral_value(rounding=ROUND_FLOOR if side == "buy" else ROUND_CEILING)
    rounded = (steps * tick).normalize()
    return rounded if rounded > 0 else px.normalize()


def round_stop(stop: Decimal, tick: Decimal | None, side: str) -> Decimal:
    """Round an entry's stop to ``tick`` toward the entry (a buy's stop up, a sell's down), so the
    stop on OKX is never further away than the one the Risk Engine sized the position on."""
    return round_px(stop, tick, "sell" if side == "buy" else "buy")
