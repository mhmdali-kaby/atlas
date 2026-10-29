"""The CLI subprocess never sees an Anthropic API key or auth token (decision #242)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from atlas_agents import testing as fake
from atlas_agents.registry import AgentSpec
from atlas_agents.session import (
    FORBIDDEN_ENV,
    AgentSession,
    ForbiddenCredentialError,
    cli_env,
    scrub_process_env,
)
from claude_agent_sdk import ClaudeAgentOptions
from sqlalchemy import Engine

from .conftest import Recorder

# Stands in for `claude`: writes the NAMES (never values) of its environment variables, then exits.
STAND_IN = """#!{python}
import os, sys
with open(os.environ["ATLAS_ENV_NAMES"], "w") as fh:
    fh.write("\\n".join(sorted(os.environ)))
sys.exit(3)
"""


@pytest.fixture
def keys_in_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-reach-the-cli")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-should-never-reach-the-cli")


def test_cli_env_and_scrub_remove_both_keys() -> None:
    environ = {"ANTHROPIC_API_KEY": "a", "ANTHROPIC_AUTH_TOKEN": "b", "PATH": "/bin"}
    assert cli_env(environ) == {"PATH": "/bin"}
    assert scrub_process_env(environ) == list(FORBIDDEN_ENV)
    assert environ == {"PATH": "/bin"}


@pytest.mark.parametrize("key", FORBIDDEN_ENV)
def test_passing_a_key_in_options_env_raises(key: str, spec: AgentSpec, engine: Engine) -> None:
    with pytest.raises(ForbiddenCredentialError, match=key):
        AgentSession(spec, env="demo", engine=engine, options=ClaudeAgentOptions(env={key: "x"}))


async def test_options_handed_to_the_client_carry_no_key(
    keys_in_environ: None, spec: AgentSpec, engine: Engine, recorder: Recorder
) -> None:
    client = fake.FakeClaudeClient([[fake.result(tokens_in=1, tokens_out=1)]])
    session = AgentSession(
        spec, env="demo", engine=engine, client_factory=fake.factory(client), publisher=recorder
    )
    result = await session.run("hello")

    assert result.status == "ok"
    assert client.options is not None
    for key in FORBIDDEN_ENV:
        assert key not in client.options.env
        # Removed from this process too, because the SDK merges os.environ into the child's env.
        assert key not in os.environ


async def test_real_sdk_subprocess_env_has_neither_key(
    keys_in_environ: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    spec: AgentSpec,
    engine: Engine,
    recorder: Recorder,
) -> None:
    """Spawn the real SDK transport (default client factory) against a stand-in `claude`."""
    names_file = tmp_path / "names.txt"
    script = tmp_path / "claude"
    script.write_text(STAND_IN.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("ATLAS_ENV_NAMES", str(names_file))
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")

    session = AgentSession(
        spec,
        env="demo",
        engine=engine,
        options=ClaudeAgentOptions(cli_path=str(script)),
        publisher=recorder,
    )
    result = await session.run("hello")

    assert result.status == "error"  # the stand-in exits at once; the run is recorded, not raised
    names = set(names_file.read_text().splitlines())
    assert "ATLAS_ENV_NAMES" in names  # proves this is the real subprocess's environment
    assert "CLAUDE_AGENT_SDK_VERSION" in names  # set by the SDK's own transport
    assert "ANTHROPIC_API_KEY" not in names
    assert "ANTHROPIC_AUTH_TOKEN" not in names
