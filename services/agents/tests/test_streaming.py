"""warroom.thinking streaming and the final run message (ATL-88). Runs AgentSession against
FakeClaudeClient (needs the test DB: `cd backend && php artisan test` first)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from atlas_agents import testing as fake
from atlas_agents.registry import AgentSpec
from atlas_agents.session import AgentSession, RunResult
from atlas_agents.streaming import ThinkingPublisher, observer_factory
from atlas_agents.supervisor import Supervisor
from atlas_shared.events import EventEnvelope
from sqlalchemy import Engine

CONFIG = {
    "max_events_per_second": 10,
    "event_text_max_chars": 4000,
    "tool_input_max_chars": 200,
    "tool_output_max_chars": 120,
}


@dataclass
class FakeTime:
    """A monotonic clock that moves ``step`` seconds on every read; sleep() moves it too."""

    t: float = 1000.0
    step: float = 0.005
    slept: list[float] = field(default_factory=list)

    def clock(self) -> float:
        self.t += self.step
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


@dataclass
class TimedRecorder:
    time: FakeTime
    events: list[tuple[float, str, EventEnvelope]] = field(default_factory=list)

    def __call__(self, stream: str, envelope: EventEnvelope) -> None:
        self.events.append((self.time.t, stream, envelope))

    def of(self, type_: str) -> list[tuple[float, EventEnvelope]]:
        return [(t, e) for t, _s, e in self.events if e.type == type_]

    def deltas(self) -> list[tuple[float, EventEnvelope]]:
        """The thinking events before run_end."""
        return [(t, e) for t, e in self.of("warroom.thinking") if e.payload["delta_type"] != "run_end"]

    def run_ends(self) -> list[EventEnvelope]:
        return [e for _t, e in self.of("warroom.thinking") if e.payload["delta_type"] == "run_end"]


def _session(
    spec: AgentSpec, engine: Engine, client: fake.FakeClaudeClient, rec: TimedRecorder, time: FakeTime
) -> AgentSession:
    def make(
        run_id: uuid.UUID, correlation_id: str | None, thread_id: str | None, reply_to: str | None
    ) -> ThinkingPublisher:
        return ThinkingPublisher(
            agent=spec.slug,
            env="demo",
            run_id=run_id,
            publish=rec,
            correlation_id=correlation_id,
            thread_id=thread_id,
            reply_to=reply_to,
            config=CONFIG,
            clock=time.clock,
            sleep=time.sleep,
        )

    return AgentSession(
        spec,
        env="demo",
        engine=engine,
        client_factory=fake.factory(client),
        publisher=rec,
        observer_factory=make,
    )


def _max_in_any_second(times: list[float]) -> int:
    return max(sum(1 for u in times if t <= u < t + 1.0) for t in times)


def _tool_output(as_of: str, rows: int = 3) -> str:
    return json.dumps(
        {"as_of": as_of, "source": "atlas.candles", "truncated": False, "data": {"rows": list(range(rows))}}
    )


async def test_200_thinking_deltas_are_throttled_coalesced_and_assembled_in_order(
    spec: AgentSpec, engine: Engine
) -> None:
    time = FakeTime(step=0.005)  # 200 deltas over ~1 s of (fake) stream time
    rec = TimedRecorder(time)
    deltas = [f"step {i:03d}; " for i in range(200)]
    client = fake.FakeClaudeClient(
        [
            [
                *(fake.thinking_delta(d) for d in deltas),
                fake.block_stop(),
                fake.thinking("".join(deltas)),  # the whole block the CLI repeats after streaming
                fake.assistant_text("Regime: trend (p=0.78)."),
                fake.result(tokens_in=1200, tokens_out=300, turns=1, cost_usd=0.0123456789),
            ]
        ]
    )
    result = await _session(spec, engine, client, rec, time).run("regime table", correlation_id="cycle-1")
    assert result.status == "ok"

    thinking = rec.deltas()
    assert 2 <= len(thinking) <= 30
    assert _max_in_any_second([t for t, _ in thinking]) <= 10
    seqs = [e.payload["seq"] for _, e in rec.of("warroom.thinking")]
    assert seqs == list(range(1, len(thinking) + 2))  # run_end takes the last seq
    for _, e in thinking:
        assert e.source == f"agent:{spec.slug}"
        assert e.correlation_id == "cycle-1"
        assert e.payload["run_id"] == str(result.run_id)
        assert e.payload["agent"] == spec.slug
    streamed = "".join(e.payload["text"] for _, e in thinking if e.payload["delta_type"] == "thinking")
    assert streamed == "".join(deltas)  # coalesced, nothing lost, no duplicate from the whole block
    assert [e.payload["delta_type"] for _, e in thinking][-1] == "text"

    finals = rec.of("warroom.message")
    assert len(finals) == 1
    final_ts, final = finals[0]
    assert final_ts >= thinking[-1][0]
    p = final.payload
    assert p["author"] == spec.slug and p["author_type"] == "agent" and p["kind"] == "text"
    assert final.source == f"agent:{spec.slug}"
    assert p["content_md"] == "Regime: trend (p=0.78)."
    assert p["reasoning_md"] == "".join(deltas)
    positions = [p["reasoning_md"].index(d) for d in deltas]
    assert positions == sorted(positions)
    assert p["tokens_in"] == 1200 and p["tokens_out"] == 300
    assert p["cost_usd"] == "0.012346"
    assert p["tool_calls"] == []
    assert p["run_id"] == str(result.run_id)
    (end,) = rec.run_ends()
    assert end.payload["run_id"] == str(result.run_id) and end.payload["status"] == "ok"
    assert rec.events[-1][2] is end  # the stream ends after the final message


async def test_deltas_arriving_slower_than_the_limit_each_get_an_event(
    spec: AgentSpec, engine: Engine
) -> None:
    time = FakeTime(step=0.2)
    rec = TimedRecorder(time)
    client = fake.FakeClaudeClient([[*(fake.thinking_delta(f"d{i} ") for i in range(5)), fake.result()]])
    await _session(spec, engine, client, rec, time).run("x")
    assert [e.payload["text"] for _, e in rec.deltas()] == [f"d{i} " for i in range(5)]


async def test_final_tool_calls_list_every_scripted_call_with_name_input_and_as_of(
    spec: AgentSpec, engine: Engine
) -> None:
    time = FakeTime(step=0.001)
    rec = TimedRecorder(time)
    calls = [
        (
            "mcp__history__compute_stats",
            {"inst_id": "XAU-USDT-SWAP", "bar": "1H", "lookback": 500},
            "2026-10-29T07:00:00+00:00",
        ),
        ("mcp__history__detect_regime", {"inst_id": "XAU-USDT-SWAP"}, "2026-10-29T07:00:00+00:00"),
        (
            "mcp__okx_market__get_candles",
            {"inst_id": "BTC-USDT-SWAP", "bar": "4H", "limit": 500},
            "2026-10-29T04:00:00+00:00",
        ),
    ]
    steps: list[Any] = [fake.thinking("Check XAU stats first.")]
    for i, (name, args, as_of) in enumerate(calls):
        steps.append(fake.tool_use(name, args, tool_use_id=f"toolu_{i}"))
        steps.append(fake.tool_result(_tool_output(as_of, rows=200), tool_use_id=f"toolu_{i}"))
    steps.append(
        fake.tool_use(
            "mcp__warroom__post_message", {"kind": "text", "content_md": "x" * 500}, tool_use_id="toolu_err"
        )
    )
    steps.append(
        fake.tool_result(
            '{"error":"content_too_long","message":"..."}', tool_use_id="toolu_err", is_error=True
        )
    )
    steps += [fake.assistant_text("Done."), fake.result(tokens_in=10, tokens_out=5)]
    client = fake.FakeClaudeClient([steps])

    await _session(spec, engine, client, rec, time).run("x")

    ((_, final),) = rec.of("warroom.message")
    tool_calls = final.payload["tool_calls"]
    assert [c["name"] for c in tool_calls] == [c[0] for c in calls] + ["mcp__warroom__post_message"]
    for record, (_name, args, as_of) in zip(tool_calls, calls, strict=False):
        assert record["input"] == args and record["input_truncated"] is False
        assert record["as_of"] == as_of
        assert record["output_truncated"] is True and len(record["output"]) == CONFIG["tool_output_max_chars"]
        assert record["is_error"] is False
    err = tool_calls[-1]
    assert err["as_of"] is None and err["is_error"] is True
    assert err["input_truncated"] is True and isinstance(err["input"], str)
    assert len(err["input"]) == CONFIG["tool_input_max_chars"]
    assert final.payload["reasoning_md"] == "Check XAU stats first."

    thinking = [e for _, e in rec.of("warroom.thinking")]
    types = [e.payload["delta_type"] for e in thinking]
    assert types == ["thinking", *(["tool_use", "tool_result"] * 4), "text", "run_end"]
    use = thinking[1].payload
    assert use["tool"] == calls[0][0] and use["input"] == calls[0][1] and use["tool_use_id"] == "toolu_0"
    res = thinking[2].payload
    assert res["tool"] == calls[0][0] and len(res["output"]) == CONFIG["tool_output_max_chars"]
    assert _max_in_any_second([t for t, _ in rec.deltas()]) <= 10


async def test_a_failing_publisher_never_fails_the_run(spec: AgentSpec, engine: Engine) -> None:
    def broken(stream: str, envelope: EventEnvelope) -> None:
        if envelope.type.startswith("warroom."):
            raise ConnectionError("redis down")

    session = AgentSession(
        spec,
        env="demo",
        engine=engine,
        client_factory=fake.factory(
            fake.FakeClaudeClient([[fake.thinking_delta("a"), fake.assistant_text("ok"), fake.result()]])
        ),
        publisher=broken,
        observer_factory=observer_factory(agent=spec.slug, env="demo", publish=broken),
    )
    result = await session.run("x")
    assert result.status == "ok" and result.text == "ok"


async def test_a_run_with_nothing_to_show_posts_no_final_message_only_run_end() -> None:
    rec = TimedRecorder(FakeTime())
    pub = ThinkingPublisher(agent="historian", env="demo", run_id=uuid.uuid4(), publish=rec, config=CONFIG)
    await pub.on_finish(RunResult(run_id=pub.run_id, status="error", error="boom"))
    assert rec.of("warroom.message") == []
    (end,) = rec.run_ends()
    assert end.payload["status"] == "error" and end.payload["error"] == "boom"
    assert end.payload["run_id"] == str(pub.run_id)


async def test_long_final_text_is_cut_to_the_war_room_cap() -> None:
    rec = TimedRecorder(FakeTime())
    pub = ThinkingPublisher(agent="historian", env="demo", run_id=uuid.uuid4(), publish=rec, config=CONFIG)
    await pub.on_finish(
        RunResult(run_id=pub.run_id, status="ok", text="y" * 9000, cost_usd_notional=Decimal(0))
    )
    ((_, final),) = rec.of("warroom.message")
    assert len(final.payload["content_md"]) == 4000 and final.payload["content_md"].endswith("…")
    assert final.payload["cost_usd"] == "0.000000"


async def test_supervisor_streams_live_runs_with_partial_messages(spec: AgentSpec, engine: Engine) -> None:
    rec = TimedRecorder(FakeTime())
    client = fake.FakeClaudeClient([[fake.thinking_delta("hmm"), fake.assistant_text("hi"), fake.result()]])
    sup = Supervisor(
        env="demo",
        engine=engine,
        registry={spec.slug: spec},
        client_factory=fake.factory(client),
        publisher=rec,
    )
    await sup.run(spec.slug, "x")
    assert client.options is not None and client.options.include_partial_messages is True
    assert rec.of("warroom.thinking") and len(rec.of("warroom.message")) == 1
