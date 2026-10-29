"""check_claude_cli reports install and login state from the CLI's own commands (ATL-83)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from typing import Any

import pytest
from atlas_agents import cli_check
from atlas_agents.cli_check import INSTALL_HINT, LOGIN_HINT, check_claude_cli

STATUS_IN = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "email": "owner@example.com",
    "orgId": "00000000-0000-0000-0000-000000000000",
}


def runner(version: tuple[int, str], status: tuple[int, str]) -> Any:
    calls: list[list[str]] = []

    def run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        code, out = version if argv[1] == "--version" else status
        return subprocess.CompletedProcess(list(argv), code, stdout=out, stderr="")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_logged_in() -> None:
    run = runner((0, "2.1.312 (Claude Code)\n"), (0, json.dumps(STATUS_IN)))
    status = check_claude_cli(cli="/usr/local/bin/claude", runner=run)
    assert status == {"installed": True, "version": "2.1.312", "logged_in": True, "hint": None}
    assert run.calls == [
        ["/usr/local/bin/claude", "--version"],
        ["/usr/local/bin/claude", "auth", "status", "--json"],
    ]
    assert "owner@example.com" not in json.dumps(status)


def test_logged_out() -> None:
    run = runner((0, "2.1.312 (Claude Code)"), (1, json.dumps({"loggedIn": False})))
    assert check_claude_cli(cli="claude", runner=run) == {
        "installed": True,
        "version": "2.1.312",
        "logged_in": False,
        "hint": LOGIN_HINT,
    }


def test_unreadable_status_is_not_logged_in() -> None:
    run = runner((0, "2.0.1"), (0, "not json"))
    assert check_claude_cli(cli="claude", runner=run)["logged_in"] is False


def test_not_installed() -> None:
    status = check_claude_cli(cli="claude-not-installed-atl83")
    assert status == {"installed": False, "version": None, "logged_in": False, "hint": INSTALL_HINT}


def test_cli_runs_without_anthropic_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(cli_check.subprocess, "run", fake_run)
    cli_check._run(["claude", "--version"])
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in seen["env"]
    assert seen["timeout"] == cli_check.TIMEOUT_SECONDS
