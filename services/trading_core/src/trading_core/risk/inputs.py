"""Frozen inputs shared by the Risk Engine checks (brief §10, §4.3).

Every money, price, size and ratio is a ``Decimal``. A ``float`` anywhere raises ``TypeError`` at
construction, so binary rounding can never reach an order size. Non-finite Decimals raise
``ValueError``. Percentages are in percent (``Decimal("1.0")`` = 1 %), as in the settings table.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any, Literal

from atlas_shared.defaults import DEFAULTS

Side = Literal["buy", "sell"]
ContractType = Literal["linear", "inverse", "spot"]

# Settle/quote currencies whose price is already in US dollars for sizing. USDC is not here: it is
# converted with the USDC/USD rate the caller passes (brief §4.3).
USD_LIKE: frozenset[str] = frozenset({"USD", "USDT"})


def require_decimal(name: str, value: object, *, optional: bool = False) -> None:
    """Refuse floats (TypeError) and non-finite Decimals (ValueError) at the boundary."""
    if value is None and optional:
        return
    if isinstance(value, float) or not isinstance(value, Decimal):
        raise TypeError(f"{name} must be decimal.Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")


def require_int(name: str, value: object, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int, got {type(value).__name__}")


def _check_fields(obj: object, ints: frozenset[str] = frozenset(), optional: frozenset[str] = frozenset(),
                  skip: frozenset[str] = frozenset()) -> None:
    for f in fields(obj):  # type: ignore[arg-type]
        if f.name in skip:
            continue
        value = getattr(obj, f.name)
        if f.name in ints:
            require_int(f.name, value, optional=f.name in optional)
        else:
            require_decimal(f.name, value, optional=f.name in optional)


@dataclass(frozen=True, slots=True)
class RiskSettings:
    """The numbers checks 3 and 4 read from one settings version (brief §1.2: only from Settings)."""

    max_loss_per_trade_pct: Decimal
    max_position_pct: Decimal
    max_share_per_instrument_pct: Decimal
    max_leverage: int
    min_expected_r: Decimal
    max_usdt_per_trade: Decimal
    max_new_exposure_per_day_usdt: Decimal
    min_stop_atr_mult: Decimal
    max_stop_pct: Decimal
    slippage_bps: Decimal
    margin_buffer: Decimal

    def __post_init__(self) -> None:
        _check_fields(self, ints=frozenset({"max_leverage"}))
        if self.max_leverage < 1:
            raise ValueError("max_leverage must be >= 1")
        for name in ("slippage_bps", "max_loss_per_trade_pct", "max_position_pct",
                     "max_share_per_instrument_pct", "max_usdt_per_trade", "max_new_exposure_per_day_usdt"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")

    @classmethod
    def from_settings_data(cls, data: Mapping[str, Any]) -> RiskSettings:
        """Build from a ``settings.data`` row; keys it lacks fall back to DEFAULTS (ATL-124 handoff).

        Profile fields fall back to the row's ``risk_profile`` preset. Stored values are decimal
        strings or JSON integers; a float in the row raises TypeError like any other float.
        """
        profiles: dict[str, Any] = DEFAULTS["risk_profiles"]
        profile_name = data.get("risk_profile", profiles["default"])
        preset: dict[str, Any] = profiles.get(profile_name, profiles[profiles["default"]])
        money: dict[str, Any] = DEFAULTS["money_limits"]
        engine: dict[str, Any] = DEFAULTS["risk_engine"]

        def dec(key: str, fallback: Mapping[str, Any]) -> Decimal:
            raw = data[key] if key in data else fallback[key]
            if isinstance(raw, float):
                raise TypeError(f"settings {key} is a float; store it as a decimal string")
            if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
                raise TypeError(f"settings {key} has type {type(raw).__name__}")
            return Decimal(raw)

        raw_lev = data["max_leverage"] if "max_leverage" in data else preset["max_leverage"]
        if isinstance(raw_lev, float):
            raise TypeError("settings max_leverage is a float")
        leverage = int(Decimal(str(raw_lev)))

        return cls(
            max_loss_per_trade_pct=dec("max_loss_per_trade_pct", preset),
            max_position_pct=dec("max_position_pct", preset),
            max_share_per_instrument_pct=dec("max_share_per_instrument_pct", preset),
            max_leverage=leverage,
            min_expected_r=dec("min_expected_r", preset),
            max_usdt_per_trade=dec("max_usdt_per_trade", money),
            max_new_exposure_per_day_usdt=dec("max_new_exposure_per_day_usdt", money),
            min_stop_atr_mult=dec("min_stop_atr_mult", engine),
            max_stop_pct=dec("max_stop_pct", engine),
            slippage_bps=dec("slippage_bps", engine),
            margin_buffer=dec("margin_buffer", engine),
        )


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """The §4.3 fields sizing needs. ``ct_type``: see :func:`infer_contract_type`."""

    inst_id: str
    ct_type: ContractType
    settle_ccy: str
    quote_ccy: str
    ct_val: Decimal
    ct_mult: Decimal
    lot_sz: Decimal
    min_sz: Decimal
    tick_sz: Decimal
    max_lever: Decimal | None

    def __post_init__(self) -> None:
        _check_fields(self, optional=frozenset({"max_lever"}),
                      skip=frozenset({"inst_id", "ct_type", "settle_ccy", "quote_ccy"}))
        if self.ct_type not in ("linear", "inverse", "spot"):
            raise ValueError(f"unknown ct_type {self.ct_type!r}")
        for name in ("ct_val", "ct_mult", "lot_sz", "tick_sz"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.min_sz < 0:
            raise ValueError("min_sz must be >= 0")


def infer_contract_type(inst_type: str, base_ccy: str | None, settle_ccy: str | None) -> ContractType:
    """SPOT is spot; a derivative settled in its base coin (BTC-USD-SWAP) is inverse; else linear."""
    if inst_type.upper() == "SPOT":
        return "spot"
    if base_ccy and settle_ccy and base_ccy.upper() == settle_ccy.upper():
        return "inverse"
    return "linear"


@dataclass(frozen=True, slots=True)
class ProposalInput:
    """What checks 3 and 4 read from a TradeProposal. ``entry`` is the price to size at: the limit
    price, or the mark for a market order (the caller resolves an entry zone to one price)."""

    side: Side
    entry: Decimal
    stop: Decimal | None
    expected_r: Decimal
    leverage_requested: int
    size_suggestion_pct_equity: Decimal | None = None

    def __post_init__(self) -> None:
        _check_fields(self, ints=frozenset({"leverage_requested"}),
                      optional=frozenset({"stop", "size_suggestion_pct_equity"}), skip=frozenset({"side"}))
        if self.side not in ("buy", "sell"):
            raise ValueError(f"unknown side {self.side!r}")
        if self.entry <= 0:
            raise ValueError("entry must be > 0")
        if self.leverage_requested < 1:
            raise ValueError("leverage_requested must be >= 1")
