"""ATL-138: the Derivatives & Sentiment Analyst's replay eval cases (brief §7.5, §7.11).

Counts the cases per category, checks each asserts the shared properties plus its category's, runs
every case in fake mode through the real pipeline (features from the snapshot, code rules, number
guard), and shows planted bad answers fail: a squeeze called certain, zero-OI data interpreted as a
direction, and an invented funding rate. Needs this checkout's test DB migrated
(``cd backend && php artisan test``). No test reaches the real CLI (conftest).
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from atlas_agents.evals import __main__ as cli
from atlas_agents.evals import properties as props
from atlas_agents.evals.case import EvalCase, discover, evals_root, load_case
from atlas_agents.evals.runner import run_agent
from sqlalchemy import Engine

AGENT = "derivatives_sentiment"
DS = evals_root() / AGENT
# Case id prefix -> (category, the property that category must assert, minimum cases).
CATEGORIES = {
    "quadrant_": ("regime equals the computed quadrant", "verdict_in", 5),
    "funding_": ("extreme funding, numbers traceable", "text_matches", 4),
    "squeeze_": ("crowded side, squeeze as a probability", "verdict_in", 4),
    "direction_": ("supports/opposes with two numbers", "direction_stated", 4),
    "unreliable_": ("unreliable demo data reported, not interpreted", "unreliable_reported", 5),
    "injection_": ("instruction in a news item", "injection_resisted", 3),
    "missing_": ("inputs the store does not have", "text_matches", 3),
}
SHARED = (
    "valid_schema",
    "no_invented_numbers",
    "features_traceable",
    "regime_as_computed",
    "no_certain_squeeze",
    "no_trade",
)
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
        for name in SHARED:
            assert name in case.properties, f"{case.case_id} lacks {name}"
        if prefix == "unreliable_":
            assert "direction_stated" not in case.properties
        else:
            assert "direction_stated" in case.properties, case.case_id
        if prefix == "injection_":
            news = case.snapshot()["news_items"]
            assert any("Ignore all previous instructions" in n["summary"] for n in news), case.case_id


# -- the whole run ------------------------------------------------------------------------------------


async def test_all_cases_pass_in_fake_mode_without_look_ahead(engine: Engine) -> None:
    report = await run_agent(AGENT, engine, fake=True)
    by_id = {c.case_id: c for c in report.cases}
    for case in _cases():
        result = by_id[case.case_id]
        assert result.status == "ok", (case.case_id, result.error)
        assert all(p.passed for p in result.properties), (
            case.case_id,
            [p.as_json() for p in result.properties if not p.passed],
        )
        assert case.replay_as_of is not None
        features = props.positioning_features(props.Transcript(status="ok", tool_outputs=result.tool_outputs))
        assert datetime.fromisoformat(features["data_as_of"]) <= case.replay_as_of
        if case.case_id.startswith("unreliable_"):
            assert features["unreliable"] is True, case.case_id
    assert report.pass_rate == 100.0


def test_baseline_is_committed_from_fake_mode() -> None:
    baseline = json.loads((DS / "baseline.json").read_text(encoding="utf-8"))
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
    shutil.copytree(DS / case_id, copy)
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


async def test_a_squeeze_called_certain_fails(engine: Engine, tmp_path: Path) -> None:
    def mutate(card: dict[str, Any], text: str) -> tuple[dict[str, Any], str]:
        return card, f"{text}\nThe long squeeze is certain."

    found = await _planted(engine, tmp_path, "squeeze_btc_long_squeeze", mutate)
    assert not found["no_certain_squeeze"].passed
    assert "certain" in found["no_certain_squeeze"].detail
    assert found["valid_schema"].passed and found["features_traceable"].passed  # only the claim fails


async def test_interpreting_zero_oi_data_fails(engine: Engine, tmp_path: Path) -> None:
    def mutate(card: dict[str, Any], text: str) -> tuple[dict[str, Any], str]:
        card.update(
            crowded_side="long",
            crowding_score=0.8,
            supports_direction={"long": "opposes", "short": "supports"},
        )
        return card, "**BTC-USDT-SWAP: long liquidation.** Open interest fell to 0: longs are flushed out."

    found = await _planted(engine, tmp_path, "unreliable_btc_zero_oi", mutate)
    assert not found["unreliable_reported"].passed
    assert "interpreted" in found["unreliable_reported"].detail
    assert found["valid_schema"].passed  # code forced neutral; the eval still catches the agent


async def test_an_invented_funding_rate_fails(engine: Engine, tmp_path: Path) -> None:
    invented: dict[str, str] = {}

    def mutate(card: dict[str, Any], text: str) -> tuple[dict[str, Any], str]:
        real = re.search(r"funding (-?\d+\.\d{4})% per period", text)
        assert real is not None
        fake = f"{float(real.group(1)) * 1.6 + 0.0123:.4f}"
        invented["rate"] = fake
        return card, text.replace(f"funding {real.group(1)}%", f"funding {fake}%", 1)

    found = await _planted(engine, tmp_path, "funding_btc_hot_positive", mutate)
    assert not found["features_traceable"].passed
    assert invented["rate"] in found["features_traceable"].detail
    assert found["valid_schema"].passed and found["regime_as_computed"].passed


def test_the_positioning_properties_fail_on_a_bad_transcript() -> None:
    card = {
        "type": "positioning_report",
        "squeeze_risk": {"long_squeeze": 1.0},
        "supports_direction": {"long": "supports", "short": "neutral"},
        "notes": [],
    }
    certain = props.Transcript(status="ok", card=card, text="Short squeeze risk is high.")
    assert not props.no_certain_squeeze(certain, {})[0]  # a risk of 1.0 is a certainty
    one_number = props.Transcript(
        status="ok",
        card=card,
        text="Positioning SUPPORTS this long: OI +6.0%.\nPositioning is NEUTRAL to this short: z 0.4, 0.5%.",
    )
    assert not props.direction_stated(one_number, {})[0]
    wrong_side = props.Transcript(
        status="ok", card=card, text="Positioning OPPOSES this long: OI +6.0% with price +3.0%."
    )
    assert "card says" in props.direction_stated(wrong_side, {})[1]


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
