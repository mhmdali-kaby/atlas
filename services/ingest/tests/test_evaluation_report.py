import subprocess
import sys
import types
from collections.abc import Iterator, Sequence

import pytest
from atlas_ingest.evaluation import registry
from atlas_ingest.evaluation.__main__ import main, report_lines
from atlas_ingest.evaluation.fixture import HeadlineRow, load_fixture


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    registry.clear()
    yield
    registry.clear()


def test_report_says_not_registered_for_missing_stages() -> None:
    lines = report_lines(load_fixture())
    assert lines[0] == "fixture: 300 rows"
    assert "linking: not registered" in lines
    assert "event_type: not registered" in lines
    assert "clustering: not registered" in lines


def gold_linker(row: HeadlineRow) -> list[str]:
    return row.expected_instruments


def test_report_scores_registered_stages() -> None:
    registry.register_linker(gold_linker)
    registry.register_classifier(lambda r: "other")

    def singleton_clusterer(rows: Sequence[HeadlineRow]) -> dict[str, str]:
        return {r.id: r.id for r in rows}

    registry.register_clusterer(singleton_clusterer)
    lines = report_lines(load_fixture(), confusion=True)
    assert any(ln.startswith("linking: precision=1.0000 recall=1.0000") and "PASS" in ln for ln in lines)
    assert any(ln.startswith("event_type: accuracy=0.2000 (60/300)") for ln in lines)
    assert any(ln.startswith("  confused earnings -> other=") for ln in lines)
    assert any(ln.startswith("clustering: pairwise_f1=0.0000") and "BELOW TARGET" in ln for ln in lines)


def test_cli_override_registers_dotted_callable(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = types.ModuleType("atl58_fake_linker")
    plugin.link = gold_linker  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "atl58_fake_linker", plugin)
    assert main(["report", "--linker", "atl58_fake_linker:link"]) == 0
    out = capsys.readouterr().out
    assert "linking: precision=1.0000" in out
    # ATL-63's classifier arrives through its entry point alongside the CLI override.
    assert "event_type: accuracy=" in out


def test_module_runs_as_cli() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "atlas_ingest.evaluation", "report"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "linking: not registered" in proc.stdout
    assert "clustering: not registered" in proc.stdout
