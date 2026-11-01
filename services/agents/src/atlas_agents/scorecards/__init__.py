"""Per-agent daily scorers (ATL-195 onward). Each agent's scorer is its own module here, exposing
``AGENT`` (the slug) and ``score_day(ctx, day)``; ``atlas_agents.agents.scores`` registers it with
:func:`atlas_agents.scoring.register_scorer` and adds its 00:15 schedule hook.

The shared helpers read the day's War Room cards and walk candles; the maths lives in each module
as pure functions so golden tests can check it by hand.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from atlas_agents.scoring import day_bounds

_CARDS = text(
    "SELECT id, created_at, card FROM warroom_messages "
    "WHERE env = :env AND author = :author AND author_type = 'agent' AND kind = 'research_report' "
    "  AND card->>'type' = :card_type AND created_at >= :start AND created_at < :end "
    "ORDER BY created_at, id"
)


@dataclass(frozen=True)
class DayCard:
    message_id: str
    created_at: datetime
    card: Mapping[str, Any]


def day_cards(session: Session, env: str, author: str, card_type: str, day: date) -> list[DayCard]:
    """The ``research_report`` cards of ``card_type`` that ``author`` posted on Baghdad ``day``."""
    start, end = day_bounds(day)
    rows = session.execute(
        _CARDS, {"env": env, "author": author, "card_type": card_type, "start": start, "end": end}
    ).all()
    return [DayCard(str(mid), created_at, card or {}) for mid, created_at, card in rows]


def rate(hits: int, n: int) -> float | None:
    return hits / n if n else None


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


__all__ = ["DayCard", "day_cards", "mean", "rate"]
