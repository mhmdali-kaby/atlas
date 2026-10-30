"""``python -m atlas_agents.evals run|compare`` (brief §7.11, §9).

run --agent historian [--case ID] [--fake] [--label v3] [--cases-dir DIR] [--results-dir DIR]
    [--baseline FILE]
    Runs the cases on the checkout's test DB (run `php artisan test` first so it is migrated),
    prints a pass rate per case, writes evals/results/<agent>/<timestamp>.json, and exits 1 when
    the overall pass rate is more than agents.evals.max_drop_points below the committed
    evals/<agent>/baseline.json. Without --fake it uses the real Claude CLI login.
compare A.json B.json
    Prints the pass-rate change per case and overall.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from atlas_shared import testing
from atlas_shared.db import make_engine

from atlas_agents.evals.compare import render
from atlas_agents.evals.runner import CaseResult, baseline_path, check_baseline, run_agent, write_results


def _print_case(result: CaseResult) -> None:
    mark = "PASS" if result.passed == result.total and result.total else "FAIL"
    rate = 100.0 * result.passed / result.total if result.total else 0.0
    print(f"{mark}  {result.case_id}  {rate:.2f}% ({result.passed}/{result.total})  [{result.status}]")
    for prop in result.properties:
        if not prop.passed:
            print(f"      - {prop.name}: {prop.detail}")


def _run(args: argparse.Namespace) -> int:
    engine = make_engine(testing.db_settings())
    cases_dir = Path(args.cases_dir) if args.cases_dir else None
    try:
        report = asyncio.run(
            run_agent(
                args.agent,
                engine,
                fake=args.fake,
                cases_root=cases_dir,
                case_id=args.case,
                label=args.label,
                on_case=_print_case,
            )
        )
    finally:
        engine.dispose()
    if not report.cases:
        print(f"no eval cases for {args.agent}", file=sys.stderr)
        return 2
    base = Path(args.baseline) if args.baseline else baseline_path(args.agent, cases_dir)
    ok = check_baseline(report, base)
    path = write_results(report, Path(args.results_dir) if args.results_dir else None)
    print(f"OVERALL  {report.pass_rate:.2f}% ({report.passed}/{report.total})")
    assert report.baseline is not None
    if report.baseline["pass_rate"] is None:
        print(f"baseline: none at {base}")
    else:
        print(
            f"baseline: {report.baseline['pass_rate']:.2f}% "
            f"(allowed drop {report.baseline['max_drop_points']:g} points) -> {'ok' if ok else 'BELOW'}"
        )
    print(f"results: {path}")
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m atlas_agents.evals")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run an agent's eval cases")
    run.add_argument("--agent", required=True)
    run.add_argument("--case", help="run only this case id")
    run.add_argument("--fake", action="store_true", help="replay the cases' scripted answers (no CLI)")
    run.add_argument("--label", help="a name for this run, e.g. the prompt version")
    run.add_argument("--cases-dir", help="cases root (default services/agents/evals)")
    run.add_argument("--results-dir", help="results root (default services/agents/evals/results)")
    run.add_argument("--baseline", help="baseline results file (default <cases-dir>/<agent>/baseline.json)")
    cmp_ = sub.add_parser("compare", help="pass-rate change between two results files")
    cmp_.add_argument("a")
    cmp_.add_argument("b")
    args = parser.parse_args(argv)
    if args.command == "compare":
        print(render(Path(args.a), Path(args.b)))
        return 0
    return _run(args)


if __name__ == "__main__":
    sys.exit(main())
