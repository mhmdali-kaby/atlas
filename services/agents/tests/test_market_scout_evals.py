"""ATL-112: Market Scout's 30 replay eval cases (brief §7.1, §7.11, §9).

Counts the cases per category, checks each asserts the shared properties plus its category's, shows
the category properties fail on a bad watchlist, checks the correlated cases' candles really are
correlated, and runs all of them in fake mode through the real watchlist pipeline (feature table
from the snapshot, code rules, number guard). Needs this checkout's test DB migrated
(``cd backend && php artisan test``). No test reaches the real CLI (conftest).
"""

from __future__ import annotations

import itertools
import json
import math
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from atlas_agents.evals import properties as props
from atlas_agents.evals.case import EvalCase, discover, evals_root, load_case
from atlas_agents.evals.runner import run_agent
from atlas_agents.evals.snapshot import load_snapshot
from sqlalchemy import Engine, text

SCOUT = evals_root() / "market_scout"
# Case id prefix -> (category, the property that category must assert).
CATEGORIES = {
    "mixed_": ("normal mixed-asset day", "asset_classes"),
    "notrade_": ("known no-trade day", "no_trade_day"),
    "chasing_": ("chasing > 3 ATR", "not_picked"),
    "correlated_": ("correlated candidates", "max_correlated"),
    "timeout_": ("teammate timeout", "max_conviction"),
    "owner_": ("owner pin/block", "not_picked"),
    "single_": ("single-evidence pick", "not_picked"),
}
REQUIRED = ("valid_watchlist", "no_invented_numbers", "rejected_listed")


def _category(name: str) -> str | None:
    for prefix, (category, _) in CATEGORIES.items():
        if name.startswith(prefix):
            return category
    return None


def _prefix(name: str) -> str:
    return next(p for p in CATEGORIES if name.startswith(p))


def _cases() -> list[EvalCase]:
    return [load_case(p) for p in discover("market_scout")]


def _answer(case: EvalCase) -> dict[str, Any]:
    assert case.fake is not None
    body = "\n".join(s.text for s in case.fake.steps if s.text)
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)
    assert blocks, f"{case.case_id}: the fake answer ends without a json block"
    answer: dict[str, Any] = json.loads(blocks[-1])
    return answer


def test_at_least_thirty_cases_and_three_per_category() -> None:
    cases = _cases()
    assert len(cases) >= 30
    counts = Counter(_category(c.case_id) for c in cases)
    assert None not in counts, [c.case_id for c in cases if _category(c.case_id) is None]
    for category, _ in CATEGORIES.values():
        assert counts[category] >= 3, (category, counts[category])


def test_every_case_asserts_the_shared_and_its_category_property() -> None:
    for case in _cases():
        assert case.agent == "market_scout"
        assert case.fake is not None and any(s.text for s in case.fake.steps), case.case_id
        for name in REQUIRED:
            assert name in case.properties, f"{case.case_id} lacks {name}"
        assert case.properties["rejected_listed"]["count"] == 3
        _, wanted = CATEGORIES[_prefix(case.case_id)]
        assert wanted in case.properties, f"{case.case_id} lacks its category property {wanted}"
        assert len(_answer(case)["rejected"]) == 3, case.case_id


def test_category_specifics() -> None:
    for case in _cases():
        prefix = _prefix(case.case_id)
        answer = _answer(case)
        picked = {p["inst_id"] for p in answer["picks"]}
        if prefix == "timeout_":
            assert case.properties["max_conviction"]["max"] <= 0.5
            assert case.properties["verdict_in"] == {"field": "status", "values": ["partial_evidence"]}
            assert set(case.inputs["replies"]) < {"news_intelligence", "historian"}, case.case_id
            # The model asks for more than the cap: code, not the script, keeps it at 0.5.
            assert max(p["conviction"] for p in answer["picks"]) > 0.5
        if prefix == "notrade_":
            assert case.properties["no_trade_day"] == {"max_picks": 1, "max_conviction": 0.5}
        if prefix in ("chasing_", "single_"):
            (banned,) = case.properties["not_picked"]["inst_ids"]
            assert banned in {r["inst_id"] for r in answer["rejected"]}, case.case_id
        if prefix == "owner_":
            snapshot = case.snapshot()
            assert snapshot["settings"][0]["data"].get("blocklist") or snapshot.get("watchlist_items")
            if "picked" in case.properties:
                (pin,) = case.properties["picked"]["inst_ids"]
                assert any(i["inst_id"] == pin and i["pinned_by_owner"] for i in snapshot["watchlist_items"])
        if prefix == "correlated_":
            (group,) = case.properties["max_correlated"]["groups"]
            assert len(picked & set(group)) <= 2


def _log_returns(rows: list[dict[str, Any]]) -> list[float]:
    closes = [float(r["c"]) for r in sorted(rows, key=lambda r: r["ts"])][-31:]
    return [math.log(b / a) for a, b in itertools.pairwise(closes)]


def _corr(xs: list[float], ys: list[float]) -> float:
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return cov / (vx * vy)


def test_correlated_groups_are_above_the_threshold_in_the_candles() -> None:
    for case in _cases():
        if not case.case_id.startswith("correlated_"):
            continue
        (group,) = case.properties["max_correlated"]["groups"]
        candles = case.snapshot()["candles"]
        rets = {i: _log_returns([c for c in candles if c["inst_id"] == i]) for i in group}
        for a in group:
            for b in group:
                if a < b:
                    assert _corr(rets[a], rets[b]) > 0.8, (case.case_id, a, b)


