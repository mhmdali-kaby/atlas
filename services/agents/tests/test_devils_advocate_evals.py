"""ATL-167: the Devil's Advocate's replay eval cases (brief §7.7, §7.11, §9).

Counts the cases per category, checks each asserts its category's property, runs every case in fake
mode through the production review (the code table from the snapshot, card validation, the rating
guide and vote rules, the code-cast vote, the one-re-rate hook), and shows planted bad answers fail:
a review skipping item 4, and a rating of 5 on a clean fixture. Needs this checkout's test DB
migrated (``cd backend && php artisan test``). No test reaches the real CLI (conftest).
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
from atlas_agents.evals.case import EvalCase, discover, evals_root, load_case
from atlas_agents.evals.runner import CaseResult, run_agent
from sqlalchemy import Engine

AGENT = "devils_advocate"
DA = evals_root() / AGENT
# Case id prefix -> (category, the property that category must assert, minimum cases).
CATEGORIES = {
    "items_": ("all 10 checklist items addressed", "checklist_complete", 5),
    "guide_": ("risk rating follows the §7.7 guide", "rating_follows_guide", 5),
    "event_": ("event inside the trade window flagged", "item_flagged", 4),
    "noise_": ("stop inside 4H noise flagged", "item_flagged", 4),
    "clean_": ("no objection to a clean proposal (false alarm)", "no_false_alarm", 5),
    "rerate_": ("re-rates only once", "rerated_once", 4),
}
REVIEW_SHARED = ("valid_schema", "checklist_complete", "rating_follows_guide", "no_invented_numbers")
JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _prefix(name: str) -> str | None:
    return next((p for p in CATEGORIES if name.startswith(p)), None)


def _cases() -> list[EvalCase]:
    return [load_case(p) for p in discover(AGENT)]


def _model_runs(case: EvalCase) -> bool:
    return case.properties.get("rerated_once", {}).get("expected", 1) != 0


def test_at_least_thirty_cases_across_the_categories() -> None:
    cases = _cases()
    assert len(cases) >= 30
    counts = Counter(_prefix(c.case_id) for c in cases)
    assert None not in counts, [c.case_id for c in cases if _prefix(c.case_id) is None]
    for prefix, (category, _, minimum) in CATEGORIES.items():
        assert counts[prefix] >= minimum, (category, counts[prefix])


def test_every_case_asserts_its_category_property() -> None:
    for case in _cases():
        assert case.agent == AGENT
        assert case.fake is not None, case.case_id
        assert case.inputs.get("proposal_id"), case.case_id
        prefix = _prefix(case.case_id)
        assert prefix is not None
        _, wanted, _ = CATEGORIES[prefix]
        assert wanted in case.properties, f"{case.case_id} lacks its category property {wanted}"
        if _model_runs(case):
            for name in REVIEW_SHARED:
                assert name in case.properties, f"{case.case_id} lacks {name}"
        snapshot = case.snapshot()
        assert [p["id"] for p in snapshot["proposals"]] == [case.inputs["proposal_id"]]
        if prefix == "event_":
            assert snapshot.get("economic_events") or snapshot.get("earnings_events"), case.case_id
            assert case.properties["item_flagged"]["item"] == 1
        if prefix == "noise_":
            assert case.properties["item_flagged"]["item"] == 4
        if prefix == "rerate_":
            assert case.inputs["mode"] == "rerate" and case.inputs["replies"]


def test_the_ratings_cover_the_whole_guide() -> None:
    ratings = {
        c.properties["rating_follows_guide"]["expected"] for c in _cases() if c.case_id.startswith("guide_")
    }
    assert ratings == {1, 2, 3, 4, 5}


def test_re_rate_cases_include_second_replies_and_stored_re_rates() -> None:
    rerates = [c for c in _cases() if c.case_id.startswith("rerate_")]
    assert any(len(c.inputs["replies"]) >= 2 and _model_runs(c) for c in rerates)  # a second reply
    assert sum(1 for c in rerates if not _model_runs(c)) >= 2  # nothing starts at all


# -- the whole run ------------------------------------------------------------------------------------


async def test_all_cases_pass_in_fake_mode(engine: Engine) -> None:
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
    assert report.pass_rate == 100.0


def test_baseline_is_committed_from_fake_mode() -> None:
    baseline = json.loads((DA / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["agent"] == AGENT
    assert baseline["mode"] == "fake"
    assert len(baseline["cases"]) >= 30
    assert baseline["overall"]["pass_rate"] == 100.0


# -- planted bad answers fail -------------------------------------------------------------------------

Mutate = Callable[[dict[str, Any]], None]


async def _planted(engine: Engine, tmp_path: Path, case_id: str, mutate: Mutate) -> CaseResult:
    """Run ``case_id`` with its scripted DevilsReview card changed by ``mutate(card)``."""
    copy = tmp_path / AGENT / case_id
    shutil.copytree(DA / case_id, copy)
    doc = yaml.safe_load((copy / "case.yaml").read_text(encoding="utf-8"))
    step = next(s for s in doc["fake"]["steps"] if s.get("text") and JSON_BLOCK.search(s["text"]))
    card = json.loads(JSON_BLOCK.findall(step["text"])[-1])
    mutate(card)
    body = step["text"].rsplit("```json", 1)[0].rstrip()
    step["text"] = f"{body}\n\n```json\n{json.dumps(card, indent=1)}\n```"
    (copy / "case.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    report = await run_agent(AGENT, engine, fake=True, cases_root=tmp_path)
    (result,) = report.cases
    return result


async def test_a_review_skipping_item_4_fails(engine: Engine, tmp_path: Path) -> None:
    def mutate(card: dict[str, Any]) -> None:
        card["items"] = [i for i in card["items"] if i["item"] != 4]

    result = await _planted(engine, tmp_path, "noise_btc_tight_stop", mutate)
    # Code refuses the card and asks once; the scripted answer has no second card, so the run fails
    # and nothing is posted or voted.
    assert result.status != "ok"
    assert result.error is not None and "checklist items missing: 4 (stop_quality)" in result.error
    assert not any(p.passed for p in result.properties)


async def test_a_rating_of_5_on_a_clean_fixture_fails(engine: Engine, tmp_path: Path) -> None:
    def mutate(card: dict[str, Any]) -> None:
        card["items"][0] = {
            "item": 1,
            "name": "event_risk",
            "status": "problem",
            "severity": "loss_gt_1r",
            "detail": "a surprise 5 importance event could gap through the stop",
        }
        card["risk_rating"] = 5
        card["vote"] = "skip"
        card["strongest_objection"] = "An event could gap through the stop."

    result = await _planted(engine, tmp_path, "clean_btc_long", mutate)
    assert result.status == "ok", result.error
    found = {p.name: p for p in result.properties}
    assert not found["no_false_alarm"].passed
    assert "false alarm: risk 5/5 on a clean proposal" in found["no_false_alarm"].detail
    assert found["checklist_complete"].passed  # the card itself is well formed


async def test_a_bare_rating_of_5_against_ok_items_fails_the_guide(engine: Engine, tmp_path: Path) -> None:
    def mutate(card: dict[str, Any]) -> None:
        card["risk_rating"] = 5
        card["vote"] = "skip"

    result = await _planted(engine, tmp_path, "clean_eth_short", mutate)
    found = {p.name: p for p in result.properties}
    assert not found["rating_follows_guide"].passed
    assert "rated 5 but its own items give 1" in found["rating_follows_guide"].detail
    assert not found["no_false_alarm"].passed  # code corrected the rating to 1, but the vote is skip


async def test_an_event_marked_ok_fails_item_flagged(engine: Engine, tmp_path: Path) -> None:
    def mutate(card: dict[str, Any]) -> None:
        card["items"][0] = {
            "item": 1,
            "name": "event_risk",
            "status": "ok",
            "severity": "none",
            "detail": "OK",
        }
        card["risk_rating"] = 1
        card["vote"] = "take"
        card["fix"] = None

    result = await _planted(engine, tmp_path, "event_xau_us_cpi", mutate)
    found = {p.name: p for p in result.properties}
    assert not found["item_flagged"].passed
    assert "item 1 (event_risk) answered OK" in found["item_flagged"].detail


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
