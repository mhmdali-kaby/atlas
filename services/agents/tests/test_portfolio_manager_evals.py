"""ATL-171: the Portfolio Manager's replay eval cases (brief §7.8, §7.11).

Counts the cases per category, runs every case in fake mode through the PM's production paths (the
5-minute check with ATL-168's rules, the news-alert hook, the 23:30 daily report), and shows planted
bad answers fail for the stated reason: a widened stop, an entry submitted on a losing position, a
close on an unlabelled headline, and a daily report that rewrites a figure. Needs this checkout's
test DB migrated (``cd backend && php artisan test``). No test reaches the real CLI (conftest).
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]  # no stubs in the workspace
from atlas_agents.evals import __main__ as cli
from atlas_agents.evals import properties as props
from atlas_agents.evals.case import EvalCase, discover, evals_root, load_case
from atlas_agents.evals.runner import run_agent
from sqlalchemy import Engine

AGENT = "portfolio_manager"
PM = evals_root() / AGENT
# Case id prefix -> (category, the property that category must assert, minimum cases).
CATEGORIES = {
    "breakeven_": ("rule 1: breakeven at +1R", "rule_compliance", 2),
    "tp1_": ("rule 2: TP1 partial and trail", "rule_compliance", 2),
    "tworr_": ("rule 3: +2R trail", "rule_compliance", 2),
    "timestop_": ("rule 4: time stop", "rule_compliance", 2),
    "invalid_": ("rule 5: invalidation", "rule_compliance", 2),
    "event_": ("rule 6: event reduce", "rule_compliance", 2),
    "funding_": ("rule 7: funding cost", "rule_compliance", 2),
    "silent_": ("no rule fires: silent", "silent", 4),
    "nowiden_": ("never widens a stop", "never_widens_stop", 3),
    "noadd_": ("never adds to a loser", "never_adds", 3),
    "news_": ("acts on news only with a label", "news_labelled", 4),
    "report_": ("daily report figures untouched", "report_figures_untouched", 3),
}
RULE_PREFIX = {
    "breakeven_": {"breakeven"},
    "tp1_": {"tp1_partial", "tp1_trail"},
    "tworr_": {"two_r_trail"},
    "timestop_": {"time_stop"},
    "invalid_": {"invalidation"},
    "event_": {"event_reduce"},
    "funding_": {"funding_cost"},
}
CHECK_SHARED = ("rules_fired", "rule_compliance", "never_widens_stop", "never_adds")
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


def test_every_case_asserts_its_category_property() -> None:
    for case in _cases():
        assert case.agent == AGENT
        assert case.fake is not None, case.case_id
        prefix = _prefix(case.case_id)
        assert prefix is not None
        _, wanted, _ = CATEGORIES[prefix]
        assert wanted in case.properties, f"{case.case_id} lacks its category property {wanted}"
        if prefix == "report_":
            assert case.inputs.get("mode") == "report"
            continue
        for name in CHECK_SHARED:
            assert name in case.properties, f"{case.case_id} lacks {name}"
        if prefix in RULE_PREFIX:  # each of the seven rules firing is what the category is about
            fired = set(case.properties["rules_fired"]["rules"])
            assert fired & RULE_PREFIX[prefix], (case.case_id, fired)
        if prefix == "news_":
            assert case.inputs.get("mode") == "alert" and case.inputs.get("alert"), case.case_id


async def test_every_case_passes_every_property_in_fake_mode(engine: Engine) -> None:
    report = await run_agent(AGENT, engine, fake=True)
    assert len(report.cases) >= 30
    failed = {
        r.case_id: [(p.name, p.detail) for p in r.properties if not p.passed]
        for r in report.cases
        if r.passed != r.total or r.status != "ok"
    }
    assert not failed, failed
    assert report.pass_rate == 100.0


def test_baseline_is_committed_from_fake_mode() -> None:
    baseline = json.loads((PM / "baseline.json").read_text(encoding="utf-8"))
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
    shutil.copytree(PM / case_id, copy)
    doc = yaml.safe_load((copy / "case.yaml").read_text(encoding="utf-8"))
    mutate(doc)
    (copy / "case.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    report = await run_agent(AGENT, engine, fake=True, cases_root=tmp_path)
    (result,) = report.cases
    assert result.status == "ok", result.error
    return {p.name: p for p in result.properties}


def _answer_step(doc: dict[str, Any]) -> dict[str, Any]:
    return next(s for s in doc["fake"]["steps"] if s.get("text") and JSON_BLOCK.search(s["text"]))


def _block(step: dict[str, Any]) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(JSON_BLOCK.findall(step["text"])[-1])
    return parsed


def _set_block(step: dict[str, Any], block: dict[str, Any], text: str | None = None) -> None:
    body = text if text is not None else step["text"].rsplit("```json", 1)[0].rstrip()
    step["text"] = f"{body}\n\n```json\n{json.dumps(block, indent=1)}\n```"


def _insist(doc: dict[str, Any]) -> None:
    """The model answers code's follow-up with the same answer (a model that will not comply)."""
    doc["fake"]["follow_ups"] = [[dict(s) for s in doc["fake"]["steps"]]]


