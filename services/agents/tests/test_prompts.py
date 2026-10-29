"""ATL-90: versioned prompts — files match the brief, render() blocks, versions and the supervisor."""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from atlas_agents import prompts
from atlas_agents import testing as fake
from atlas_agents.registry import REGISTRY, AgentSpec
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools import ToolContext
from atlas_shared.db import AgentRun, AgentScore, PromptVersion
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Recorder

ENV = "demo"
NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)  # 12:00 Baghdad, 2026-10-29
REPO = Path(__file__).resolve().parents[3]
AGENT_TITLES = {
    "Market Scout": "market_scout",
    "News Intelligence": "news_intelligence",
    "Historian": "historian",
    "Technical Analyst": "technical_analyst",
    "Derivatives & Sentiment Analyst": "derivatives_sentiment",
    "Strategist": "strategist",
    "Devil's Advocate": "devils_advocate",
    "Portfolio Manager": "portfolio_manager",
    "Orchestrator": "orchestrator",
}
SETTINGS: dict[str, int | Decimal] = {
    "max_loss_per_trade_pct": Decimal("1.0"),
    "max_instruments_per_day": 5,
    "min_expected_r": Decimal("1.8"),
    "max_leverage": 5,
}


def _brief_blocks() -> list[list[str]]:
    """Fenced blocks of the brief as quoted in the repository's CLAUDE.md."""
    lines = [re.sub(r"^> ?", "", line) for line in (REPO / "CLAUDE.md").read_text().splitlines()]
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        stripped = line.strip()
        if current is None and stripped.startswith("```"):
            current = []
        elif current is not None and stripped == "```":
            blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    return blocks


def _ctx(**overrides: object) -> prompts.PromptContext:
    base = prompts.PromptContext(env=ENV, today=date(2026, 10, 29), settings=SETTINGS, settings_version=4)
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


# -- the files ---------------------------------------------------------------------------------------


def test_prompt_files_match_the_brief_verbatim() -> None:
    blocks = _brief_blocks()
    preamble = next(b for b in blocks if b and b[0].startswith("SHARED PREAMBLE"))
    assert (prompts.PROMPTS_DIR / "_preamble.md").read_text() == "\n".join(preamble[1:]) + "\n"
    roles = {b[0][len("ROLE: ") :].split(".")[0]: b for b in blocks if b and b[0].startswith("ROLE:")}
    assert set(roles) == set(AGENT_TITLES)
    for title, slug in AGENT_TITLES.items():
        assert (prompts.PROMPTS_DIR / f"{slug}.md").read_text() == "\n".join(roles[title]) + "\n", slug


def test_there_is_one_prompt_file_per_registered_agent() -> None:
    files = {p.stem for p in prompts.PROMPTS_DIR.glob("*.md")} - {"_preamble"}
    assert files == set(REGISTRY) == set(AGENT_TITLES.values())


@pytest.mark.parametrize("slug", sorted(AGENT_TITLES.values()))
def test_every_agent_renders(slug: str) -> None:
    out = prompts.render(slug, _ctx())
    assert "{{" not in out
    assert out.startswith('You are one member of "Atlas"')
    assert "Environment: demo (demo|live). Today: 2026-10-29 " in out
    assert "## Current settings" in out
    assert "## Your last 7 days scorecard" in out


def test_templates_are_filled_from_settings() -> None:
    assert "Pick 0..5 instruments." in prompts.render("market_scout", _ctx())
    assert 'If expected R < 1.8, the answer is "none".' in prompts.render("technical_analyst", _ctx())


# -- missing / unknown variables ----------------------------------------------------------------------


def _tmp_prompts(tmp_path: Path, role: str, slug: str = "historian") -> Path:
    shutil.copy(prompts.PROMPTS_DIR / "_preamble.md", tmp_path / "_preamble.md")
    (tmp_path / f"{slug}.md").write_text(role)
    return tmp_path


def test_unknown_variable_raises(tmp_path: Path) -> None:
    folder = _tmp_prompts(tmp_path, "ROLE: x. Limit {{NOT_A_SETTING}}.\n")
    with pytest.raises(prompts.PromptError, match="NOT_A_SETTING"):
        prompts.render("historian", _ctx(), prompts_dir=folder)


