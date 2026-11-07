"""Market Scout step 1: the universe filter (brief §7.1, decision #247 (2), ATL-104).

Code, not the model, decides which instruments the Scout may consider. An instrument is kept when
every check passes, in this order:

* ``market``: its market (spot, crypto_perp, stock_perp, commodity_perp, futures, options, other)
  is enabled;
* ``state``: OKX ``state`` is ``live``;
* ``blocklist``: its instId is not on the owner's blocklist (case-insensitive);
* ``whitelist``: when the owner's Markets whitelist is not empty, its instId is on it (decision
  #250: the whitelist restricts, it never adds; every other check still applies);
* ``age``: OKX ``listTime`` is more than ``min_listed_days`` before ``as_of``;
* ``volume``: 24h quote volume, in USD, is at least the market's minimum. USDC and USD quotes count
  1:1 as USD (``usd_quote_ccys``); any other quote currency fails as ``volume_unknown``;
* ``spread``: spread is at most ``max_spread_bps``.

A rejected instrument carries every check it failed, not only the first. Two inputs can be unknown
without rejecting: a missing ``listTime`` (``age_unknown``) and, in Phase 1, the spread
(``spread_unknown``: Atlas stores no order book until the Phase 2 WebSocket feed). Those are kept
with a note so the feature table and the model see the gap.

Replay: instruments listed after ``as_of`` are invisible, volume comes from bars closed by
``as_of`` and the settings are the version in force at ``as_of``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal

from atlas_shared.db import Instrument
from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas_agents.tools.account import _settings, _settings_history
from atlas_agents.tools.framework import ToolContext
from atlas_agents.tools.market import compute_tickers, listed

Market = Literal["spot", "crypto_perp", "stock_perp", "commodity_perp", "futures", "options", "other"]
RejectReason = Literal[
    "market", "state", "blocklist", "whitelist", "age", "volume", "volume_unknown", "spread"
]
KeepNote = Literal["age_unknown", "spread_unknown"]

MARKETS: Final[tuple[str, ...]] = ("spot", "crypto_perp", "stock_perp", "commodity_perp")
_STOCK_CLASSES: Final = frozenset({"stock", "etf", "index"})


def scout_config(defaults: Mapping[str, Any] = DEFAULTS) -> Mapping[str, Any]:
    config: Mapping[str, Any] = defaults["agents"]["scout"]
    return config


def market_of(inst_type: str, asset_class: str) -> Market:
    """The Settings -> Markets toggle an instrument falls under."""
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


class UniverseConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled_markets: frozenset[str]
    blocklist: frozenset[str]  # upper-case instIds
    whitelist: frozenset[str] = frozenset()  # upper-case instIds; empty = no restriction
    min_volume_usd: dict[str, Decimal]  # per market
    max_spread_bps: Decimal
    min_listed_days: int
    usd_quote_ccys: frozenset[str]
    settings_version: int | None = None


@dataclass(frozen=True)
class Candidate:
    """What the filter needs to know about one instrument."""

    inst_id: str
    inst_type: str
    asset_class: str
    state: str
    list_time: datetime | None
    quote_ccy: str | None
    settle_ccy: str | None
    volume_24h_quote: Decimal | None  # quote currency; None when no bar closed in the window
    spread_bps: Decimal | None = None  # None = unknown


class Kept(BaseModel):
    model_config = ConfigDict(frozen=True)

    inst_id: str
    inst_type: str
    asset_class: str
    market: str
    volume_24h_usd: Decimal
    spread_bps: Decimal | None
    notes: list[KeepNote]


class Rejected(BaseModel):
    model_config = ConfigDict(frozen=True)

    inst_id: str
    market: str
    reasons: list[RejectReason]
    detail: list[str]  # one human line per reason, same order


class UniverseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    as_of: datetime
    settings_version: int | None
    volume_basis: str
    kept: list[Kept]
    rejected: list[Rejected]


VOLUME_BASIS: Final = "24h quote volume summed from closed 1H candles; USDC and USD quotes counted 1:1 as USD"


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _str_list(value: object) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [str(v) for v in value]
    return None


def config_from(data: Mapping[str, Any] | None, version: int | None = None) -> UniverseConfig:
    """The filter's config: settings ``data`` where it has the key, else config/defaults.yaml."""
    settings = data or {}
    universe = scout_config()["universe"]
    markets: Mapping[str, Any] = DEFAULTS["markets"]
    min_volume: dict[str, Decimal] = {}
    for market in MARKETS:
        default = Decimal(str(markets["min_24h_volume_usd"][market]))
        min_volume[market] = _decimal(settings.get(f"min_24h_volume_usd_{market}")) or default
    spread = _decimal(settings.get("max_spread_bps")) or Decimal(str(markets["max_spread_bps"]))
    # A saved [] means every toggle is off: only a missing (or malformed) key falls back.
    enabled_saved = _str_list(settings.get("enabled_markets"))
    enabled = enabled_saved if enabled_saved is not None else list(universe["enabled_markets"])
    blocklist = _str_list(settings.get("blocklist"))
    blocked = blocklist if blocklist is not None else list(universe["blocklist"])
    allowed = _str_list(settings.get("whitelist")) or []
    return UniverseConfig(
        enabled_markets=frozenset(enabled),
        blocklist=frozenset(b.upper() for b in blocked),
        whitelist=frozenset(w.upper() for w in allowed),
        min_volume_usd=min_volume,
        max_spread_bps=spread,
        min_listed_days=int(universe["min_listed_days"]),
        usd_quote_ccys=frozenset(str(c).upper() for c in universe["usd_quote_ccys"]),
        settings_version=version,
    )