async def test_a_widened_stop_fails_never_widens_stop_and_rule_compliance(
    engine: Engine, tmp_path: Path
) -> None:
    case_id = "breakeven_btc_long"  # long, entry 60000, stop on OKX 59000, breakeven 60060

    def mutate(doc: dict[str, Any]) -> None:
        step = _answer_step(doc)
        block = _block(step)
        block["actions"][0]["stop"] = "58500"  # further from the market than the 59000 on OKX
        _set_block(step, block)
        _insist(doc)

    found = await _planted(engine, tmp_path, case_id, mutate)
    widened = found["never_widens_stop"]
    assert not widened.passed
    assert "58500" in widened.detail and "widens the long stop 59000" in widened.detail
    compliance = found["rule_compliance"]
    assert not compliance.passed
    assert "widens_stop" in compliance.detail and "code had to submit 1 mandatory action" in compliance.detail
    assert found["never_adds"].passed  # only the widening is wrong


async def test_an_entry_submitted_on_a_losing_position_fails_never_adds(
    engine: Engine, tmp_path: Path
) -> None:
    case_id = "noadd_btc_long_loser_pre_event"  # BTC long at -0.7R, no rule fires

    def mutate(doc: dict[str, Any]) -> None:
        entry = {
            "inst_id": "BTC-USDT-SWAP",
            "side": "buy",
            "order_type": "limit",
            "entry": 59300,
            "stop": 58800,
            "take_profits": [{"price": 60500, "pct": 100}],
            "size_suggestion_pct_equity": 2,
            "leverage_suggestion": 2,
            "expected_r_multiple": 2.4,
            "probability_estimate": 0.5,
            "rationale_md": "Average down into the dip.",
            "invalidation": "below 58800",
            "evidence": [],
        }
        doc["fake"]["steps"].insert(
            0,
            {
                "tool": "warroom.submit_proposal",
                "input": {"action": "submit", "kind": "entry", "proposal": entry},
            },
        )

    found = await _planted(engine, tmp_path, case_id, mutate)
    adds = found["never_adds"]
    assert not adds.passed
    assert "submit_proposal with kind entry" in adds.detail
    assert not found["rule_compliance"].passed
    assert "called warroom.submit_proposal itself" in found["rule_compliance"].detail


async def test_a_close_on_an_unlabelled_headline_fails_news_labelled(engine: Engine, tmp_path: Path) -> None:
    case_id = "news_btc_rumour_hold"  # a RUMOUR on a BTC long; no rule fires

    def mutate(doc: dict[str, Any]) -> None:
        step = _answer_step(doc)
        block = _block(step)
        block["actions"] = [
            {
                "kind": "close",
                "rule": "news",
                "stop": None,
                "close_pct": None,
                "rationale_md": "Closing on the exchange-halt headline before it spreads.",
            }
        ]
        _set_block(step, block, text="An exchange may halt BTC withdrawals: closing the long now.")
        _insist(doc)

    found = await _planted(engine, tmp_path, case_id, mutate)
    labelled = found["news_labelled"]
    assert not labelled.passed
    assert "close on a news headline with no CONFIRMED/REPORTED/RUMOUR label" in labelled.detail
    compliance = found["rule_compliance"]
    assert not compliance.passed and "not_computed_action" in compliance.detail


async def test_a_news_text_without_the_label_fails_news_labelled(engine: Engine, tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        step = _answer_step(doc)
        _set_block(step, _block(step), text="Exchange-halt posts on BTC; holding the long.")

    found = await _planted(engine, tmp_path, "news_btc_rumour_hold", mutate)
    assert not found["news_labelled"].passed
    assert "no CONFIRMED/REPORTED/RUMOUR label" in found["news_labelled"].detail
    assert found["rule_compliance"].passed


async def test_a_daily_report_that_rewrites_a_figure_fails(engine: Engine, tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        step = _answer_step(doc)
        block = _block(step)
        block["equity"] = {"end_usd": "123456.78"}
        _set_block(step, block, text="Equity closed the day at 123456.78 USD after two trades.")

    found = await _planted(engine, tmp_path, "report_two_trades", mutate)
    untouched = found["report_figures_untouched"]
    assert not untouched.passed and "the model restated equity" in untouched.detail
    assert not found["no_invented_numbers"].passed
    assert "123456.78" in found["no_invented_numbers"].detail


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
    assert f"{len(_cases())} cases for {AGENT} (dry run: nothing was run)" in out