def test_template_without_a_value_raises_instead_of_leaving_braces() -> None:
    with pytest.raises(prompts.PromptError, match="MIN_R"):
        prompts.render("technical_analyst", _ctx(settings=None, settings_version=None))
    with pytest.raises(prompts.PromptError, match="MAX_PICKS"):
        prompts.render("market_scout", _ctx(settings={"max_leverage": 5}))


def test_malformed_braces_raise(tmp_path: Path) -> None:
    folder = _tmp_prompts(tmp_path, "ROLE: x. Broken {{ here.\n")
    with pytest.raises(prompts.PromptError):
        prompts.render("historian", _ctx(), prompts_dir=folder)


def test_no_settings_block_says_so() -> None:
    out = prompts.render("historian", _ctx(settings=None, settings_version=None))
    assert "## Current settings\n\nno settings saved" in out


# -- versions ---------------------------------------------------------------------------------------


def test_one_changed_byte_changes_the_version(tmp_path: Path) -> None:
    original = (prompts.PROMPTS_DIR / "historian.md").read_bytes()
    folder = _tmp_prompts(tmp_path, original.decode())
    same = prompts.load_source("historian", folder)
    assert same.version == prompts.load_source("historian").version
    assert re.fullmatch(r"[0-9a-f]{12}", same.version)
    (folder / "historian.md").write_bytes(original[:-1] + b"!")
    assert prompts.load_source("historian", folder).version != same.version


def test_version_ignores_generated_blocks() -> None:
    src = prompts.load_source("historian")
    a = prompts.render("historian", _ctx(), source=src)
    b = prompts.render("historian", _ctx(settings=None, scores=(prompts.ScoreLine(date(2026, 10, 28), 3),)))
    assert a != b
    assert prompts.load_source("historian").version == src.version


# -- historian from the database ---------------------------------------------------------------------


@pytest.fixture
def db(engine: Engine) -> Iterator[sessionmaker[Session]]:
    conn = engine.connect()
    outer = conn.begin()
    factory = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield factory
    finally:
        outer.rollback()
        conn.close()


def _seed_settings(s: Session) -> None:
    s.execute(text("delete from settings where env = :e"), {"e": ENV})
    data = {
        "risk_profile": "balanced",
        "max_loss_per_trade_pct": "1.0",
        "max_position_pct": "5",
        "max_leverage": 5,
        "max_instruments_per_day": 5,
        "min_expected_r": "1.8",
        "autonomy_level": 2,
        "api_key": "secret-must-not-appear",
    }
    s.execute(
        text(
            "insert into settings (id, env, version, data, risk_profile, autonomy_level, "
            "max_loss_per_trade_pct, max_position_pct, max_leverage, created_at, updated_at) "
            "values (:id, :env, 7, cast(:data as jsonb), 'balanced', 2, 1, 5, 5, :t, :t)"
        ),
        {"id": uuid.uuid4(), "env": ENV, "data": json.dumps(data), "t": NOW - timedelta(hours=1)},
    )


def _tool_ctx(db: sessionmaker[Session], agent: str = "historian") -> ToolContext:
    return ToolContext(env=ENV, session_factory=db, agent=agent, clock=lambda: NOW)


