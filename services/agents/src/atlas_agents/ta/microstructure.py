"""Order-book microstructure from a books snapshot (brief §7.4 "Microstructure", ATL-133).

Input is an OKX ``market/books`` snapshot: ``bids`` (best first, descending price) and ``asks`` (best
first, ascending), each level ``[price, size, ...]`` with strings or numbers. Sizes are in contracts;
``ct_val`` turns them into base units (1 for spot). Linear (USDT/USDC-margined) contracts only: a
level's notional is ``price x size x ct_val``. All maths is Decimal.

* **Imbalance** over the top N levels: (sum bid size - sum ask size) / (sum bid size + sum ask size),
  in [-1, 1]; positive = more resting bids. Computed for each N in ``imbalance_depths`` (10 and 50).
* **Spread**: (best ask - best bid) / mid x 10,000 bps, mid = (best bid + best ask) / 2.
* **Slippage** (:func:`walk_book`): a buy walks the asks, a sell the bids, taking each level's notional
  until ``notional`` is filled (the last level partly). Average fill = notional spent / base quantity
  bought. ``slippage_bps`` is the average fill's distance from mid (adverse side positive) and
  ``impact_bps`` its distance from the best price on that side. When the book runs out, ``filled`` is
  false and the figures cover ``filled_notional`` only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Literal

from atlas_shared.defaults import DEFAULTS
from pydantic import BaseModel, ConfigDict

from atlas_agents.ta._structure import cfg

Level = Sequence[Any]
BPS = Decimal(10_000)


class BookFill(BaseModel):
    model_config = ConfigDict(frozen=True)

    side: Literal["buy", "sell"]
    notional: Decimal
    filled: bool
    filled_notional: Decimal
    base_qty: Decimal
    avg_price: Decimal | None
    levels_used: int
    slippage_bps: Decimal | None  # vs mid
    impact_bps: Decimal | None  # vs best price on that side


class BookSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    best_bid: Decimal
    best_ask: Decimal
    mid: Decimal
    spread_bps: Decimal
    imbalance: dict[int, Decimal | None]


def _levels(rows: Sequence[Level]) -> list[tuple[Decimal, Decimal]]:
    out = [(Decimal(str(row[0])), Decimal(str(row[1]))) for row in rows]
    return [(px, sz) for px, sz in out if px > 0 and sz > 0]


def _touch(bids: Sequence[Level], asks: Sequence[Level]) -> tuple[Decimal, Decimal, Decimal]:
    b, a = _levels(bids), _levels(asks)
    if not b or not a:
        raise ValueError("book needs at least one bid and one ask")
    best_bid, best_ask = max(px for px, _ in b), min(px for px, _ in a)
    return best_bid, best_ask, (best_bid + best_ask) / 2


def imbalance(bids: Sequence[Level], asks: Sequence[Level], depth: int) -> Decimal | None:
    bid_sz = sum((sz for _, sz in _levels(bids)[:depth]), Decimal(0))
    ask_sz = sum((sz for _, sz in _levels(asks)[:depth]), Decimal(0))
    total = bid_sz + ask_sz
    return None if total == 0 else (bid_sz - ask_sz) / total


def spread_bps(bids: Sequence[Level], asks: Sequence[Level]) -> Decimal:
    best_bid, best_ask, mid = _touch(bids, asks)
    return (best_ask - best_bid) / mid * BPS


def summarise_book(
    bids: Sequence[Level], asks: Sequence[Level], *, defaults: Mapping[str, Any] = DEFAULTS
) -> BookSummary:
    best_bid, best_ask, mid = _touch(bids, asks)
    depths = [int(d) for d in cfg("microstructure", defaults)["imbalance_depths"]]
    return BookSummary(
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        spread_bps=(best_ask - best_bid) / mid * BPS,
        imbalance={d: imbalance(bids, asks, d) for d in depths},
    )


def walk_book(
    bids: Sequence[Level],
    asks: Sequence[Level],
    side: Literal["buy", "sell"],
    notional: Decimal | int | str,
    *,
    ct_val: Decimal | int | str = 1,
) -> BookFill:
    want = Decimal(str(notional))
    if want <= 0:
        raise ValueError("notional must be positive")
    unit = Decimal(str(ct_val))
    best_bid, best_ask, mid = _touch(bids, asks)
    book = _levels(asks) if side == "buy" else _levels(bids)
    remaining, spent, qty, used = want, Decimal(0), Decimal(0), 0
    for px, sz in book:
        if remaining <= 0:
            break
        take = min(remaining, px * sz * unit)
        spent += take
        qty += take / px
        remaining -= take
        used += 1
    if qty == 0:
        avg = None
    else:
        avg = spent / qty
    slip = impact = None
    if avg is not None:
        if side == "buy":
            slip, impact = (avg - mid) / mid * BPS, (avg - best_ask) / best_ask * BPS
        else:
            slip, impact = (mid - avg) / mid * BPS, (best_bid - avg) / best_bid * BPS
    return BookFill(
        side=side,
        notional=want,
        filled=remaining <= 0,
        filled_notional=spent,
        base_qty=qty,
        avg_price=avg,
        levels_used=used,
        slippage_bps=slip,
        impact_bps=impact,
    )
