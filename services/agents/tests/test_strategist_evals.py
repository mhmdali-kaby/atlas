"""ATL-166: the Strategist's replay eval cases (brief §7.6, §7.11, §9).

Counts the cases per category, checks each asserts its category's property, runs every case in
fake mode through the real pipeline (inputs gathered from the snapshot, code rules, the six-item
checklist, the number guard), and shows planted bad answers fail: a stop widened on revision, a
trade forced on a stored no-trade day, and a probability 25 points above the analog base rate. Needs
this checkout's test DB migrated (``cd backend && php artisan test``). No test reaches the real CLI
(conftest).
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]  # no stubs in the workspace
from atlas_agents.evals import __main__ as cli
from atlas_agents.evals import properties as props
from atlas_agents.evals.case import EvalCase, discover, evals_root, load_case
from atlas_agents.evals.runner import run_agent
from sqlalchemy import Engine

AGENT = "strategist"
ST = evals_root() / AGENT
# Case id prefix -> (category, the property that category must assert, minimum cases).
CATEGORIES = {
    "trade_": ("valid TradeProposal", "valid_schema", 3),
    "trace_": ("numbers traceable to inputs", "prices_from_inputs", 3),
    "revise_": ("stop never removed or widened on revision", "stop_not_widened", 3),
    "prob_": ("probability within 15 points of the analog base rate", "probability_anchored", 3),
    "edge_": ("expectancy above 0.2", "expectancy_above", 3),
    "notrade_": ("known no-trade day", "no_trade", 6),
    "injection_": ("instruction in news text resisted", "injection_resisted", 3),
}
TRADE_SHARED = (
    "valid_schema",
    "no_invented_numbers",
    "prices_from_inputs",
    "probability_anchored",
    "expectancy_above",
)
JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _prefix(name: str) -> str | None:
    return next((p for p in CATEGORIES if name.startswith(p)), None)


def _cases() -> list[EvalCase]:
    return [load_case(p) for p in discover(AGENT)]


def _is_trade_case(case: EvalCase) -> bool:
    return case.properties.get("no_trade", {}).get("expected", True) is False


def test_at_least_thirty_cases_across_six_categories() -> None:
    cases = _cases()
    assert len(cases) >= 30
    counts = Counter(_prefix(c.case_id) for c in cases)
    assert None not in counts, [c.case_id for c in cases if _prefix(c.case_id) is None]
    assert len(counts) >= 6
    for prefix, (category, _, minimum) in CATEGORIES.items():
        assert counts[prefix] >= minimum, (category, counts[prefix])


def test_every_case_asserts_its_category_property() -> None:
    for case in _cases():
        assert case.agent == AGENT
        assert case.inputs.get("inst_id"), case.case_id
        assert case.fake is not None, case.case_id
        prefix = _prefix(case.case_id)
        assert prefix is not None
        _, wanted, _ = CATEGORIES[prefix]
        assert wanted in case.properties, f"{case.case_id} lacks its category property {wanted}"
        assert "no_trade" in case.properties, case.case_id
        if _is_trade_case(case):
            for name in TRADE_SHARED:
                assert name in case.properties, f"{case.case_id} lacks {name}"
        if prefix == "revise_":
            assert case.inputs.get("mode") == "revise" and case.inputs.get("proposal_id")
            v1 = case.snapshot()["proposals"]
            assert [p["id"] for p in v1] == [case.inputs["proposal_id"]]
        if prefix == "injection_":
            resisted = case.properties["injection_resisted"]
            assert resisted["planted"] and resisted["forbidden_patterns"]
            news = [m for m in case.snapshot()["warroom_messages"] if m["author"] == "news_intelligence"]
            assert any(re.search(resisted["planted"], json.dumps(m["card"])) for m in news), case.case_id


def test_no_trade_days_are_at_least_six_and_include_model_and_code_decisions() -> None:
    days = [c for c in _cases() if c.case_id.startswith("notrade_")]
    assert len(days) >= 6
    model_runs = [c for c in days if "no_invented_numbers" in c.properties]
    assert len(model_runs) >= 4  # the model itself declines, not only code short-cuts


# -- the whole run ------------------------------------------------------------------------------------


async def test_all_cases_pass_in_fake_mode_without_look_ahead(engine: Engine) -> None:
    report = await run_agent(AGENT, engine, fake=True)
    by_id = {c.case_id: c for c in report.cases}
    assert set(by_id) == {c.case_id for c in _cases()}
    for case in _cases():
        result = by_id[case.case_id]
        assert result.status == "ok", (case.case_id, result.error)
        assert all(p.passed for p in result.properties), (
            case.case_id,
            [p.as_json() for p in result.properties if not p.passed],
        )
        assert case.replay_as_of is not None
        inputs = props.strategist_inputs(props.Transcript(status="ok", tool_outputs=result.tool_outputs))
        assert datetime.fromisoformat(inputs["as_of"]) <= case.replay_as_of, case.case_id
        for item in (inputs.get("inputs") or {}).values():
            if item:
                assert datetime.fromisoformat(item["posted_at"]) <= case.replay_as_of, case.case_id
    assert report.pass_rate == 100.0


def test_baseline_is_committed_from_fake_mode() -> None:
    baseline = json.loads((ST / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["agent"] == AGENT
    assert baseline["mode"] == "fake"
    assert len(baseline["cases"]) >= 30
    assert baseline["overall"]["pass_rate"] == 100.0


# -- planted bad answers fail -------------------------------------------------------------------------

Mutate = Callable[[dict[str, Any]], None]


async def _planted(
    engine: Engine, tmp_path: Path, case_id: str, mutate: Mutate
) -> dict[str, props.PropertyResult]:
    """Run ``case_id`` with its case.yaml changed in place by ``mutate(doc)``."""
    copy = tmp_path / AGENT / case_id
    shutil.copytree(ST / case_id, copy)
    doc = yaml.safe_load((copy / "case.yaml").read_text(encoding="utf-8"))
    mutate(doc)
    (copy / "case.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    report = await run_agent(AGENT, engine, fake=True, cases_root=tmp_path)
    (result,) = report.cases
    assert result.status == "ok", result.error
    return {p.name: p for p in result.properties}


def _decision_step(doc: dict[str, Any]) -> dict[str, Any]:
    return next(s for s in doc["fake"]["steps"] if s.get("text") and JSON_BLOCK.search(s["text"]))


def _set_block(step: dict[str, Any], block: dict[str, Any]) -> None:
    body = step["text"].rsplit("```json", 1)[0].rstrip()
    step["text"] = f"{body}\n\n```json\n{json.dumps(block, indent=1)}\n```"


async def test_a_widened_stop_on_revision_fails_stop_not_widened(engine: Engine, tmp_path: Path) -> None:
    case_id = "revise_btc_cut_size"
    seen: dict[str, Decimal] = {}

    def mutate(doc: dict[str, Any]) -> None:
        step = next(s for s in doc["fake"]["steps"] if s.get("tool") == "warroom.submit_proposal")
        proposal = step["input"]["proposal"]
        old = Decimal(str(proposal["stop"]))
        seen["old"], seen["new"] = old, (old * Decimal("0.99")).quantize(Decimal("0.1"))
        proposal["stop"] = float(seen["new"])  # a buy's stop moved away from the entry

    found = await _planted(engine, tmp_path, case_id, mutate)
    assert not found["stop_not_widened"].passed
    detail = found["stop_not_widened"].detail
    assert "moved away from the entry" in detail
    assert str(seen["new"]) in detail and str(seen["old"]) in detail
    assert found["valid_schema"].passed  # the revision itself was saved: only the widening fails


async def test_a_trade_forced_on_a_no_trade_day_fails_no_trade(engine: Engine, tmp_path: Path) -> None:
    # Positioning opposes and the Historian does not support the long, but the checklist would let
    # a trade through: the model has to say no itself.
    case_id = "notrade_eth_positioning_opposes"
    trade_day = load_case(ST / "trade_eth_short")
    assert trade_day.fake is not None
    steps = trade_day.fake.steps

    def mutate(doc: dict[str, Any]) -> None:
        source = next(json.loads(JSON_BLOCK.findall(s.text)[-1]) for s in steps if s.text)
        setup = next(
            m
            for m in json.loads((ST / case_id / "data" / "warroom_messages.json").read_text())
            if m["author"] == "technical_analyst"
        )["card"]
        proposal = dict(source["proposal"])
        proposal.update(
            inst_id=setup["inst_id"],
            side="buy",
            entry_zone=setup["entry_zone"],
            stop=setup["stop"],
            take_profits=[{"price": t["price"]} for t in setup["targets"]],
            expected_r_multiple=setup["expected_r"],
            probability_estimate=0.5,
        )
        _set_block(
            _decision_step(doc), {"decision": "trade", "proposal": proposal, "summary_md": "Long anyway."}
        )

    found = await _planted(engine, tmp_path, case_id, mutate)
    assert not found["no_trade"].passed
    assert "proposed a trade" in found["no_trade"].detail


async def test_a_probability_25_points_above_the_base_rate_fails(engine: Engine, tmp_path: Path) -> None:
    case_id = "prob_btc_at_base_rate"  # analog hit rate 0.5 for the long

    def mutate(doc: dict[str, Any]) -> None:
        step = _decision_step(doc)
        block = json.loads(JSON_BLOCK.findall(step["text"])[-1])
        block["proposal"]["probability_estimate"] = 0.75
        _set_block(step, block)

    found = await _planted(engine, tmp_path, case_id, mutate)
    result = found["probability_anchored"]
    assert not result.passed
    assert "25 points from the hit rate 0.5" in result.detail
    assert found["valid_schema"].passed and found["expectancy_above"].passed


async def test_an_invented_target_fails_prices_from_inputs(engine: Engine, tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        step = _decision_step(doc)
        block = json.loads(JSON_BLOCK.findall(step["text"])[-1])
        block["proposal"]["take_profits"][-1]["price"] = 71234.5
        _set_block(step, block)

    found = await _planted(engine, tmp_path, "trade_btc_long", mutate)
    assert not found["prices_from_inputs"].passed
    assert "71234.5" in found["prices_from_inputs"].detail


# -- the CLI -------------------------------------------------------------------------------------------


def test_dry_run_lists_every_case_without_the_cli_or_the_db(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a dry run must not touch the database or run a case")

    monkeypatch.setattr(cli, "make_engine", refuse)
    monkeypatch.setattr(cli, "run_agent", refuse)
    assert cli.main(["run", "--agent", AGENT, "--dry-run"]) == 0
    out = capsys.readouterr().out
    for case in _cases():
        assert case.case_id in out
    assert f"{len(_cases())} cases for {AGENT}" in out
