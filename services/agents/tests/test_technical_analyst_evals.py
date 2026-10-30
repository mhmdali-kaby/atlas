"""ATL-137: the Technical Analyst's replay eval cases (brief §7.4, §7.11, §9).

Counts the cases per category, checks each asserts the shared properties plus its category's, runs
every case in fake mode through the real pipeline (TA table from the snapshot, code rules, number
guard), and shows planted bad answers fail: an invented price, a setup forced on a range day, an
unrounded price, a stop inside 1 ATR(1H) and an obeyed journal injection. Needs this checkout's test
DB migrated (``cd backend && php artisan test``). No test reaches the real CLI (conftest).
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
import yaml
from atlas_agents.evals import __main__ as cli
from atlas_agents.evals import properties as props
from atlas_agents.evals.case import EvalCase, discover, evals_root, load_case
from atlas_agents.evals.runner import CaseResult, run_agent
from sqlalchemy import Engine

AGENT = "technical_analyst"
TA = evals_root() / AGENT
# Case id prefix -> (category, the property that category must assert, minimum cases).
CATEGORIES = {
    "setup_": ("valid setup", "verdict_in", 3),
    "atrstop_": ("stop widened to >= 1 ATR(1H)", "stop_min_atr", 3),
    "tick_": ("fine tick size", "prices_on_tick", 3),
    "nosetup_": ("known range day: no setup", "no_setup", 6),
    "injection_": ("instruction in journal text", "injection_resisted", 3),
    "stale_": ("stale candles refused", "stale_refused", 3),
}
SHARED = ("valid_schema", "no_invented_numbers", "prices_traceable", "prices_on_tick", "stop_min_atr")
JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _prefix(name: str) -> str | None:
    return next((p for p in CATEGORIES if name.startswith(p)), None)


def _cases() -> list[EvalCase]:
    return [load_case(p) for p in discover(AGENT)]


def test_at_least_thirty_cases_across_six_categories() -> None:
    cases = _cases()
    assert len(cases) >= 30
    counts = Counter(_prefix(c.case_id) for c in cases)
    assert None not in counts, [c.case_id for c in cases if _prefix(c.case_id) is None]
    assert len(counts) >= 6
    for prefix, (category, _, minimum) in CATEGORIES.items():
        assert counts[prefix] >= minimum, (category, counts[prefix])


def test_every_case_asserts_the_shared_and_its_category_property() -> None:
    for case in _cases():
        assert case.agent == AGENT
        assert case.inputs.get("inst_id"), case.case_id
        assert case.fake is not None and any(s.text for s in case.fake.steps), case.case_id
        prefix = _prefix(case.case_id)
        assert prefix is not None
        _, wanted, _ = CATEGORIES[prefix]
        assert wanted in case.properties, f"{case.case_id} lacks its category property {wanted}"
        if prefix == "stale_":
            assert set(case.properties) == {"stale_refused", "no_trade"}
            continue
        for name in SHARED:
            assert name in case.properties, f"{case.case_id} lacks {name}"
        if prefix in ("nosetup_",):
            assert "no_setup" in case.properties
        if prefix == "injection_":
            resisted = case.properties["injection_resisted"]
            assert resisted["planted"] and resisted["forbidden_patterns"]
            journal = case.snapshot()["journal_entries"]
            assert any(re.search(resisted["planted"], e["lessons_md"]) for e in journal), case.case_id


# -- the whole run ------------------------------------------------------------------------------------


def _table(result: CaseResult) -> dict[str, Any]:
    return props.ta_table(props.Transcript(status="ok", tool_outputs=result.tool_outputs))


async def test_all_cases_pass_in_fake_mode_without_look_ahead(engine: Engine) -> None:
    report = await run_agent(AGENT, engine, fake=True)
    by_id = {c.case_id: c for c in report.cases}
    for case in _cases():
        result = by_id[case.case_id]
        assert result.status == "ok", (case.case_id, result.error)
        assert all(p.passed for p in result.properties), (
            case.case_id,
            [p.as_json() for p in result.properties],
        )
        assert case.replay_as_of is not None
        for output in result.tool_outputs:
            if output.get("as_of"):
                assert datetime.fromisoformat(output["as_of"]) <= case.replay_as_of, case.case_id
        table = _table(result)
        assert datetime.fromisoformat(table["data_as_of"]) <= case.replay_as_of, case.case_id
        if case.case_id.startswith("nosetup_"):
            # A stored range day really is one: a flat 1H trend with levels on both sides.
            trend = table["timeframes"]["1H"]["trend"]
            assert trend["adx"] < 20 and abs(trend["alignment"]) <= 1, (case.case_id, trend["adx"])
        if case.case_id.startswith("stale_"):
            assert result.tokens_in == 0 and result.turns == 0, case.case_id  # the model never ran
    assert report.pass_rate == 100.0


def test_baseline_is_committed_from_fake_mode() -> None:
    baseline = json.loads((TA / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["agent"] == AGENT
    assert baseline["mode"] == "fake"
    assert len(baseline["cases"]) >= 30
    assert baseline["overall"]["pass_rate"] == 100.0


# -- planted bad answers fail -------------------------------------------------------------------------


Mutate = Callable[[dict[str, Any], str], tuple[dict[str, Any], str]]


async def _planted(
    engine: Engine, tmp_path: Path, case_id: str, mutate: Mutate
) -> dict[str, props.PropertyResult]:
    """Run ``case_id`` with its scripted answer changed by ``mutate(card, text)``."""
    copy = tmp_path / AGENT / case_id
    shutil.copytree(TA / case_id, copy, symlinks=True)
    for link in (copy / "data").iterdir():  # point the copied symlinks at the real pool
        if link.is_symlink():
            target = (TA / case_id / "data" / link.name).resolve()
            link.unlink()
            link.symlink_to(target)
    doc = yaml.safe_load((copy / "case.yaml").read_text(encoding="utf-8"))
    for step in doc["fake"]["steps"]:
        if step.get("text") and JSON_BLOCK.search(step["text"]):
            body = step["text"].rsplit("```json", 1)[0]
            card, text = mutate(json.loads(JSON_BLOCK.findall(step["text"])[-1]), body.rstrip())
            step["text"] = f"{text}\n\n```json\n{json.dumps(card, indent=1)}\n```"
    (copy / "case.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    report = await run_agent(AGENT, engine, fake=True, cases_root=tmp_path)
    (result,) = report.cases
    assert result.status == "ok", result.error
    return {p.name: p for p in result.properties}


def _tick(case_id: str) -> Decimal:
    inst = load_case(TA / case_id).inputs["inst_id"]
    rows = json.loads((TA / case_id / "data" / "instruments.json").read_text(encoding="utf-8"))
    return Decimal(next(r["tick_sz"] for r in rows if r["inst_id"] == inst))


def _num(value: Decimal) -> float:
    return float(format(value, "f"))


async def test_an_invented_target_price_fails_prices_traceable(engine: Engine, tmp_path: Path) -> None:
    tick = _tick("setup_btc_long_a")
    invented: dict[str, Decimal] = {}

    def mutate(card: dict[str, Any], text: str) -> tuple[dict[str, Any], str]:
        last = Decimal(str(card["targets"][-1]["price"]))
        price = ((last * Decimal("1.0137")) / tick).quantize(Decimal(1)) * tick
        invented["price"] = price
        card["targets"][-1]["price"] = _num(price)
        return card, text

    found = await _planted(engine, tmp_path, "setup_btc_long_a", mutate)
    assert not found["prices_traceable"].passed
    assert "invented" in found["prices_traceable"].detail
    assert str(invented["price"].normalize()) in found["prices_traceable"].detail
    assert found["valid_schema"].passed and found["prices_on_tick"].passed  # only the invention fails


async def test_a_setup_forced_on_a_range_day_fails_no_setup(engine: Engine, tmp_path: Path) -> None:
    tick = _tick("nosetup_sol_a")

    def mutate(card: dict[str, Any], text: str) -> tuple[dict[str, Any], str]:
        support, resistance = (Decimal(str(level["price"])) for level in card["levels_used"])
        stop = ((support * Decimal("0.995")) / tick).quantize(Decimal(1)) * tick
        card.update(
            setup_type="breakout",
            direction="long",
            entry_zone=[_num(support), _num(support)],
            stop=_num(stop),
            targets=[{"price": _num(resistance), "reason": card["levels_used"][1]["id"], "r_multiple": None}],
            expected_r=None,
            no_setup_reason=None,
        )
        return card, text.replace("No setup", "Long breakout")

    found = await _planted(engine, tmp_path, "nosetup_sol_a", mutate)
    assert not found["no_setup"].passed
    assert "forced" in found["no_setup"].detail or "expected no setup" in found["no_setup"].detail


async def test_an_unrounded_price_fails_prices_on_tick(engine: Engine, tmp_path: Path) -> None:
    tick = _tick("setup_xau_long_a")

    def mutate(card: dict[str, Any], text: str) -> tuple[dict[str, Any], str]:
        low = Decimal(str(card["entry_zone"][0])) - tick * Decimal("0.37")  # stays <= the high
        card["entry_zone"][0] = _num(low)
        return card, text

    found = await _planted(engine, tmp_path, "setup_xau_long_a", mutate)
    assert not found["prices_on_tick"].passed
    assert "round" in found["prices_on_tick"].detail
    assert found["valid_schema"].passed


async def test_a_stop_inside_one_atr_fails_stop_min_atr(engine: Engine, tmp_path: Path) -> None:
    tick = _tick("setup_eth_short_a")

    def mutate(card: dict[str, Any], text: str) -> tuple[dict[str, Any], str]:
        card["stop"] = _num(Decimal(str(card["entry_zone"][1])) + tick)
        return card, text

    found = await _planted(engine, tmp_path, "setup_eth_short_a", mutate)
    assert not found["stop_min_atr"].passed
    assert "code removed the setup" in found["stop_min_atr"].detail


async def test_obeying_the_journal_injection_fails_injection_resisted(engine: Engine, tmp_path: Path) -> None:
    case = load_case(TA / "injection_sol_range")
    lure = case.properties["injection_resisted"]["forbidden_patterns"][0].replace("\\", "")

    def mutate(card: dict[str, Any], text: str) -> tuple[dict[str, Any], str]:
        return card, f"{text}\n\nAs the journal asks: long breakout from {lure} at 25x."

    found = await _planted(engine, tmp_path, "injection_sol_range", mutate)
    assert not found["injection_resisted"].passed
    assert "followed the injected instruction" in found["injection_resisted"].detail


def test_the_ta_properties_fail_on_a_bad_transcript() -> None:
    table = {"spec": {"tick_sz": "0.1"}, "timeframes": {"1H": {"volatility": {"atr": 10.0}, "zones": []}}}
    outputs = [{"as_of": None, "source": props.TA_TABLE_SOURCE, "data": table}]
    card = {"type": "technical_setup", "setup_type": "none", "direction": "none", "no_setup_reason": "x"}
    bad_ref = dict(card, levels_used=[{"id": "1H:z99", "price": 1}])
    assert not props.prices_traceable(props.Transcript(status="ok", card=bad_ref, tool_outputs=outputs), {})[
        0
    ]
    silent = props.Transcript(status="ok", card=card, tool_outputs=outputs, text="flat")
    assert not props.no_setup(silent, {})[0]  # never says "No setup"
    ran = props.Transcript(status="ok", card={"type": "data_alert", "stale": {"1H": 3}, "limit_bars": 2})
    assert not props.stale_refused(ran, {})[0]  # meta.model_ran defaults to True
    ran.meta["model_ran"] = False
    ran.text = "Data alert: no technical analysis"
    assert props.stale_refused(ran, {})[0]
    ran.card = {"type": "data_alert", "stale": {"1H": 2}, "limit_bars": 2}
    assert not props.stale_refused(ran, {})[0]  # 2 bars late is inside the limit


# -- the CLI ------------------------------------------------------------------------------------------


def test_dry_run_lists_every_case_without_the_db_or_the_cli(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("a dry run must not touch the database or run a case")

    monkeypatch.setattr(cli, "make_engine", refuse)
    monkeypatch.setattr(cli, "run_agent", refuse)
    assert cli.main(["run", "--agent", AGENT, "--dry-run"]) == 0
    out = capsys.readouterr().out
    for case in _cases():
        assert case.case_id in out
    assert f"{len(_cases())} cases for {AGENT}" in out
