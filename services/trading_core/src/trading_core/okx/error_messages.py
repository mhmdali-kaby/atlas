"""OKX v5 error codes (``code`` / ``sCode``) as plain English for the owner (ATL-141, brief §11).

The Executor shows these in the War Room when OKX refuses an order. Messages say what happened and,
where it helps, what to change; they never contain keys or account identifiers. Codes come from the
OKX v5 "Error Code" reference (REST trade, account and general sections, checked October 2026). A code
not listed here is shown as ``OKX error <code>: <OKX's own message>``.
"""

from __future__ import annotations

from typing import Final

MESSAGES: Final[dict[str, str]] = {
    # General / system.
    "50001": "OKX is temporarily unavailable. Atlas will retry.",
    "50004": "OKX did not answer in time. Atlas will check whether the order went through before retrying.",
    "50011": "Too many requests to OKX. Atlas is slowing down and will retry.",
    "50013": "OKX is busy. Atlas will retry.",
    "50014": "A required order field was missing in the request to OKX.",
    "50026": "OKX had an internal system error. Atlas will retry.",
    # Order placement.
    "51000": "OKX refused one of the order's values as invalid.",
    "51001": "OKX does not know this instrument. It may have been delisted or renamed.",
    "51004": "The order size is above the limit for this leverage tier. Lower the size or the leverage.",
    "51005": "The order size is above the largest size OKX allows for one order.",
    "51006": "The order price is outside OKX's allowed price range for this instrument right now.",
    "51007": "The order is smaller than one contract.",
    "51008": "Insufficient balance: the account does not have enough available margin for this order.",
    "51009": "OKX has blocked new orders on this instrument.",
    "51010": "This order type is not allowed in the account's current mode. Check the OKX account mode.",
    "51011": "OKX already has an order with this order ID.",
    "51015": "The instrument ID does not match the instrument type.",
    "51016": "OKX already has an order with this client order ID. The earlier attempt may have gone through.",
    "51020": "The order size is below the instrument's minimum size.",
    "51022": "Trading on this contract is suspended.",
    "51023": "There is no open position to act on.",
    "51024": "The trading account is restricted by OKX.",
    "51025": "There are too many open orders. Cancel some before placing more.",
    "51027": "This contract has expired.",
    "51028": "This contract is being delivered and cannot be traded now.",
    "51029": "This contract is being settled and cannot be traded now.",
    "51030": "Funding is being settled. Try again in a minute.",
    "51046": "The take-profit trigger price must be above the order price for a buy.",
    "51047": "The stop-loss trigger price must be below the order price for a buy.",
    "51048": "The take-profit trigger price must be below the order price for a sell.",
    "51049": "The stop-loss trigger price must be above the order price for a sell.",
    "51102": "There are too many pending orders on OKX. Cancel some before placing more.",
    "51103": "There are too many pending orders on this instrument. Cancel some before placing more.",
    "51112": "The closing size is larger than the position you hold.",
    "51116": "The order price or trigger price is outside the allowed limit.",
    "51119": "Insufficient balance: the account does not have enough to place this order.",
    "51120": "The order size is below the minimum OKX accepts.",
    "51121": "The order size must be a whole multiple of the instrument's lot size.",
    "51127": "The available balance is zero.",
    "51131": "Insufficient balance for this order.",
    "51134": "The reduce-only close failed. Check the position and its pending orders.",
    "51169": "There is no position in this direction to reduce or close.",
    "51170": "A reduce-only order cannot be in the same direction as the position.",
    "51201": "A single market order cannot be worth more than OKX's per-order maximum.",
    "51202": "The market order size is above OKX's maximum for this instrument.",
    "51279": "The take-profit trigger price is on the wrong side of the last price.",
    "51280": "The stop-loss trigger price is on the wrong side of the last price.",
    # Cancel.
    "51400": "The order could not be cancelled because OKX has no such order.",
    "51401": "The order was already cancelled.",
    "51402": "The order was already filled, so it could not be cancelled.",
    "51403": "This order type cannot be cancelled.",
    "51410": "The order is already being cancelled.",
    # Amend.
    "51500": "An amendment needs a new price or a new size.",
    "51503": "The order could not be changed because OKX has no such order.",
    "51509": "The order could not be changed because it was cancelled.",
    "51510": "The order could not be changed because it was already filled.",
    # Query.
    "51603": "OKX has no order with this ID.",
    # Account settings.
    "59000": "Leverage or margin settings cannot change while there are open positions or orders.",
    "59102": "The requested leverage is above the instrument's maximum.",
}

UNKNOWN_TEMPLATE: Final = "OKX error {code}: {msg}"


def human_message(code: str, msg: str = "") -> str:
    """The owner-facing message for an OKX error code; unknown codes keep OKX's own text."""
    known = MESSAGES.get(code)
    if known is not None:
        return known
    return UNKNOWN_TEMPLATE.format(code=code, msg=msg.strip() or "no message")


__all__ = ["MESSAGES", "UNKNOWN_TEMPLATE", "human_message"]