def test_historian_prompt_from_settings_row_and_two_score_days(db: sessionmaker[Session]) -> None:
    with db() as s:
        _seed_settings(s)
        s.execute(delete(AgentScore).where(AgentScore.agent == "historian", AgentScore.env == ENV))
        for day, n, hit, brier in (
            (date(2026, 10, 27), 12, "0.5833", "0.210000"),
            (date(2026, 10, 28), 9, "0.6667", "0.185000"),
        ):
            s.add(
                AgentScore(
                    env=ENV,
                    agent="historian",
                    day=day,
                    n=n,
                    hit_rate=Decimal(hit),
                    brier_score=Decimal(brier),
                    created_at=NOW - timedelta(hours=2),
                    updated_at=NOW - timedelta(hours=2),
                )
            )
        s.commit()

    out = prompts.render("historian", prompts.load_context(_tool_ctx(db)))

    preamble = prompts.fill(
        (prompts.PROMPTS_DIR / "_preamble.md").read_text(), {"ENV": "demo", "DATE": "2026-10-29"}
    ).rstrip("\n")
    assert out.startswith(preamble)
    assert "Environment: demo (demo|live). Today: 2026-10-29 (owner timezone" in out
    assert (prompts.PROMPTS_DIR / "historian.md").read_text().rstrip("\n") in out

    settings = out.split("## Current settings\n\n", 1)[1].split("\n\n## ", 1)[0]
    assert settings.startswith("Settings version 7.")
    pairs = [line[2:].split(": ") for line in settings.splitlines() if line.startswith("- ")]
    assert {k for k, _ in pairs} == {
        "max_loss_per_trade_pct",
        "max_position_pct",
        "max_leverage",
        "max_instruments_per_day",
        "min_expected_r",
        "autonomy_level",
    }
    for _key, value in pairs:
        Decimal(value)  # every value is a number
        assert re.fullmatch(r"-?\d+(\.\d+)?", value)
    assert "secret-must-not-appear" not in out and "balanced" not in settings

    card = out.split("## Your last 7 days scorecard\n\n", 1)[1]
    assert "| 2026-10-28 | 9 | 0.6667 | 0.185 |" in card
    assert "| 2026-10-27 | 12 | 0.5833 | 0.21 |" in card
    assert prompts.NO_SCORES not in card
    assert "{{" not in out


def test_historian_with_no_scores_reads_no_scores_yet(db: sessionmaker[Session]) -> None:
    with db() as s:
        _seed_settings(s)
        s.execute(delete(AgentScore).where(AgentScore.agent == "historian", AgentScore.env == ENV))
        s.commit()
    out = prompts.render("historian", prompts.load_context(_tool_ctx(db)))
    assert out.rstrip("\n").endswith("## Your last 7 days scorecard\n\nno scores yet")
    assert "{{" not in out


# -- supervisor ------------------------------------------------------------------------------------


async def test_supervisor_records_version_once_and_runs_carry_it(
    engine: Engine, spec: AgentSpec, recorder: Recorder, tmp_path: Path
) -> None:
    role = f"ROLE: Test agent {uuid.uuid4().hex}. Answer with statistics.\n"
    folder = _tmp_prompts(tmp_path, role, slug=spec.slug)
    source = prompts.load_source(spec.slug, folder)
    client = fake.FakeClaudeClient([[fake.assistant_text("ok"), fake.result()]])
    try:
        for _ in range(2):
            sup = Supervisor(
                env=ENV,
                engine=engine,
                registry={spec.slug: spec},
                client_factory=fake.factory(client),
                publisher=recorder,
                prompts_dir=folder,
            )
        with Session(engine) as s:
            rows = s.scalars(select(PromptVersion).where(PromptVersion.agent == spec.slug)).all()
        assert len(rows) == 1
        assert rows[0].sha256 == source.sha256 and rows[0].sha256[:12] == source.version
        assert rows[0].body == source.body and rows[0].version == 1

        result = await sup.run(spec.slug, "hello")
        assert result.status == "ok"
        with Session(engine) as s:
            run = s.scalars(select(AgentRun).where(AgentRun.agent == spec.slug)).one()
        assert run.prompt_version == rows[0].version
        system_prompt = client.options.system_prompt if client.options else None
        assert isinstance(system_prompt, str)
        assert system_prompt.startswith('You are one member of "Atlas"')
        assert role.strip() in system_prompt and "{{" not in system_prompt
    finally:
        with engine.begin() as conn:
            conn.execute(delete(PromptVersion).where(PromptVersion.agent == spec.slug))


def test_supervisor_records_a_new_version_when_the_file_changes(
    engine: Engine, spec: AgentSpec, tmp_path: Path
) -> None:
    folder = _tmp_prompts(tmp_path, "ROLE: v1.\n", slug=spec.slug)
    try:
        first = prompts.sync_versions(engine, [spec.slug], prompts_dir=folder)[spec.slug][1]
        (folder / f"{spec.slug}.md").write_text("ROLE: v2.\n")
        second = prompts.sync_versions(engine, [spec.slug], prompts_dir=folder)[spec.slug][1]
        assert (first, second) == (1, 2)
    finally:
        with engine.begin() as conn:
            conn.execute(delete(PromptVersion).where(PromptVersion.agent == spec.slug))
