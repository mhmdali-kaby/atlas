"""ATL-94: the Historian's 30 replay eval cases (brief §7.3, §7.11).

Counts the cases per category, checks that each asserts the required properties and the verdict
bound its category demands, and runs all of them in fake mode (tool steps run the real replay-bounded
tools on the shared ``_pool`` snapshot). Needs this checkout's test DB migrated
(``cd backend && php artisan test``). No test reaches the real CLI (conftest).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from atlas_agents.evals.case import EvalCase, discover, evals_root, load_case
from atlas_agents.evals.runner import run_agent
from sqlalchemy import Engine

HISTORIAN = evals_root() / "historian"
EXAMPLES = {"btc_regime_1h", "btc_stats_1h", "doge_no_data"}  # ATL-92's harness examples

# Case id prefix -> (category, how many the task asks for).
CATEGORIES = {
    "analog_": ("analog", 8),
    "event_": ("event", 5),
    "backtest_": ("backtest", 5),
    "small_": ("small-sample", 4),
    "gap_": ("gapped-data", 3),
    "stopnoise_": ("stop-noise", 3),
    "regime_table_": ("regime-table", 2),
}
AT_MOST_WEAK = {"weakly_supports", "does_not_support"}
REQUIRED = ("valid_schema", "no_invented_numbers", "mentions_n")


def _category(name: str) -> str | None:
    for prefix, (category, _) in CATEGORIES.items():
        if name.startswith(prefix):
            return category
    return None


def _cases() -> list[EvalCase]:
    return [load_case(p) for p in discover("historian") if p.name not in EXAMPLES]


def _fake_card(case: EvalCase) -> dict[str, Any]:
    assert case.fake is not None
    text = "\n".join(s.text for s in case.fake.steps if s.text)
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    assert blocks, f"{case.case_id}: the fake answer ends without a json card"
    card: dict[str, Any] = json.loads(blocks[-1])
    return card


def test_thirty_cases_cover_the_seven_categories() -> None:
    cases = _cases()
    counts = Counter(_category(c.case_id) for c in cases)
    assert None not in counts, [c.case_id for c in cases if _category(c.case_id) is None]
    assert {cat: counts[cat] for cat, _ in CATEGORIES.values()} == {cat: n for cat, n in CATEGORIES.values()}
    assert len(cases) == 30


def test_event_studies_cover_cpi_fomc_earnings_token_unlock_and_listing() -> None:
    types = set()
    for case in _cases():
        if _category(case.case_id) == "event":
            assert case.fake is not None
            types |= {str(s.input["event_type"]) for s in case.fake.steps if s.tool == "history.event_study"}
    assert types == {"cpi", "fomc", "earnings", "token_unlock", "listing"}


def test_every_case_has_a_fake_script_and_the_required_properties() -> None:
    for case in _cases():
        assert case.agent == "historian"
        assert case.fake is not None and any(s.text for s in case.fake.steps), case.case_id
        for name in REQUIRED:
            assert name in case.properties, f"{case.case_id} lacks {name}"
        if _category(case.case_id) != "regime-table":  # the regime table card has no verdict
            assert case.properties["verdict_in"]["values"], case.case_id
        else:
            assert case.properties["valid_schema"]["model"] == "RegimeTable"


def test_small_sample_cases_cap_the_verdict_at_weakly_supports() -> None:
    small = [c for c in _cases() if _category(c.case_id) == "small-sample"]
    assert len(small) == 4
    for case in small:
        assert set(case.properties["verdict_in"]["values"]) <= AT_MOST_WEAK, case.case_id
        card = _fake_card(case)
        assert card["n"] < 20, case.case_id
        assert card["verdict"] in AT_MOST_WEAK


def test_gapped_backtests_assert_does_not_support() -> None:
    gapped = [c for c in _cases() if _category(c.case_id) == "gapped-data"]
    assert len(gapped) == 3
    for case in gapped:
        assert case.properties["verdict_in"]["values"] == ["does_not_support"], case.case_id
        assert case.fake is not None
        assert any(s.tool == "history.backtest_rule" for s in case.fake.steps)
        assert any("unrepaired interior gap" in note for note in _fake_card(case)["data_quality_notes"])


def test_two_backtests_are_overfit_and_never_supported() -> None:
    overfit = [c for c in _cases() if c.case_id.startswith("backtest_overfit_")]
    assert len(overfit) == 2
    for case in overfit:
        assert "supports" not in case.properties["verdict_in"]["values"]
        assert _fake_card(case)["overfit_warning"] is True, case.case_id


def test_every_case_links_the_shared_pool() -> None:
    for case in _cases():
        for file in (case.path / "data").glob("*.json"):
            assert file.is_symlink() and file.resolve().parent == (HISTORIAN / "_pool").resolve(), file


async def test_all_thirty_pass_in_fake_mode_without_look_ahead(engine: Engine, tmp_path: Path) -> None:
    report = await run_agent("historian", engine, fake=True)
    by_id = {c.case_id: c for c in report.cases}
    for case in _cases():
        result = by_id[case.case_id]
        assert result.status == "ok", (case.case_id, result.error)
        assert all(p.passed for p in result.properties), (
            case.case_id,
            [p.as_json() for p in result.properties],
        )
        for output in result.tool_outputs:
            if output.get("as_of"):
                assert datetime.fromisoformat(output["as_of"]) <= case.replay_as_of, case.case_id
    assert report.pass_rate == 100.0
