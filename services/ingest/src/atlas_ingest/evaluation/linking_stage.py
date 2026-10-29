"""The ATL-62 linker as the report's ``linker`` stage (entry point in services/ingest/pyproject.toml).

The fixture is labelled in ``BASE-USDT`` family form over the vocabulary in
:data:`~atlas_ingest.evaluation.fixture.INSTRUMENT_ASSET_CLASS`, so the evaluation universe is that
vocabulary as one swap per family; aliases are built exactly as ``aliases sync`` builds them.
"""

from __future__ import annotations

from functools import cache

from atlas_ingest.aliases import InstrumentInfo, build_alias_rows, make_linker
from atlas_ingest.evaluation.fixture import INSTRUMENT_ASSET_CLASS, HeadlineRow
from atlas_ingest.linking import Linker, load_curated


def reference_instruments() -> list[InstrumentInfo]:
    return [
        InstrumentInfo(f"{family}-SWAP", "SWAP", family, None, None, cls)
        for family, cls in sorted(INSTRUMENT_ASSET_CLASS.items())
    ]


@cache
def reference_linker() -> Linker:
    curated = load_curated()
    instruments = reference_instruments()
    return make_linker(build_alias_rows(instruments, curated), instruments, curated)


def link_headline(row: HeadlineRow) -> list[str]:
    return list(reference_linker().link(row.title, row.summary).instruments)
