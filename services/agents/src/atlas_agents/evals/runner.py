"""Run an agent's eval cases on replayed days and score them (brief §7.11, §9; decision #245).

For each case the runner opens one transaction on the checkout's test DB, loads the case snapshot,
builds the agent through :class:`atlas_agents.supervisor.Supervisor` with ``replay_as_of`` (so every
tool query is bounded by the cutoff and the agent gets its normal options and prompt), sends the
case prompt once, scores the answer, and rolls the transaction back. A case that fails in any way is
recorded as failed and the runner moves on to the next one.

``fake=True`` replays the case's scripted answer through :class:`EvalFakeClient` (tool calls still
run for real); otherwise the real Claude CLI login is used. Only fake mode ever runs under pytest.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from atlas_shared.defaults import DEFAULTS
from atlas_shared.events import EventEnvelope
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from atlas_agents.evals import properties as props
from atlas_agents.evals.case import EvalCase, discover, evals_root, load_case
from atlas_agents.evals.fake import EvalFakeClient
from atlas_agents.evals.snapshot import load_snapshot
from atlas_agents.registry import REGISTRY
from atlas_agents.session import ClaudeClient, ClientFactory, RunResult, default_client_factory
from atlas_agents.supervisor import Supervisor
from atlas_agents.tools import ToolContext

log = logging.getLogger(__name__)

BASELINE_FILE = "baseline.json"


@dataclass
class Recorder:
    """Swallows bus events: an eval never publishes agent states or alerts to the live bus."""

    events: list[tuple[str, EventEnvelope]] = field(default_factory=list)

    def __call__(self, stream: str, envelope: EventEnvelope) -> None:
        self.events.append((stream, envelope))


# -- transcript --------------------------------------------------------------------------------------


def _tool_output(content: Any) -> list[dict[str, Any]]:
    """A ToolResultBlock's content as JSON objects (the tools' {as_of, source, data} envelopes)."""
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        texts.extend(str(item.get("text", "")) for item in content if isinstance(item, Mapping))
    out: list[dict[str, Any]] = []
    for text in texts:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        out.append(parsed if isinstance(parsed, dict) else {"text": text})
    return out


def transcript(result: RunResult, prompt: str = "") -> props.Transcript:
    calls: list[tuple[str, dict[str, Any]]] = []
    outputs: list[dict[str, Any]] = []
    texts: list[str] = []
    for message in result.messages:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    texts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    calls.append((block.name, dict(block.input)))
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    outputs.extend(_tool_output(block.content))
    # What the War Room shows is the run's text plus anything posted with post_message/reply.
    posted = [str(args.get("content_md", "")) for name, args in calls if name in props.POST_TOOLS]
    text = "\n\n".join(t for t in [*texts, *posted] if t)
    return props.Transcript(
        status=result.status,
        text=text,
        card=props.card_from(text, calls),
        tool_calls=calls,
        tool_outputs=outputs,
        error=result.error,
        prompt=prompt,
    )


# -- one case ----------------------------------------------------------------------------------------


@dataclass
class CaseResult:
    case_id: str
    status: str
    properties: list[props.PropertyResult]
    error: str | None = None
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    turns: int = 0
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for p in self.properties if p.passed)

    @property
    def total(self) -> int:
        return len(self.properties)

    def as_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "error": self.error,
            "passed": self.passed,
            "total": self.total,
            "pass_rate": rate(self.passed, self.total),
            "properties": [p.as_json() for p in self.properties],
            "tool_calls": self.tool_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "turns": self.turns,
        }


def rate(passed: int, total: int) -> float:
    """Pass rate in percent, two decimals; 0 when there is nothing to pass."""
    return round(100.0 * passed / total, 2) if total else 0.0


def _failed(case_id: str, names: Sequence[str], status: str, error: str) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        status=status,
        error=error,
        properties=[props.PropertyResult(n, False, f"case {status}: {error}") for n in names or ["case"]],
    )


async def run_case(case: EvalCase, engine: Engine, *, fake: bool) -> CaseResult:
    """Run one case in a transaction that is always rolled back."""
    if fake and case.fake is None:
        return _failed(case.case_id, list(case.properties), "error", "case has no fake script for --fake")
    spec = REGISTRY.get(case.agent)
    if spec is None:
        return _failed(case.case_id, list(case.properties), "error", f"unknown agent {case.agent}")
    spec = dataclasses.replace(spec, enabled=True)  # an eval may run an agent that is off in Settings
    recorder = Recorder()
    conn = engine.connect()
    outer = conn.begin()
    try:
        load_snapshot(conn, case.snapshot(), replay_as_of=case.replay_as_of)
        factory = _client_factory(case, conn, recorder) if fake else default_client_factory
        # Session(conn) joins the open transaction, so the run's own writes roll back with it.
        supervisor = Supervisor(
            env=case.env,
            engine=cast(Engine, conn),
            registry={spec.slug: spec},
            client_factory=factory,
            publisher=recorder,
            replay_as_of=case.replay_as_of,
        )
        try:
            result = await supervisor.run(
                spec.slug,
                case.prompt,
                trigger="eval",
                task=f"eval {case.case_id}",
                correlation_id=f"eval-{case.case_id}",
            )
        finally:
            await supervisor.close()
        t = transcript(result, case.prompt)
        return CaseResult(
            case_id=case.case_id,
            status=result.status,
            error=result.error,
            properties=props.score(t, case.properties),
            tool_calls=len(t.tool_calls),
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            turns=result.turns,
            tool_outputs=t.tool_outputs,
        )
    except Exception as exc:  # one broken case never stops the run
        log.exception("eval case %s failed", case.case_id)
        return _failed(case.case_id, list(case.properties), "error", f"{type(exc).__name__}: {exc}")
    finally:
        outer.rollback()
        conn.close()


def _client_factory(case: EvalCase, conn: Any, recorder: Recorder) -> ClientFactory:
    assert case.fake is not None
    ctx = ToolContext(
        env=case.env,
        session_factory=sessionmaker(bind=conn, join_transaction_mode="create_savepoint"),
        agent=case.agent,
        correlation_id=f"eval-{case.case_id}",
        replay_as_of=case.replay_as_of,
        publish=recorder,
    )
    steps = case.fake.steps

    def make(options: ClaudeAgentOptions) -> ClaudeClient:
        return EvalFakeClient(steps, ctx, options)

    return make


# -- a whole agent -----------------------------------------------------------------------------------


@dataclass
class RunReport:
    agent: str
    mode: str
    label: str | None
    started_at: datetime
    finished_at: datetime
    cases: list[CaseResult]
    baseline: dict[str, Any] | None = None

    @property
    def passed(self) -> int:
        return sum(c.passed for c in self.cases)

    @property
    def total(self) -> int:
        return sum(c.total for c in self.cases)

    @property
    def pass_rate(self) -> float:
        return rate(self.passed, self.total)

    def as_json(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "mode": self.mode,
            "label": self.label,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "cases": [c.as_json() for c in self.cases],
            "overall": {"passed": self.passed, "total": self.total, "pass_rate": self.pass_rate},
            "baseline": self.baseline,
        }


async def run_agent(
    agent: str,
    engine: Engine,
    *,
    fake: bool,
    cases_root: Path | None = None,
    case_id: str | None = None,
    label: str | None = None,
    on_case: Callable[[CaseResult], None] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunReport:
    started = clock()
    results: list[CaseResult] = []
    for path in discover(agent, cases_root, case_id):
        try:
            case = load_case(path)
        except Exception as exc:  # an unreadable case.yaml is a failed case, not a crash
            result = _failed(path.name, [], "invalid", f"{type(exc).__name__}: {exc}")
        else:
            if case.agent != agent:
                result = _failed(case.case_id, list(case.properties), "invalid", f"case is for {case.agent}")
            else:
                result = await run_case(case, engine, fake=fake)
        results.append(result)
        if on_case is not None:
            on_case(result)
    return RunReport(
        agent=agent,
        mode="fake" if fake else "real",
        label=label,
        started_at=started,
        finished_at=clock(),
        cases=results,
    )


def write_results(report: RunReport, results_dir: Path | None = None) -> Path:
    """``evals/results/<agent>/<UTC timestamp>.json``."""
    folder = (results_dir or evals_root() / "results") / report.agent
    folder.mkdir(parents=True, exist_ok=True)
    stamp = report.started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f"{stamp}.json"
    path.write_text(json.dumps(report.as_json(), indent=2) + "\n", encoding="utf-8")
    return path


def overall_rate(results: Mapping[str, Any]) -> float:
    return float(results["overall"]["pass_rate"])


def check_baseline(report: RunReport, baseline_path: Path, max_drop: float | None = None) -> bool:
    """True when the run is within ``max_drop`` points of the baseline (or there is no baseline).
    Records the comparison on ``report.baseline``."""
    drop = float(DEFAULTS["agents"]["evals"]["max_drop_points"]) if max_drop is None else max_drop
    if not baseline_path.is_file():
        report.baseline = {"path": str(baseline_path), "pass_rate": None, "max_drop_points": drop, "ok": True}
        return True
    base = overall_rate(json.loads(baseline_path.read_text(encoding="utf-8")))
    ok = report.pass_rate >= base - drop
    report.baseline = {"path": str(baseline_path), "pass_rate": base, "max_drop_points": drop, "ok": ok}
    return ok


def baseline_path(agent: str, cases_root: Path | None = None) -> Path:
    return (cases_root or evals_root()) / agent / BASELINE_FILE


__all__ = [
    "CaseResult",
    "RunReport",
    "baseline_path",
    "check_baseline",
    "rate",
    "run_agent",
    "run_case",
    "transcript",
    "write_results",
]
