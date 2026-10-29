"""Is the Claude Code CLI installed and logged in? (decision #242; for atlas:doctor and the Agents page)

Uses only the CLI's own commands, ``claude --version`` and ``claude auth status --json``. It never
reads credential files, and it runs the CLI with ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN removed so
an API key in the environment can never count as "logged in". The result carries no account
details (no email, org or UID).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from typing import Final, TypedDict

from atlas_agents.session import cli_env

LOGIN_HINT: Final = "Run `claude login` in a terminal on this Mac, then check again."
INSTALL_HINT: Final = (
    "Install Claude Code (https://docs.claude.com/en/docs/claude-code), then run `claude login`."
)
TIMEOUT_SECONDS: Final = 15
VERSION_RE: Final = re.compile(r"\d+\.\d+\.\d+")


class CliStatus(TypedDict):
    installed: bool
    version: str | None
    logged_in: bool
    hint: str | None


Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is ours
        list(argv),
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        env=cli_env(),
        check=False,
    )


def check_claude_cli(*, cli: str = "claude", runner: Runner | None = None) -> CliStatus:
    path = shutil.which(cli) if runner is None else cli
    if path is None:
        return {"installed": False, "version": None, "logged_in": False, "hint": INSTALL_HINT}
    run = runner or _run

    try:
        done = run([path, "--version"])
    except (OSError, subprocess.SubprocessError):
        return {"installed": False, "version": None, "logged_in": False, "hint": INSTALL_HINT}
    if done.returncode != 0:
        return {"installed": False, "version": None, "logged_in": False, "hint": INSTALL_HINT}
    match = VERSION_RE.search(done.stdout or "")
    version = match.group(0) if match else (done.stdout or "").strip() or None

    logged_in = False
    try:
        status = run([path, "auth", "status", "--json"])
        data = json.loads(status.stdout or "{}")
        logged_in = isinstance(data, dict) and data.get("loggedIn") is True
    except (OSError, subprocess.SubprocessError, ValueError):
        logged_in = False

    return {
        "installed": True,
        "version": version,
        "logged_in": logged_in,
        "hint": None if logged_in else LOGIN_HINT,
    }


def main() -> int:
    """``python -m atlas_agents.cli_check``: print the status as JSON; exit 1 unless logged in."""
    status = check_claude_cli()
    print(json.dumps(status))
    return 0 if status["logged_in"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
