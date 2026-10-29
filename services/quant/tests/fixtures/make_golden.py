"""Hand calculation of every compute_stats figure for golden_4h.csv (ATL-79).

Independent of atlas_quant: plain Python loops over the CSV with the textbook formulas, no numpy or
pandas. It writes

- golden_4h_worked.csv: the spreadsheet, one row per bar with every intermediate column
  (returns, ln(h/l)², ln(c/o)², true range, Wilder ATR, running high, drawdown);
- golden_4h_expected.json: the final figures the golden test compares to at 1e-9 relative.

Run from services/:  uv run python quant/tests/fixtures/make_golden.py
The golden test also re-runs it and checks both files are unchanged.

Fixture: 28 bars of 4H, Mon 2026-10-26 00:00 UTC .. Fri 2026-10-30 12:00 UTC, asset class crypto,
so P = 365 * 86400 / 14400 = 2190 bars a year.
"""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BARS = HERE / "golden_4h.csv"
WORKED = HERE / "golden_4h_worked.csv"
EXPECTED = HERE / "golden_4h_expected.json"

P = 365 * 86400 / 14400  # 2190
ATR_W = 14
BAGHDAD = timedelta(hours=3)  # Asia/Baghdad is UTC+3 all year (no DST)


def _mean(xs: list[float]) -> float:
    total = 0.0
    for x in xs:
        total += x
    return total / len(xs)


def _std(xs: list[float]) -> float:
    m = _mean(xs)
    ss = 0.0
    for x in xs:
        ss += (x - m) ** 2
    return math.sqrt(ss / (len(xs) - 1))


def _central_moment(xs: list[float], k: int) -> float:
    m = _mean(xs)
    acc = 0.0
    for x in xs:
        acc += (x - m) ** k
    return acc / len(xs)


