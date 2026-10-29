"""Pydantic models for the Phase 0 read-only OKX v5 REST responses.

Every number OKX sends is a string; money, prices, sizes and rates parse to ``Decimal`` and never to
``float``. OKX uses ``""`` for "not applicable", which parses to ``None``. Millisecond timestamps parse to
timezone-aware UTC ``datetime``. Unknown fields are ignored, so new OKX fields do not break parsing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


def _blank_to_none(value: Any) -> Any:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return value


def _to_decimal(value: Any) -> Any:
    value = _blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, float):
        raise ValueError("refusing a float for a Decimal field; OKX sends numbers as strings")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"not a decimal: {value!r}") from exc


def _ms_to_datetime(value: Any) -> Any:
    value = _blank_to_none(value)
    if value is None or isinstance(value, datetime):
        return value
    ms = int(value)
    return datetime.fromtimestamp(ms // 1000, tz=UTC).replace(microsecond=(ms % 1000) * 1000)


Dec = Annotated[Decimal | None, BeforeValidator(_to_decimal)]
"""An OKX numeric string: ``Decimal``, or ``None`` when OKX sends ``""``."""

Ms = Annotated[datetime | None, BeforeValidator(_ms_to_datetime)]
"""An OKX epoch-millisecond string as an aware UTC ``datetime``, or ``None`` when blank."""

MsRequired = Annotated[datetime, BeforeValidator(_ms_to_datetime)]
"""A required OKX epoch-millisecond string as an aware UTC ``datetime``."""

Str = Annotated[str | None, BeforeValidator(_blank_to_none)]
"""A string that is ``None`` when OKX sends ``""``."""


class OkxModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore", frozen=True)


class ServerTime(OkxModel):
    ts: MsRequired

    @property
    def epoch_ms(self) -> int:
        return int(self.ts.timestamp()) * 1000 + self.ts.microsecond // 1000


class Instrument(OkxModel):
    inst_type: str
    inst_id: str
    inst_family: Str = None
    inst_category: Str = None
    uly: Str = None
    base_ccy: Str = None
    quote_ccy: Str = None
    settle_ccy: Str = None
    ct_val: Dec = None
    ct_val_ccy: Str = None
    ct_mult: Dec = None
    ct_type: Str = None
    lot_sz: Dec = None
    min_sz: Dec = None
    tick_sz: Dec = None
    max_lever: Dec = Field(default=None, alias="lever")
    state: str
    list_time: Ms = None
    exp_time: Ms = None


class Ticker(OkxModel):
    inst_type: str
    inst_id: str
    last: Dec = None
    last_sz: Dec = None
    ask_px: Dec = None
    ask_sz: Dec = None
    bid_px: Dec = None
    bid_sz: Dec = None
    open24h: Dec = None
    high24h: Dec = None
    low24h: Dec = None
    vol_ccy24h: Dec = None
    vol24h: Dec = None
    sod_utc0: Dec = None
    sod_utc8: Dec = None
    ts: Ms = None


class AccountConfig(OkxModel):
    """``account/config``. ``uid`` is personal data: never log it or show more than the last 4 digits."""

    uid: str
    main_uid: Str = None
    acct_lv: str
    pos_mode: str
    auto_loan: bool | None = None
    greeks_type: Str = None
    level: Str = None
    ct_iso_mode: Str = None
    mgn_iso_mode: Str = None
    label: Str = None
    perm: str = ""
    ip: Str = None
    kyc_lv: Str = None
    settle_ccy: Str = None

    @property
    def permissions(self) -> frozenset[str]:
        return frozenset(p.strip() for p in self.perm.split(",") if p.strip())


class BalanceDetail(OkxModel):
    ccy: str
    eq: Dec = None
    cash_bal: Dec = None
    avail_bal: Dec = None
    avail_eq: Dec = None
    frozen_bal: Dec = None
    ord_frozen: Dec = None
    eq_usd: Dec = None
    dis_eq: Dec = None
    iso_eq: Dec = None
    upl: Dec = None
    liab: Dec = None
    interest: Dec = None
    u_time: Ms = None


class AccountBalance(OkxModel):
    total_eq: Dec = None
    adj_eq: Dec = None
    avail_eq: Dec = None
    iso_eq: Dec = None
    imr: Dec = None
    mmr: Dec = None
    mgn_ratio: Dec = None
    notional_usd: Dec = None
    ord_froz: Dec = None
    upl: Dec = None
    u_time: Ms = None
    details: list[BalanceDetail] = Field(default_factory=list)


class Position(OkxModel):
    inst_type: str
    inst_id: str
    mgn_mode: str
    pos_id: str
    pos_side: str
    pos: Dec = None
    pos_ccy: Str = None
    avail_pos: Dec = None
    avg_px: Dec = None
    mark_px: Dec = None
    last: Dec = None
    upl: Dec = None
    upl_ratio: Dec = None
    lever: Dec = None
    liq_px: Dec = None
    imr: Dec = None
    mmr: Dec = None
    margin: Dec = None
    mgn_ratio: Dec = None
    notional_usd: Dec = None
    ccy: Str = None
    realized_pnl: Dec = None
    fee: Dec = None
    funding_fee: Dec = None
    adl: Str = None
    c_time: Ms = None
    u_time: Ms = None


class PositionHistory(OkxModel):
    inst_type: str
    inst_id: str
    mgn_mode: str
    type: str
    pos_id: str
    pos_side: str
    direction: Str = None
    lever: Dec = None
    ccy: Str = None
    open_avg_px: Dec = None
    close_avg_px: Dec = None
    open_max_pos: Dec = None
    close_total_pos: Dec = None
    pnl: Dec = None
    pnl_ratio: Dec = None
    realized_pnl: Dec = None
    fee: Dec = None
    funding_fee: Dec = None
    liq_penalty: Dec = None
    c_time: Ms = None
    u_time: Ms = None


class Bill(OkxModel):
    bill_id: str
    inst_type: Str = None
    inst_id: Str = None
    ccy: str
    type: str
    sub_type: Str = None
    mgn_mode: Str = None
    exec_type: Str = None
    bal: Dec = None
    bal_chg: Dec = None
    sz: Dec = None
    px: Dec = None
    pnl: Dec = None
    fee: Dec = None
    interest: Dec = None
    ord_id: Str = None
    cl_ord_id: Str = None
    trade_id: Str = None
    notes: Str = None
    fill_time: Ms = None
    ts: MsRequired


class TradeFee(OkxModel):
    """``account/trade-fee``. OKX sends fees as negative rates (``-0.0005`` = you pay 0.05%)."""

    inst_type: str
    level: Str = None
    maker: Dec = None
    taker: Dec = None
    maker_u: Dec = None
    taker_u: Dec = None
    maker_usdc: Dec = Field(default=None, alias="makerUSDC")
    taker_usdc: Dec = Field(default=None, alias="takerUSDC")
    ts: Ms = None


class AssetBalance(OkxModel):
    """``asset/balances``: the funding account."""

    ccy: str
    bal: Dec = None
    frozen_bal: Dec = None
    avail_bal: Dec = None


class DepositRecord(OkxModel):
    dep_id: str
    ccy: str
    chain: Str = None
    amt: Dec = None
    from_: Str = Field(default=None, alias="from")
    to: Str = None
    tx_id: Str = None
    state: str
    actual_dep_blk_confirm: Str = None
    ts: MsRequired


class WithdrawalRecord(OkxModel):
    """``asset/withdrawal-history`` (read only, for the dashboard). Atlas never creates withdrawals."""

    wd_id: str
    ccy: str
    chain: Str = None
    amt: Dec = None
    fee: Dec = None
    fee_ccy: Str = None
    from_: Str = Field(default=None, alias="from")
    to: Str = None
    tx_id: Str = None
    state: str
    ts: MsRequired


# ---- market data (ATL-45) ----------------------------------------------------------------------


class _ArrayRow(OkxModel):
    """A model OKX sends as a positional array (``["1730000000000", "1.2", ...]``).

    ``_columns`` names the positions in the documented order; extra trailing positions are ignored, so
    a column OKX appends later does not break parsing. A dict row is also accepted.
    """

    _columns: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def _from_array(cls, value: Any) -> Any:
        if isinstance(value, list | tuple):
            if len(value) < len(cls._columns):
                raise ValueError(f"{cls.__name__} row has {len(value)} columns, expected {len(cls._columns)}")
            return dict(zip(cls._columns, value, strict=False))
        return value


def _confirm(value: Any) -> Any:
    if isinstance(value, str):
        if value not in ("0", "1"):
            raise ValueError(f"candle confirm must be '0' or '1', got {value!r}")
        return value == "1"
    return value


class Candle(_ArrayRow):
    """``market/candles`` and ``market/history-candles``: one bar, opening at ``ts`` (UTC).

    ``vol`` is in contracts for derivatives (base currency for spot), ``vol_ccy`` in base currency,
    ``vol_ccy_quote`` in quote currency. ``confirm`` is False while the bar is still forming.
    """

    _columns = ("ts", "o", "h", "l", "c", "vol", "vol_ccy", "vol_ccy_quote", "confirm")
    ts: MsRequired
    o: Dec
    h: Dec
    l: Dec  # noqa: E741 - OKX's own name
    c: Dec
    vol: Dec = None
    vol_ccy: Dec = None
    vol_ccy_quote: Dec = None
    confirm: Annotated[bool, BeforeValidator(_confirm)]


class FundingRate(OkxModel):
    """``public/funding-rate``: the current period's rate and the next settlement time."""

    inst_type: str
    inst_id: str
    funding_rate: Dec = None
    next_funding_rate: Dec = None
    funding_time: Ms = None
    next_funding_time: Ms = None
    min_funding_rate: Dec = None
    max_funding_rate: Dec = None
    sett_funding_rate: Dec = None
    sett_state: Str = None
    premium: Dec = None
    interest_rate: Dec = None
    impact_value: Dec = None
    method: Str = None
    formula_type: Str = None
    ts: Ms = None


