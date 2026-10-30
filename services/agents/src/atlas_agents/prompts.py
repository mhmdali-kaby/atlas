"""Versioned agent system prompts (brief §7, §7.11; ATL-90).

Each agent's prompt is ``prompts/_preamble.md`` (the brief's SHARED PREAMBLE) followed by
``prompts/<slug>.md`` (the brief's role prompt), then two generated blocks: "Current settings"
(numbers only, the same allow-list as ``atlas_data.get_settings_snapshot``) and "Your last 7 days
scorecard" (from ``agent_scores``).

Version rule: a prompt's version is the first 12 hex of sha256(preamble bytes + role bytes), taken
before any template is filled or block generated. ``prompt_versions`` stores the full sha256, the
raw body and a per-agent sequence number (``version`` int, 1, 2, ...); ``agent_runs.prompt_version``
holds that number, so a run resolves to its exact body and hash.

Templates are ``{{NAME}}``: ENV, DATE, MAX_PICKS (= max_instruments_per_day), MIN_R
(= min_expected_r), and every allow-listed settings key upper-cased. An unknown or unavailable
variable raises PromptError; a rendered prompt never contains ``{{``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from atlas_shared.db import AgentScore, PromptVersion, usage_day
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlas_agents.registry import REGISTRY
from atlas_agents.tools import ToolContext
from atlas_agents.tools.account import load_settings
from atlas_agents.tools.framework import ToolError

PROMPTS_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "prompts"
PREAMBLE_FILE: Final = "_preamble.md"
VERSION_HEX: Final = 12
SCORE_DAYS: Final = 7
NO_SCORES: Final = "no scores yet"
NO_SETTINGS: Final = "no settings saved"

# Template names that map to a settings key under another name (brief §7.1, §7.4).
ALIASES: Final[Mapping[str, str]] = {"MAX_PICKS": "max_instruments_per_day", "MIN_R": "min_expected_r"}

_VAR: Final = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


class PromptError(ValueError):
    """A prompt file is missing, or a template variable is unknown or has no value."""


@dataclass(frozen=True)
class PromptSource:
    """The raw preamble + role for one agent, and its version."""

    agent: str
    preamble: str
    role: str
    sha256: str

    @property
    def version(self) -> str:
        return self.sha256[:VERSION_HEX]

    @property
    def body(self) -> str:
        return self.preamble + self.role


@dataclass(frozen=True)
class ScoreLine:
    day: date
    n: int
    hit_rate: Decimal | None = None
    brier_score: Decimal | None = None
    pnl_attributed_usd: Decimal | None = None
    metrics: Mapping[str, Decimal | None] = field(default_factory=dict)
    """Agent-specific daily numbers written by atlas_agents.scoring (ATL-110), e.g. Market Scout's
    selection_edge_atr or News Intelligence's latency_p90_s, from ``agent_scores.data.metrics``."""


def score_metrics(data: Mapping[str, Any] | None) -> dict[str, Decimal | None]:
    """``data.metrics`` of an agent_scores row as Decimals rounded to 4 places (None kept)."""
    raw = (data or {}).get("metrics") or {}
    out: dict[str, Decimal | None] = {}
    if not isinstance(raw, Mapping):
        return out
    for key, value in raw.items():
        if not isinstance(key, str) or not key.replace("_", "").isalnum():
            continue
        if value is None:
            out[key] = None
        elif isinstance(value, int | float) and not isinstance(value, bool):
            out[key] = Decimal(str(round(float(value), 4)))
    return out


@dataclass(frozen=True)
class PromptContext:
    """Everything a render needs besides the files. ``settings`` is None when none are saved."""

    env: str
    today: date
    settings: Mapping[str, int | Decimal] | None = None
    settings_version: int | None = None
    scores: Sequence[ScoreLine] = field(default_factory=tuple)


def load_source(agent: str, prompts_dir: Path | None = None) -> PromptSource:
    folder = prompts_dir or PROMPTS_DIR
    try:
        preamble = (folder / PREAMBLE_FILE).read_bytes()
        role = (folder / f"{agent}.md").read_bytes()
    except FileNotFoundError as exc:
        raise PromptError(f"no prompt file for agent [{agent}]: {exc.filename}") from None
    return PromptSource(
        agent=agent,
        preamble=preamble.decode("utf-8"),
        role=role.decode("utf-8"),
        sha256=hashlib.sha256(preamble + role).hexdigest(),
    )


def has_prompt(agent: str, prompts_dir: Path | None = None) -> bool:
    return ((prompts_dir or PROMPTS_DIR) / f"{agent}.md").is_file()


def _number(value: int | Decimal) -> str:
    if isinstance(value, Decimal):
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(int(value))


def variables(ctx: PromptContext) -> dict[str, str]:
    values: dict[str, str] = {"ENV": ctx.env, "DATE": ctx.today.isoformat()}
    settings = ctx.settings or {}
    for key, value in settings.items():
        values[key.upper()] = _number(value)
    for name, key in ALIASES.items():
        if key in settings:
            values[name] = _number(settings[key])
    return values


def fill(template: str, values: Mapping[str, str], *, agent: str = "?") -> str:
    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise PromptError(f"prompt for [{agent}] uses unknown or unset variable {{{{{name}}}}}")
        return values[name]

    out = _VAR.sub(sub, template)
    if "{{" in out:
        raise PromptError(f"prompt for [{agent}] has an unclosed or malformed '{{{{' template")
    return out


def settings_block(ctx: PromptContext) -> str:
    lines = ["## Current settings", ""]
    if ctx.settings is None:
        lines.append(NO_SETTINGS)
        return "\n".join(lines)
    lines.append(f"Settings version {ctx.settings_version}. Read-only; only the owner can change them.")
    lines.append("")
    lines.extend(f"- {key}: {_number(ctx.settings[key])}" for key in sorted(ctx.settings))
    return "\n".join(lines)