def build() -> tuple[str, str]:
    with BARS.open() as fh:
        rows = list(csv.DictReader(fh))
    ts = [datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) for r in rows]
    o = [float(r["o"]) for r in rows]
    h = [float(r["h"]) for r in rows]
    lo = [float(r["l"]) for r in rows]
    c = [float(r["c"]) for r in rows]
    n = len(rows)

    # Returns r_t (t >= 1), attributed to bar t.
    simple = [None] + [c[t] / c[t - 1] - 1.0 for t in range(1, n)]
    logr = [None] + [math.log(c[t] / c[t - 1]) for t in range(1, n)]
    s_list = [x for x in simple if x is not None]
    l_list = [x for x in logr if x is not None]

    hl2 = [math.log(h[t] / lo[t]) ** 2 for t in range(n)]
    co2 = [math.log(c[t] / o[t]) ** 2 for t in range(n)]
    gk_term = [0.5 * hl2[t] - (2.0 * math.log(2.0) - 1.0) * co2[t] for t in range(n)]

    # True range and Wilder ATR.
    tr = [h[0] - lo[0]]
    for t in range(1, n):
        tr.append(max(h[t] - lo[t], abs(h[t] - c[t - 1]), abs(lo[t] - c[t - 1])))
    atr: list[float | None] = [None] * n
    atr[ATR_W - 1] = _mean(tr[:ATR_W])
    for t in range(ATR_W, n):
        prev = atr[t - 1]
        assert prev is not None
        atr[t] = (prev * (ATR_W - 1) + tr[t]) / ATR_W

    # Drawdown on closes.
    run_hi: list[float] = []
    dd: list[float] = []
    hi = -math.inf
    for t in range(n):
        hi = max(hi, c[t])
        run_hi.append(hi)
        dd.append(c[t] / hi - 1.0)
    trough = 0
    for t in range(n):
        if dd[t] < dd[trough]:
            trough = t
    peak = max(t for t in range(trough + 1) if c[t] == run_hi[trough])

    # Average daily range over complete UTC days (6 bars of 4H).
    days: dict[str, list[int]] = {}
    for t in range(n):
        days.setdefault(ts[t].strftime("%Y-%m-%d"), []).append(t)
    adr_rows = []
    for day, idx in days.items():
        if len(idx) == 6:
            day_hi = max(h[t] for t in idx)
            day_lo = min(lo[t] for t in idx)
            adr_rows.append((day, (day_hi - day_lo) / o[idx[0]] * 100.0))
    adr = _mean([v for _, v in adr_rows])

    # Hour and weekday tables.
    def hour_table(shift: timedelta) -> list[dict[str, object]]:
        buckets: dict[int, list[float]] = {}
        for t in range(1, n):
            buckets.setdefault((ts[t] + shift).hour, []).append(logr[t])  # type: ignore[arg-type]
        return [
            {
                "hour": hr,
                "n": len(v),
                "mean_log_return": _mean(v),
                "std_log_return": _std(v) if len(v) > 1 else None,
            }
            for hr, v in sorted(buckets.items())
        ]

    wd: dict[int, list[float]] = {}
    for t in range(1, n):
        wd.setdefault(ts[t].weekday(), []).append(simple[t])  # type: ignore[arg-type]

    # Sample skew G1 and excess kurtosis G2 from the biased moments.
    m = len(l_list)
    m2 = _central_moment(l_list, 2)
    g1 = _central_moment(l_list, 3) / m2**1.5
    g2 = _central_moment(l_list, 4) / m2**2 - 3.0
    skew = g1 * math.sqrt(m * (m - 1)) / (m - 2)
    kurt = ((m + 1) * g2 + 6.0) * (m - 1) / ((m - 2) * (m - 3))

    last_atr = atr[-1]
    assert last_atr is not None
    expected = {
        "periods_per_year": P,
        "n": n,
        "log_returns": {
            "count": m,
            "mean": _mean(l_list),
            "std": _std(l_list),
            "min": min(l_list),
            "max": max(l_list),
            "total": math.log(c[-1] / c[0]),
        },
        "simple_returns": {
            "count": len(s_list),
            "mean": _mean(s_list),
            "std": _std(s_list),
            "min": min(s_list),
            "max": max(s_list),
            "total": c[-1] / c[0] - 1.0,
        },
        "realised_vol": _std(l_list) * math.sqrt(P),
        "parkinson_vol": math.sqrt(_mean(hl2) / (4.0 * math.log(2.0))) * math.sqrt(P),
        "garman_klass_vol": math.sqrt(_mean(gk_term)) * math.sqrt(P),
        "atr": last_atr,
        "atr_pct": last_atr / c[-1] * 100.0,
        "skew": skew,
        "excess_kurtosis": kurt,
        "drawdown": {
            "max_drawdown": dd[trough],
            "peak_ts": ts[peak].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "trough_ts": ts[trough].strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "adr_pct": adr,
        "adr_days": len(adr_rows),
        "hourly_utc": hour_table(timedelta(0)),
        "hourly_local": hour_table(BAGHDAD),
        "weekday": [{"weekday": d, "n": len(v), "mean_return": _mean(v)} for d, v in sorted(wd.items())],
    }

    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(
        [
            "ts",
            "o",
            "h",
            "l",
            "c",
            "simple_r",
            "log_r",
            "ln_hl_sq",
            "ln_co_sq",
            "gk_term",
            "tr",
            "atr_wilder",
            "running_high",
            "drawdown",
            "hour_utc",
            "hour_baghdad",
            "weekday",
        ]
    )
    for t in range(n):
        w.writerow(
            [
                rows[t]["ts"],
                o[t],
                h[t],
                lo[t],
                c[t],
                "" if simple[t] is None else repr(simple[t]),
                "" if logr[t] is None else repr(logr[t]),
                repr(hl2[t]),
                repr(co2[t]),
                repr(gk_term[t]),
                repr(tr[t]),
                "" if atr[t] is None else repr(atr[t]),
                run_hi[t],
                repr(dd[t]),
                ts[t].hour,
                (ts[t] + BAGHDAD).hour,
                ts[t].weekday(),
            ]
        )
    w.writerow([])
    w.writerow(["# ADR complete UTC days", *[f"{d}={v!r}" for d, v in adr_rows]])
    w.writerow(["# P = 365*86400/14400", P])
    w.writerow(
        [
            "# m2 m3 m4 of log returns",
            repr(m2),
            repr(_central_moment(l_list, 3)),
            repr(_central_moment(l_list, 4)),
        ]
    )
    w.writerow(["# g1 = m3/m2^1.5; G1 = g1*sqrt(m(m-1))/(m-2)", repr(g1), repr(skew)])
    w.writerow(["# g2 = m4/m2^2-3; G2 = ((m+1)g2+6)(m-1)/((m-2)(m-3))", repr(g2), repr(kurt)])
    return out.getvalue(), json.dumps(expected, indent=2) + "\n"


if __name__ == "__main__":
    worked, expected_json = build()
    WORKED.write_text(worked)
    EXPECTED.write_text(expected_json)