# -- the category properties fail on a bad watchlist --------------------------------------------------


def _watchlist(picks: list[tuple[str, str, float]], status: str = "published") -> props.Transcript:
    card = {
        "type": "watchlist",
        "status": status,
        "picks": [
            {
                "inst_id": inst,
                "rank": rank,
                "asset_class": cls,
                "bias": "long",
                "conviction": conviction,
                "thesis": "x",
                "catalysts": [],
                "key_levels": {},
                "invalidation": "x",
                "data_as_of": "2026-10-13T04:55:00Z",
            }
            for rank, (inst, cls, conviction) in enumerate(picks, start=1)
        ],
        "rejected": [{"inst_id": "A-USDT-SWAP", "reason": "x"}],
    }
    return props.Transcript(status="ok", card=card)


FIVE_STRONG = [
    ("BTC-USDT-SWAP", "crypto", 0.8),
    ("ETH-USDT-SWAP", "crypto", 0.8),
    ("SOL-USDT-SWAP", "crypto", 0.8),
    ("XAU-USDT-SWAP", "commodity", 0.8),
    ("TSLA-USDT-SWAP", "stock", 0.8),
]


def test_no_trade_day_fails_for_five_picks_at_conviction_0_8() -> None:
    params = {"max_picks": 1, "max_conviction": 0.5}
    passed, detail = props.no_trade_day(_watchlist(FIVE_STRONG), params)
    assert not passed
    assert "5 picks" in detail
    # One pick is allowed, but not a confident one.
    passed, detail = props.no_trade_day(_watchlist([("BTC-USDT-SWAP", "crypto", 0.8)]), params)
    assert not passed and "0.8" in detail
    assert props.no_trade_day(_watchlist([("BTC-USDT-SWAP", "crypto", 0.4)]), params)[0]
    assert props.no_trade_day(_watchlist([]), params)[0]
    # Scored through the case machinery, a failed property is a failed case line.
    (result,) = props.score(_watchlist(FIVE_STRONG), {"no_trade_day": params})
    assert not result.passed


def test_the_other_category_properties_fail_on_a_bad_watchlist() -> None:
    bad = _watchlist(FIVE_STRONG)
    assert not props.max_conviction(bad, {"max": 0.5})[0]
    assert not props.not_picked(bad, {"inst_ids": ["SOL-USDT-SWAP"]})[0]
    assert not props.picked(bad, {"inst_ids": ["XAG-USDT-SWAP"]})[0]
    group = [["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]]
    assert not props.max_correlated(bad, {"groups": group, "max": 2})[0]
    assert not props.asset_classes(_watchlist(FIVE_STRONG[:3]), {"min": 2})[0]
    assert not props.rejected_listed(bad, {"count": 3})[0]
    assert not props.picks_count(bad, {"max": 3})[0]
    assert not props.verdict_in(bad, {"field": "status", "values": ["partial_evidence"]})[0]
    broken = _watchlist(FIVE_STRONG)
    assert broken.card is not None
    broken.card["picks"][0]["conviction"] = 1.4
    assert not props.valid_watchlist(broken, {})[0]
    assert props.valid_watchlist(_watchlist(FIVE_STRONG), {})[0]


# -- the snapshot loader ------------------------------------------------------------------------------


def test_snapshot_reflects_settings_and_replaces_the_envs_row(engine: Engine) -> None:
    case = next(c for c in _cases() if c.case_id.startswith("owner_04"))
    snapshot = {k: v for k, v in case.snapshot().items() if k in ("settings", "settings_history")}
    with engine.connect() as conn, conn.begin() as tx:
        load_snapshot(conn, snapshot, replay_as_of=case.replay_as_of)
        load_snapshot(conn, snapshot, replay_as_of=case.replay_as_of)  # a second load replaces, no clash
        rows = conn.execute(text("SELECT data FROM settings WHERE env = 'demo'")).all()
        assert len(rows) == 1
        assert rows[0][0]["blocklist"] == ["XAU-USDT-SWAP", "TSLA-USDT-SWAP"]
        tx.rollback()


# -- the whole run ------------------------------------------------------------------------------------


async def test_all_cases_pass_in_fake_mode_without_look_ahead(engine: Engine, tmp_path: Path) -> None:
    report = await run_agent("market_scout", engine, fake=True)
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


def test_baseline_is_committed_from_fake_mode() -> None:
    baseline = json.loads((SCOUT / "baseline.json").read_text(encoding="utf-8"))
    assert baseline["agent"] == "market_scout"
    assert baseline["mode"] == "fake"
    assert len(baseline["cases"]) >= 30


async def test_an_invented_level_fails_no_invented_numbers(engine: Engine, tmp_path: Path) -> None:
    source = SCOUT / "mixed_01_xau_tsla"
    copy = tmp_path / "market_scout" / source.name
    shutil.copytree(source, copy)
    case_file = copy / "case.yaml"
    body = case_file.read_text(encoding="utf-8")
    support = re.search(r'\\"support\\": ([0-9.]+)', body) or re.search(r'"support": ([0-9.]+)', body)
    assert support is not None
    case_file.write_text(body.replace(support.group(1), "98765.43", 1), encoding="utf-8")
    report = await run_agent("market_scout", engine, fake=True, cases_root=tmp_path)
    (result,) = report.cases
    assert result.status == "ok", result.error
    guard = next(p for p in result.properties if p.name == "no_invented_numbers")
    assert not guard.passed and "98765.43" in guard.detail