def scorecard_block(ctx: PromptContext) -> str:
    lines = ["## Your last 7 days scorecard", ""]
    if not ctx.scores:
        lines.append(NO_SCORES)
        return "\n".join(lines)

    def show(value: Decimal | None) -> str:
        return "n/a" if value is None else _number(value)

    with_metrics = any(s.metrics for s in ctx.scores)
    if with_metrics:
        lines.append("| day | n | hit_rate | brier_score | pnl_attributed_usd | metrics |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
    else:
        lines.append("| day | n | hit_rate | brier_score | pnl_attributed_usd |")
        lines.append("| --- | --- | --- | --- | --- |")
    for s in sorted(ctx.scores, key=lambda s: s.day, reverse=True):
        row = (
            f"| {s.day.isoformat()} | {s.n} | {show(s.hit_rate)} | {show(s.brier_score)} "
            f"| {show(s.pnl_attributed_usd)} |"
        )
        if with_metrics:
            metrics = ", ".join(f"{k}={show(v)}" for k, v in sorted(s.metrics.items()))
            row += f" {metrics or 'n/a'} |"
        lines.append(row)
    return "\n".join(lines)


def render(
    agent_slug: str,
    ctx: PromptContext,
    *,
    source: PromptSource | None = None,
    prompts_dir: Path | None = None,
) -> str:
    """The full system prompt: preamble, role, Current settings, Your last 7 days scorecard."""
    src = source or load_source(agent_slug, prompts_dir)
    values = variables(ctx)
    parts = [
        fill(src.preamble, values, agent=agent_slug).rstrip("\n"),
        fill(src.role, values, agent=agent_slug).rstrip("\n"),
        settings_block(ctx),
        scorecard_block(ctx),
    ]
    out = "\n\n".join(parts) + "\n"
    if "{{" in out:  # generated blocks hold only numbers and dates, but never let braces through
        raise PromptError(f"rendered prompt for [{agent_slug}] still contains '{{{{'")
    return out


def load_context(tool_ctx: ToolContext) -> PromptContext:
    """Settings in force and the last 7 Baghdad days of scores (before today) for ``tool_ctx``'s
    agent, bounded by the replay cutoff like every tool."""
    now = tool_ctx.now()
    today = usage_day(now)
    settings: dict[str, int | Decimal] | None
    version: int | None
    with tool_ctx.session_factory() as session:
        try:
            version, settings, _as_of = load_settings(session, tool_ctx)
        except ToolError as exc:
            if exc.code != "no_data":
                raise
            version, settings = None, None
        rows = session.scalars(
            select(AgentScore)
            .where(
                AgentScore.agent == tool_ctx.agent,
                AgentScore.env == tool_ctx.env,
                AgentScore.day >= today - timedelta(days=SCORE_DAYS),
                AgentScore.day < today,
                tool_ctx.bound(AgentScore.created_at),
            )
            .order_by(AgentScore.day.desc())
        ).all()
    scores = tuple(
        ScoreLine(
            day=r.day,
            n=r.n,
            hit_rate=r.hit_rate,
            brier_score=r.brier_score,
            pnl_attributed_usd=r.pnl_attributed_usd,
            metrics=score_metrics(r.data),
        )
        for r in rows
    )
    return PromptContext(
        env=tool_ctx.env, today=today, settings=settings, settings_version=version, scores=scores
    )


def renderer(tool_ctx: ToolContext, source: PromptSource) -> Callable[[], str]:
    """A zero-argument hook that renders ``source`` with fresh settings and scores."""
    return lambda: render(source.agent, load_context(tool_ctx), source=source)


def sync_versions(
    engine: Engine,
    agents: Iterable[str] | None = None,
    *,
    prompts_dir: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, tuple[PromptSource, int]]:
    """Insert every agent's current prompt version into ``prompt_versions`` if it is not there yet.

    Returns slug -> (source, row version number). Agents without a prompt file are skipped.
    Idempotent: (agent, sha256) is unique, so a second start inserts nothing.
    """
    now = (clock or (lambda: datetime.now(UTC)))()
    out: dict[str, tuple[PromptSource, int]] = {}
    for agent in agents if agents is not None else REGISTRY:
        if not has_prompt(agent, prompts_dir):
            continue
        src = load_source(agent, prompts_dir)
        out[agent] = (src, _ensure_row(engine, src, now))
    return out


def _ensure_row(engine: Engine, src: PromptSource, now: datetime) -> int:
    for _attempt in range(3):
        with Session(engine) as db:
            existing = db.scalar(
                select(PromptVersion.version).where(
                    PromptVersion.agent == src.agent, PromptVersion.sha256 == src.sha256
                )
            )
            if existing is not None:
                return int(existing)
            latest = db.scalar(
                select(func.max(PromptVersion.version)).where(PromptVersion.agent == src.agent)
            )
            number = int(latest or 0) + 1
            db.add(
                PromptVersion(
                    agent=src.agent,
                    version=number,
                    sha256=src.sha256,
                    body=src.body,
                    created_at=now,
                    updated_at=now,
                )
            )
            try:
                db.commit()
            except IntegrityError:  # another process inserted the same hash or number first
                db.rollback()
                continue
            return number
    raise PromptError(f"could not record prompt version {src.version} for [{src.agent}]")


__all__ = [
    "ALIASES",
    "NO_SCORES",
    "NO_SETTINGS",
    "PROMPTS_DIR",
    "PromptContext",
    "PromptError",
    "PromptSource",
    "ScoreLine",
    "fill",
    "has_prompt",
    "load_context",
    "load_source",
    "render",
    "renderer",
    "sync_versions",
]