class FundingRateHistory(OkxModel):
    """``public/funding-rate-history``: one settled funding period."""

    inst_type: str
    inst_id: str
    funding_rate: Dec = None
    realized_rate: Dec = None
    funding_time: MsRequired
    method: Str = None
    formula_type: Str = None


class OpenInterest(OkxModel):
    """``public/open-interest``: ``oi`` in contracts, ``oi_ccy`` in coin, ``oi_usd`` in USD."""

    inst_type: str
    inst_id: str
    oi: Dec = None
    oi_ccy: Dec = None
    oi_usd: Dec = None
    ts: MsRequired


class OpenInterestVolume(_ArrayRow):
    """``rubik/stat/contracts/open-interest-volume``: per-currency OI and volume, both in USD."""

    _columns = ("ts", "oi", "vol")
    ts: MsRequired
    oi: Dec = None
    vol: Dec = None


class LongShortRatio(_ArrayRow):
    """``rubik/stat/contracts/long-short-account-ratio``: accounts long / accounts short."""

    _columns = ("ts", "ratio")
    ts: MsRequired
    ratio: Dec = None


class TakerVolume(_ArrayRow):
    """``rubik/stat/taker-volume``: taker sell and buy volume, in the unit OKX documents for the instType."""

    _columns = ("ts", "sell_vol", "buy_vol")
    ts: MsRequired
    sell_vol: Dec = None
    buy_vol: Dec = None


class MarkPrice(OkxModel):
    """``public/mark-price``."""

    inst_type: str
    inst_id: str
    mark_px: Dec = None
    ts: MsRequired


ALL_MODELS: tuple[type[OkxModel], ...] = (
    ServerTime,
    Instrument,
    Ticker,
    AccountConfig,
    BalanceDetail,
    AccountBalance,
    Position,
    PositionHistory,
    Bill,
    TradeFee,
    AssetBalance,
    DepositRecord,
    WithdrawalRecord,
    Candle,
    FundingRate,
    FundingRateHistory,
    OpenInterest,
    OpenInterestVolume,
    LongShortRatio,
    TakerVolume,
    MarkPrice,
)
