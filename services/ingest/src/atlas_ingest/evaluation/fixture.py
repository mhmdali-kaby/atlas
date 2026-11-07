"""The labelled 300-headline fixture (brief §7.2 quality tests): row model, label vocabulary, loader."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EventType = Literal[
    "earnings",
    "guidance",
    "macro_release",
    "central_bank",
    "regulation",
    "hack_exploit",
    "listing",
    "delisting",
    "ETF_flow",
    "M&A",
    "lawsuit",
    "product",
    "partnership",
    "token_unlock",
    "geopolitics",
    "supply_shock",
    "other",
]
EVENT_TYPES: tuple[str, ...] = get_args(EventType)

AssetClass = Literal["crypto", "stock", "etf", "index", "commodity", "other"]

# Which slice of the coverage counts a row belongs to. "negative" = a hard negative:
# a headline that looks like it names an instrument but does not.
Category = Literal["crypto", "equity", "commodity", "macro", "negative"]

# Label vocabulary: every instrument the fixture may expect, in OKX instFamily/base form,
# with the asset class the brief's §4.3 derivation gives it.
INSTRUMENT_ASSET_CLASS: dict[str, str] = {
    **{
        f"{c}-USDT": "crypto"
        for c in (
            "BTC ETH SOL XRP DOGE ADA AVAX LINK DOT TON SUI ARB OP APT NEAR LTC BCH PEPE SHIB "
            "UNI AAVE TRX BNB ATOM FIL INJ TIA WLD ENA ONDO POL ETC HBAR"
        ).split()
    },
    **{
        f"{s}-USDT": "stock"
        for s in "AAPL AMZN COIN CRCL GOOGL HOOD META MSFT MSTR NVDA TSLA PLTR AMD TSM ORCL LLY COST".split()
    },
    **{f"{e}-USDT": "etf" for e in "SPY QQQ IWM TQQQ SQQQ".split()},
    "US100-USDT": "index",
    **{f"{m}-USDT": "commodity" for m in "XAU XAG CL NG XCU".split()},
}

DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "headlines_300.jsonl"


class HeadlineRow(BaseModel):
    """One labelled headline. See docs/handoff/ATL-58-headline-fixture.md for the label rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^h\d{3}$")
    source: str = Field(min_length=1)
    published_at: datetime
    title: str = Field(min_length=10)
    summary: str = ""
    language: str = Field(pattern=r"^[a-z]{2}$")
    category: Category
    expected_instruments: list[str]
    expected_asset_classes: list[AssetClass]
    expected_event_type: EventType
    cluster_label: str = Field(min_length=1)
    notes: str = ""

    @field_validator("published_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("published_at must be UTC (Z or +00:00)")
        return value

    @field_validator("expected_instruments")
    @classmethod
    def _known_instruments(cls, value: list[str]) -> list[str]:
        unknown = [i for i in value if i not in INSTRUMENT_ASSET_CLASS]
        if unknown:
            raise ValueError(f"not in the label vocabulary: {unknown}")
        if len(set(value)) != len(value):
            raise ValueError("duplicate instrument")
        return value

    @model_validator(mode="after")
    def _classes_follow_instruments(self) -> HeadlineRow:
        derived = sorted({INSTRUMENT_ASSET_CLASS[i] for i in self.expected_instruments})
        if sorted(self.expected_asset_classes) != derived:
            raise ValueError(
                f"expected_asset_classes {self.expected_asset_classes} must equal the classes "
                f"of expected_instruments {derived}"
            )
        if self.category == "negative":
            if self.expected_instruments:
                raise ValueError("a hard negative links no instrument")
            if not self.notes.startswith("hard negative"):
                raise ValueError("a hard negative's notes start with 'hard negative'")
        return self


def load_fixture(path: Path | None = None) -> list[HeadlineRow]:
    """Read and validate every row; raises on the first invalid line."""
    target = path or DEFAULT_FIXTURE_PATH
    rows: list[HeadlineRow] = []
    with target.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                rows.append(HeadlineRow.model_validate(json.loads(line)))
            except ValueError as exc:
                raise ValueError(f"{target}:{lineno}: {exc}") from exc
    return rows


def gold_linking(rows: Sequence[HeadlineRow]) -> dict[str, set[str]]:
    return {r.id: set(r.expected_instruments) for r in rows}


def gold_event_types(rows: Sequence[HeadlineRow]) -> dict[str, str]:
    return {r.id: r.expected_event_type for r in rows}


def gold_clusters(rows: Sequence[HeadlineRow]) -> dict[str, str]:
    return {r.id: r.cluster_label for r in rows}