def _volume_ccy(c: Candidate) -> str | None:
    """The currency OKX's volCcyQuote is in: the quote currency. OKX leaves quoteCcy empty on
    swaps, so it is then the instId's second segment (BTC-USDT-SWAP -> USDT), else settleCcy."""
    parts = c.inst_id.split("-")
    ccy = c.quote_ccy or (parts[1] if len(parts) >= 2 else None) or c.settle_ccy
    return ccy.upper() if ccy else None


def filter_universe(
    candidates: Iterable[Candidate], config: UniverseConfig, as_of: datetime
) -> UniverseResult:
    """Apply the step-1 checks; kept instruments are ordered by 24h USD volume, highest first."""
    kept: list[Kept] = []
    rejected: list[Rejected] = []
    cutoff = as_of - timedelta(days=config.min_listed_days)
    for c in candidates:
        market = market_of(c.inst_type, c.asset_class)
        reasons: list[RejectReason] = []
        detail: list[str] = []
        notes: list[KeepNote] = []
        if market not in config.enabled_markets:
            reasons.append("market")
            detail.append(f"market {market} is not enabled")
        if c.state != "live":
            reasons.append("state")
            detail.append(f"state is {c.state}, not live")
        if c.inst_id.upper() in config.blocklist:
            reasons.append("blocklist")
            detail.append("on the owner's blocklist")
        if config.whitelist and c.inst_id.upper() not in config.whitelist:
            reasons.append("whitelist")
            detail.append("not on the owner's Markets whitelist")
        if c.list_time is None:
            notes.append("age_unknown")
        elif c.list_time >= cutoff:
            reasons.append("age")
            detail.append(
                f"listed {c.list_time.isoformat()}, not more than {config.min_listed_days} days ago"
            )
        volume_usd: Decimal | None = None
        ccy = _volume_ccy(c)
        if ccy not in config.usd_quote_ccys:
            reasons.append("volume_unknown")
            detail.append(f"24h volume is in {ccy}, which is not converted to USD")
        else:
            volume_usd = c.volume_24h_quote if c.volume_24h_quote is not None else Decimal(0)
            minimum = config.min_volume_usd.get(market)
            if minimum is not None and volume_usd < minimum:
                reasons.append("volume")
                detail.append(f"24h volume {volume_usd:f} USD is below the {minimum:f} USD minimum")
        if c.spread_bps is None:
            notes.append("spread_unknown")
        elif c.spread_bps > config.max_spread_bps:
            reasons.append("spread")
            detail.append(f"spread {c.spread_bps:f} bps is above the {config.max_spread_bps:f} bps maximum")
        if reasons:
            rejected.append(Rejected(inst_id=c.inst_id, market=market, reasons=reasons, detail=detail))
        else:
            assert volume_usd is not None
            kept.append(
                Kept(
                    inst_id=c.inst_id,
                    inst_type=c.inst_type,
                    asset_class=c.asset_class,
                    market=market,
                    volume_24h_usd=volume_usd,
                    spread_bps=c.spread_bps,
                    notes=notes,
                )
            )
    kept.sort(key=lambda k: (-k.volume_24h_usd, k.inst_id))
    rejected.sort(key=lambda r: r.inst_id)
    return UniverseResult(
        as_of=as_of,
        settings_version=config.settings_version,
        volume_basis=VOLUME_BASIS,
        kept=kept,
        rejected=rejected,
    )


