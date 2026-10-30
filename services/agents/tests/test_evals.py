"""The eval harness (ATL-92): fake-mode runs, replay isolation, scoring, compare and the baseline gate.

Needs this checkout's test DB migrated (`cd backend && php artisan test`). Every case runs in a
transaction the runner rolls back, so nothing here leaves rows behind. No test reaches the real CLI:
conftest's autouse fixture makes constructing ClaudeSDKClient fail.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import claude_agent_sdk
import pytest
import yaml
from atlas_agents.evals import guard, properties
from atlas_agents.evals.__main__ import main
from atlas_agents.evals.case import discover, evals_root, load_case
from atlas_agents.evals.runner import run_agent, run_case, write_results
from claude_agent_sdk import ClaudeAgentOptions
from sqlalchemy import Engine

from .conftest import RealClaudeClientConstructedError

HISTORIAN = evals_root() / "historian"
EXAMPLES = ("btc_regime_1h", "btc_stats_1h", "doge_no_data")
PROPERTY_NAMES = ["valid_schema", "no_invented_numbers", "verdict_in", "no_trade", "mentions_n"]


def _copy_case(src: str, root: Path, name: str, edit: Any = None) -> Path:
    dest = root / "historian" / name
    shutil.copytree(HISTORIAN / src, dest)
    if edit is not None:
        path = dest / "case.yaml"
        raw = yaml.safe_load(path.read_text())
        edit(raw)
        path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return dest


def _post_card(raw: dict[str, Any]) -> dict[str, Any]:
    step = next(s for s in raw["fake"]["steps"] if s.get("tool") == "warroom.post_message")
    card: dict[str, Any] = step["input"]["card"]
    return card


# -- the example cases ---------------------------------------------------------------------------------


def test_the_harness_ships_three_example_historian_cases() -> None:
    assert [p.name for p in discover("historian")] == list(EXAMPLES)
    for path in discover("historian"):
        case = load_case(path)
        assert case.fake is not None
        assert list(case.properties) == PROPERTY_NAMES


async def test_fake_run_scores_every_case_and_property(engine: Engine, tmp_path: Path) -> None:
    report = await run_agent("historian", engine, fake=True, label="v-test")
    assert [c.case_id for c in report.cases] == list(EXAMPLES)
    for case in report.cases:
        assert case.status == "ok", case.error
        assert [p.name for p in case.properties] == PROPERTY_NAMES
        assert all(p.passed for p in case.properties), [p.as_json() for p in case.properties]
        assert case.tool_calls >= 2
    assert report.pass_rate == 100.0

    path = write_results(report, tmp_path)
    assert path.parent == tmp_path / "historian"
    data = json.loads(path.read_text())
    assert data["agent"] == "historian" and data["mode"] == "fake" and data["label"] == "v-test"
    assert [c["case_id"] for c in data["cases"]] == list(EXAMPLES)
    for case in data["cases"]:
        assert case["pass_rate"] == 100.0
        assert [p["name"] for p in case["properties"]] == PROPERTY_NAMES
    assert data["overall"] == {"passed": 15, "total": 15, "pass_rate": 100.0}


def test_cli_fake_run_prints_rates_and_writes_results(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = main(["run", "--agent", "historian", "--fake", "--results-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    for case_id in EXAMPLES:
        assert f"PASS  {case_id}  100.00% (5/5)" in out
    assert "OVERALL  100.00% (15/15)" in out
    assert len(list((tmp_path / "historian").glob("*.json"))) == 1


# -- replay isolation ----------------------------------------------------------------------------------


def _after_cutoff(case_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], datetime]:
    case = load_case(case_dir)
    future, past = [], []
    for table, rows in case.snapshot().items():
        for row in rows:
            ts = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
            (future if ts > case.replay_as_of else past).append({"table": table, **row})
    return future, past, case.replay_as_of


def _stamps(row: dict[str, Any]) -> set[str]:
    ts = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
    return {ts.isoformat(), ts.isoformat().replace("+00:00", "Z")}


async def test_rows_after_replay_as_of_never_reach_a_tool_output(engine: Engine, tmp_path: Path) -> None:
    # Ask every candle/funding-reading tool for the widest window it accepts, ending far in the future.
    def wide(raw: dict[str, Any]) -> None:
        raw["fake"]["steps"] = [
            {
                "tool": "okx_market.get_candles",
                "input": {
                    "inst_id": "BTC-USDT-SWAP",
                    "bar": "1H",
                    "end": "2027-01-01T00:00:00Z",
                    "limit": 1000,
                },
            },
            {"tool": "okx_market.get_funding", "input": {"inst_id": "BTC-USDT-SWAP", "history_days": 365}},
            {"tool": "okx_market.get_mark_price", "input": {"inst_id": "BTC-USDT-SWAP"}},
            {
                "tool": "history.compute_stats",
                "input": {"inst_id": "BTC-USDT-SWAP", "bar": "1H", "lookback": 500},
            },
            {"tool": "history.drawdown_profile", "input": {"inst_id": "BTC-USDT-SWAP", "bar": "1H"}},
            *raw["fake"]["steps"],
        ]

    cases = [HISTORIAN / name for name in EXAMPLES] + [_copy_case("btc_stats_1h", tmp_path, "wide", wide)]
    for case_dir in cases:
        future, past, cutoff = _after_cutoff(case_dir)
        assert future, f"{case_dir.name}: the snapshot must hold rows after replay_as_of"
        result = await run_case(load_case(case_dir), engine, fake=True)
        assert result.status == "ok", result.error
        blob = json.dumps(result.tool_outputs)
        for row in future:
            for stamp in _stamps(row):
                assert stamp not in blob, f"{case_dir.name}: {row['table']} row at {stamp} leaked"
        # The markers only the future rows carry (prices 99999.5, funding 0.0777).
        assert "99999.5" not in blob and "0.0777" not in blob
        if case_dir.name != "doge_no_data":
            # Not vacuous: rows at or before the cutoff do reach the tools.
            assert any(stamp in blob for row in past for stamp in _stamps(row)), case_dir.name
        for output in result.tool_outputs:
            if "as_of" in output:
                assert datetime.fromisoformat(output["as_of"]) <= cutoff


# -- scoring failures and continuing --------------------------------------------------------------------


async def test_a_case_failing_valid_schema_is_scored_failed_and_the_run_continues(
    engine: Engine, tmp_path: Path
) -> None:
    def drop_verdict(raw: dict[str, Any]) -> None:
        del _post_card(raw)["verdict"]

    _copy_case("btc_stats_1h", tmp_path, "a_bad_schema", drop_verdict)
    _copy_case("doge_no_data", tmp_path, "b_good")
    report = await run_agent("historian", engine, fake=True, cases_root=tmp_path)
    bad, good = report.cases
    assert bad.case_id == "a_bad_schema" and bad.status == "ok"
    failed = {p.name: p.detail for p in bad.properties if not p.passed}
    assert set(failed) == {"valid_schema", "verdict_in"}
    assert "HistoryVerdict" in failed["valid_schema"] and "verdict" in failed["valid_schema"]
    assert good.case_id == "b_good" and good.passed == good.total == 5
    entry = report.as_json()["cases"][0]
    assert {"name": "valid_schema", "passed": False, "detail": failed["valid_schema"]} in entry["properties"]
    assert report.pass_rate == pytest.approx(80.0)


async def test_a_broken_case_is_a_failed_case_not_a_crash(engine: Engine, tmp_path: Path) -> None:
    broken = _copy_case("doge_no_data", tmp_path, "a_broken")
    (broken / "data" / "no_such_table.json").write_text("[]")
    unreadable = tmp_path / "historian" / "b_unreadable"
    unreadable.mkdir(parents=True)
    (unreadable / "case.yaml").write_text("agent: historian\nprompt: x\n")
    _copy_case("doge_no_data", tmp_path, "c_good")
    report = await run_agent("historian", engine, fake=True, cases_root=tmp_path)
    statuses = {c.case_id: c.status for c in report.cases}
    assert statuses == {"a_broken": "error", "b_unreadable": "invalid", "c_good": "ok"}
    assert "no_such_table" in (report.cases[0].error or "")
    assert all(not p.passed for c in report.cases[:2] for p in c.properties)
    assert report.cases[2].passed == 5


async def test_an_invented_number_fails_no_invented_numbers(engine: Engine, tmp_path: Path) -> None:
    def invent(raw: dict[str, Any]) -> None:
        text = next(s for s in raw["fake"]["steps"] if "text" in s)
        text["text"] += " The 30-day hit rate is 61.3%."

    case = load_case(_copy_case("btc_stats_1h", tmp_path, "invented", invent))
    result = await run_case(case, engine, fake=True)
    outcome = {p.name: p for p in result.properties}["no_invented_numbers"]
    assert not outcome.passed and "61.3%" in outcome.detail


async def test_a_tool_outside_the_access_matrix_is_refused(engine: Engine, tmp_path: Path) -> None:
    def propose(raw: dict[str, Any]) -> None:
        raw["fake"]["steps"].insert(0, {"tool": "warroom.submit_proposal", "input": {}})

    case = load_case(_copy_case("doge_no_data", tmp_path, "proposes", propose))
    result = await run_case(case, engine, fake=True)
    by_name = {p.name: p for p in result.properties}
    assert not by_name["no_trade"].passed and "submit_proposal" in by_name["no_trade"].detail
    assert any("not allowed" in str(o.get("text", "")) for o in result.tool_outputs)


# -- properties and the stub guard --------------------------------------------------------------------


def test_properties_on_a_transcript() -> None:
    t = properties.Transcript(
        status="ok", text="Based on 42 cases, history supports it.", card={"verdict": "x"}
    )
    scored = {
        p.name: p.passed
        for p in properties.score(
            t,
            {
                "mentions_n": {"min": 20},
                "verdict_in": {"values": ["supports"]},
                "no_trade": {},
                "nonsense": {},
            },
        )
    }
    assert scored == {"mentions_n": True, "verdict_in": False, "no_trade": True, "nonsense": False}
    trade = properties.Transcript(status="ok", card={"side": "buy"})
    assert properties.no_trade(trade, {})[0] is False
    assert properties.no_trade(trade, {"expected": False})[0] is True
    failed_run = properties.Transcript(status="rate_limited", error="usage limit")
    assert properties.score(failed_run, {"no_trade": {}})[0].passed is False


def test_card_falls_back_to_a_json_block_in_the_text() -> None:
    text = 'Verdict below.\n```json\n{"verdict": "supports", "n": 25}\n```'
    assert properties.card_from(text, []) == {"verdict": "supports", "n": 25}


def test_stub_guard_matches_tool_numbers_and_flags_others() -> None:
    outputs = [
        {"as_of": "2026-10-20T08:00:00+00:00", "data": {"atr_pct": 0.11963, "n": 72, "mean": "0.000269"}}
    ]
    ok = guard.stub_guard(
        "n = 72 on BTC-USDT-SWAP 1H, ATR 0.12% on 2026-10-20, mean 0.000269", {"n": 72}, outputs
    )
    assert ok.ok and ok.checked == 4
    bad = guard.stub_guard("hit rate 61.3%", None, outputs)
    assert [u["raw"] for u in bad.unmatched] == ["61.3%"]


def test_guard_resolution_honours_the_setting() -> None:
    assert guard.resolve("stub") is guard.stub_guard
    assert callable(guard.resolve("auto"))


# -- compare and the baseline gate --------------------------------------------------------------------


def _results(path: Path, rates: dict[str, float], overall: float, label: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "agent": "historian",
                "mode": "fake",
                "label": label,
                "cases": [{"case_id": k, "pass_rate": v} for k, v in rates.items()],
                "overall": {"pass_rate": overall},
            }
        )
    )
    return path


def test_compare_prints_the_change_per_case_and_overall(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    a = _results(tmp_path / "a.json", {"c1": 100.0, "c2": 60.0, "gone": 40.0}, 80.0, "prompt-v1")
    b = _results(tmp_path / "b.json", {"c1": 80.0, "c2": 100.0, "new": 100.0}, 93.33, "prompt-v2")
    assert main(["compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "prompt-v1" in out and "prompt-v2" in out
    lines = {line.split()[0]: line for line in out.splitlines()[2:]}
    assert lines["c1"].endswith("-20.00")
    assert lines["c2"].endswith("+40.00")
    assert lines["gone"].endswith("n/a") and lines["new"].endswith("n/a")
    assert lines["OVERALL"].endswith("+13.33")


@pytest.mark.parametrize(("baseline", "expected"), [(100.0, 1), (85.01, 1), (85.0, 0), (80.0, 0), (60.0, 0)])
def test_run_exits_1_only_when_more_than_5_points_below_baseline(
    tmp_path: Path, baseline: float, expected: int
) -> None:
    def drop_verdict(raw: dict[str, Any]) -> None:
        del _post_card(raw)["verdict"]

    _copy_case("btc_stats_1h", tmp_path, "a_bad_schema", drop_verdict)  # 3/5
    _copy_case("doge_no_data", tmp_path, "b_good")  # 5/5 -> overall 80 %
    base = _results(tmp_path / "baseline.json", {}, baseline, "base")
    code = main(
        [
            "run",
            "--agent",
            "historian",
            "--fake",
            "--cases-dir",
            str(tmp_path),
            "--results-dir",
            str(tmp_path / "out"),
            "--baseline",
            str(base),
        ]
    )
    assert code == expected


def test_the_committed_baseline_is_readable() -> None:
    data = json.loads((HISTORIAN / "baseline.json").read_text())
    assert 0 <= float(data["overall"]["pass_rate"]) <= 100


# -- never the real CLI --------------------------------------------------------------------------------


def test_constructing_the_real_claude_client_fails_in_tests() -> None:
    with pytest.raises(RealClaudeClientConstructedError):
        claude_agent_sdk.ClaudeSDKClient(options=ClaudeAgentOptions())
