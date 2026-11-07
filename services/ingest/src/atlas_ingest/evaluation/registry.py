"""Where later tasks plug their linker, event classifier and clusterer into the report.

Two ways in, both resolved by :func:`load_entry_points` before the report runs:

* call ``register_linker`` / ``register_classifier`` / ``register_clusterer`` at import time of a module
  listed under the ``atlas_ingest.evaluation`` entry-point group, or
* point the entry point straight at the callable: ``linker = "atlas_ingest.linking:link_row"`` in
  ``services/ingest/pyproject.toml``'s ``[project.entry-points."atlas_ingest.evaluation"]`` table
  (names ``linker``, ``classifier``, ``clusterer``).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from importlib.metadata import entry_points
from typing import Literal, cast

from atlas_ingest.evaluation.fixture import HeadlineRow

Linker = Callable[[HeadlineRow], Iterable[str]]
"""Row → instrument ids in instFamily form (``BTC-USDT``)."""
Classifier = Callable[[HeadlineRow], str]
"""Row → one of the 17 event types."""
Clusterer = Callable[[Sequence[HeadlineRow]], Mapping[str, str]]
"""All rows → {row id: cluster label}. Labels are opaque; only equality matters."""

Stage = Literal["linker", "classifier", "clusterer"]
STAGES: tuple[Stage, ...] = ("linker", "classifier", "clusterer")
ENTRY_POINT_GROUP = "atlas_ingest.evaluation"

_registry: dict[str, object] = {}


def register_linker(fn: Linker) -> Linker:
    _registry["linker"] = fn
    return fn


def register_classifier(fn: Classifier) -> Classifier:
    _registry["classifier"] = fn
    return fn


def register_clusterer(fn: Clusterer) -> Clusterer:
    _registry["clusterer"] = fn
    return fn


def get_linker() -> Linker | None:
    return cast(Linker | None, _registry.get("linker"))


def get_classifier() -> Classifier | None:
    return cast(Classifier | None, _registry.get("classifier"))


def get_clusterer() -> Clusterer | None:
    return cast(Clusterer | None, _registry.get("clusterer"))


def clear() -> None:
    _registry.clear()


def register_dotted(stage: Stage, target: str) -> None:
    """Register ``module:attr`` for a stage (CLI overrides use this)."""
    module_name, _, attr = target.partition(":")
    obj = importlib.import_module(module_name)
    for part in filter(None, attr.split(".")):
        obj = getattr(obj, part)
    if not callable(obj):
        raise TypeError(f"{target} is not callable")
    _registry[stage] = obj


def load_entry_points() -> None:
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        loaded = ep.load()
        if ep.name in STAGES and callable(loaded):
            _registry.setdefault(ep.name, loaded)