# -- loading from Postgres ---------------------------------------------------------------------------


def load_config(session: Session, ctx: ToolContext) -> UniverseConfig:
    """The filter config from the settings version in force at ``ctx.now()`` (defaults when the env
    has no settings row yet)."""
    if ctx.replaying:
        row = session.execute(
            select(_settings_history.c.version, _settings_history.c.data)
            .join(_settings, _settings.c.id == _settings_history.c.settings_id)
            .where(_settings.c.env == ctx.env, ctx.bound(_settings_history.c.created_at))
            .order_by(_settings_history.c.version.desc())
            .limit(1)
        ).first()
    else:
        row = session.execute(
            select(_settings.c.version, _settings.c.data).where(_settings.c.env == ctx.env)
        ).first()
    if row is None:
        return config_from(None)
    version, data = row
    return config_from(data if isinstance(data, dict) else None, int(version))


def load_candidates(
    session: Session, ctx: ToolContext, spreads_bps: Mapping[str, Decimal] | None = None
) -> list[Candidate]:
    """Every instrument of the env known by ``ctx.now()``, with its 24h quote volume."""
    instruments: Sequence[Instrument] = session.scalars(
        select(Instrument).where(listed(ctx)).order_by(Instrument.inst_id)
    ).all()
    tickers = compute_tickers(session, ctx, [i.inst_id for i in instruments])
    spreads = spreads_bps or {}
    out: list[Candidate] = []
    for inst in instruments:
        ticker = tickers.get(inst.inst_id)
        out.append(
            Candidate(
                inst_id=inst.inst_id,
                inst_type=inst.inst_type,
                asset_class=inst.asset_class,
                state=inst.state,
                list_time=inst.list_time,
                quote_ccy=inst.quote_ccy,
                settle_ccy=inst.settle_ccy,
                volume_24h_quote=ticker.volume_24h_quote if ticker is not None else None,
                spread_bps=spreads.get(inst.inst_id),
            )
        )
    return out


def build_universe(ctx: ToolContext, spreads_bps: Mapping[str, Decimal] | None = None) -> UniverseResult:
    """Step 1 for the active env at ``ctx.now()``."""
    with ctx.session_factory() as session:
        config = load_config(session, ctx)
        candidates = load_candidates(session, ctx, spreads_bps)
    return filter_universe(candidates, config, ctx.now())


__all__ = [
    "MARKETS",
    "VOLUME_BASIS",
    "Candidate",
    "KeepNote",
    "Kept",
    "Market",
    "RejectReason",
    "Rejected",
    "UniverseConfig",
    "UniverseResult",
    "build_universe",
    "config_from",
    "filter_universe",
    "load_candidates",
    "load_config",
    "market_of",
    "scout_config",
]
