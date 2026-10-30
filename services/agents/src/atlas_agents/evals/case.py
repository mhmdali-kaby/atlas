"""Eval cases on disk (brief §7.11, §9; decision #245).

A case is a directory ``evals/<agent>/<case_id>/``::

    case.yaml      agent, replay_as_of, prompt, properties, and (for --fake) the scripted answer
    data/<table>.json   the snapshot: a JSON list of rows per Laravel-owned table

``case.yaml``::

    agent: historian
    replay_as_of: "2026-10-20T08:00:00Z"     # timezone required
    env: demo                                 # optional, default env.default
    description: one line                     # optional
    prompt: "@Historian regime check on BTC-USDT-SWAP"
    properties:                               # name -> parameters ({} or null for none)
      valid_schema: { model: HistoryVerdict }
      no_invented_numbers: {}
      verdict_in: { values: [supports, weakly_supports] }
      no_trade: {}
      mentions_n: { min: 20 }
    fake:                                     # used only with --fake
      steps:
        - tool: okx_market.get_candles        # run against the real tool, replay-bounded
          input: { inst_id: BTC-USDT-SWAP, bar: 1H, limit: 48 }
        - text: "History weakly supports ..."
        - tool: warroom.post_message
          input: { kind: research_report, content_md: "...", card: { ... } }
        - result: { tokens_in: 1200, tokens_out: 300, turns: 3 }
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # no stubs in the workspace; parsed into pydantic
from atlas_shared.defaults import DEFAULTS
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

CASE_FILE = "case.yaml"
DATA_DIR = "data"


def evals_root() -> Path:
    """``services/agents/evals`` in this checkout (the package is installed editable)."""
    # services/agents/src/atlas_agents/evals/case.py -> parents[3] is services/agents.
    return Path(__file__).resolve().parents[3] / "evals"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FakeStep(_Strict):
    """One scripted step. Exactly one of ``text``, ``tool`` or ``result`` is set."""

    text: str | None = None
    tool: str | None = Field(default=None, description="server.name, or the full mcp__server__name")
    input: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None

    def model_post_init(self, _context: Any) -> None:
        kinds = [k for k in ("text", "tool", "result") if getattr(self, k) is not None]
        if len(kinds) != 1:
            raise ValueError(f"a fake step needs exactly one of text, tool, result; got {kinds or 'none'}")


class FakeScript(_Strict):
    steps: list[FakeStep]


class EvalCase(_Strict):
    agent: str
    replay_as_of: AwareDatetime
    prompt: str = Field(min_length=1)
    properties: dict[str, dict[str, Any]]
    env: str = Field(default_factory=lambda: str(DEFAULTS["env"]["default"]))
    description: str | None = None
    fake: FakeScript | None = None

    # Set by load_case, not read from the file.
    case_id: str = ""
    path: Path = Path()

    @field_validator("properties", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: (v if v is not None else {}) for k, v in value.items()}
        return value

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """``data/<table>.json`` files, table name -> rows, in file-name order."""
        data_dir = self.path / DATA_DIR
        if not data_dir.is_dir():
            return {}
        out: dict[str, list[dict[str, Any]]] = {}
        for file in sorted(data_dir.glob("*.json")):
            rows = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ValueError(f"{file}: a snapshot file must hold a JSON list of row objects")
            out[file.stem] = rows
        return out


def load_case(path: Path) -> EvalCase:
    raw = yaml.safe_load((path / CASE_FILE).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path / CASE_FILE}: expected a mapping")
    # YAML parses an unquoted timestamp itself; keep the file's own value either way.
    if isinstance(raw.get("replay_as_of"), datetime):
        raw["replay_as_of"] = raw["replay_as_of"].isoformat()
    case = EvalCase.model_validate(raw)
    case.case_id = path.name
    case.path = path
    return case


def discover(agent: str, root: Path | None = None, case_id: str | None = None) -> list[Path]:
    """Case directories for ``agent`` (sorted), or just ``case_id``."""
    base = (root or evals_root()) / agent
    if case_id is not None:
        path = base / case_id
        if not (path / CASE_FILE).is_file():
            raise FileNotFoundError(f"no case {case_id} for {agent} under {base}")
        return [path]
    return sorted(p for p in base.iterdir() if (p / CASE_FILE).is_file()) if base.is_dir() else []
