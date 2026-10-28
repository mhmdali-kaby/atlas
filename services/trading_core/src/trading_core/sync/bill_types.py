"""How Atlas reads OKX account bills (ATL-11, brief §13 "PnL truth: compute from OKX bills/fills").

Source: OKX v5 API docs, "Trading Account → Get bills details (last 7 days)",
``GET /api/v5/account/bills`` (and ``/account/bills-archive``, same fields), response parameters
``type`` ("Bill type"), ``balChg``, ``fee`` and ``pnl``:

- ``type`` "1" = Transfer, "2" = Trade, "8" = Funding fee (others: 3 Delivery, 4 Forced repayment,
  5 Liquidation, 6 Margin transfer, 7 Interest deduction, 9 ADL, 10 Clawback, ... are stored raw).
- **Fees are not a bill type.** Every bill carries its own ``fee`` field; OKX reports it signed from
  the account's view (negative = fee charged, positive = maker rebate). Fees paid = -sum(``fee``).
- **Funding** is the ``balChg`` of bills with ``type`` = "8" (positive = received, negative = paid).
- **Realised PnL** is the ``pnl`` field of any bill (filled on closing trades, liquidation, delivery);
  it excludes the fee, so net realised = Σ ``pnl`` + Σ ``fee``.

The Laravel Portfolio API sums from the same codes in ``App\\Support\\Portfolio\\BillType`` (ATL-12);
keep the two in step.
"""

from __future__ import annotations

from typing import Final

TRANSFER: Final = "1"
TRADE: Final = "2"
FUNDING_FEE: Final = "8"

FUNDING_TYPES: Final = frozenset({FUNDING_FEE})
"""Bill types whose ``bal_chg`` is funding paid/received."""

FEE_FIELD: Final = "fee"
"""Column summed (negated) for fees paid, over bills of every type."""

PNL_FIELD: Final = "pnl"
"""Column summed for realised PnL, over bills of every type."""
