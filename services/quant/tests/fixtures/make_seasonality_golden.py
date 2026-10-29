"""Hand calculation of seasonality's hour, weekday and funding buckets (ATL-81).

Independent of atlas_quant: plain Python loops, no numpy or pandas. It writes

- seasonality_1h.csv: 60 bars of 1H, Sat 2026-10-24 22:00 UTC .. Tue 2026-10-27 09:00 UTC, with
  closes from a fixed deterministic formula (so the fixture spans three weekdays and wraps the
  Baghdad day boundary at 21:00 UTC);
- seasonality_1h_expected.json: per-bucket n, mean, median and t-stat of the log returns, by UTC
  hour, by Baghdad hour (UTC+3, no DST), by UTC weekday and by funding offset (window ±2 h around
  00/08/16 UTC).

Run from services/:  uv run python quant/tests/fixtures/make_seasonality_golden.py
The golden test re-runs it and checks both files are unchanged.
"""

from __future__ import annotations

import io
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
BARS = HERE / "seasonality_1h.csv"
EXPECTED = HERE / "seasonality_1h_expected.json"

START = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
N = 60
FUNDING_HOURS = (0, 8, 16)
FUNDING_WINDOW = 2


def _close(i: int) -> float:
    # A wobbly, drifting price: not periodic in 24 bars, so every bucket has distinct returns.
    return round(100.0 + 0.05 * i + 1.7 * math.sin(i * 0.9) + 0.6 * math.cos(i * 2.3), 6)


def _stats(xs: list[float]) -> dict[str, float | int | None]:
    n = len(xs)
    total = 0.0
    for x in xs:
        total += x
    mean = total / n
    s = sorted(xs)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    t: float | None = None
    if n > 1:
        ss = 0.0
        for x in xs:
            ss += (x - mean) ** 2
        sd = math.sqrt(ss / (n - 1))
        if sd > 0:
            t = mean / (sd / math.sqrt(n))
    return {"n": n, "mean": mean, "median": median, "t_stat": t}


def _funding_key(ts: datetime) -> int | None:
    minutes = ts.hour * 60 + ts.minute
    best: int | None = None
    for h in FUNDING_HOURS:
        off = minutes - h * 60
        while off >= 720:
            off -= 1440
        while off < -720:
            off += 1440
        if best is None or abs(off) < abs(best):
            best = off
    assert best is not None
    hour = math.floor(best / 60)
    return hour if -FUNDING_WINDOW <= hour < FUNDING_WINDOW else None


def build() -> tuple[str, str]:
    rows = [(START + timedelta(hours=i), _close(i)) for i in range(N)]
    csv_out = io.StringIO()
    csv_out.write("ts,c\n")
    for ts, c in rows:
        csv_out.write(f"{ts.strftime('%Y-%m-%dT%H:%M:%SZ')},{c:.6f}\n")

    groups: dict[str, dict[str, list[float]]] = {"utc": {}, "baghdad": {}, "weekday": {}, "funding": {}}
    for i in range(1, N):
        ts, c = rows[i]
        r = math.log(c / rows[i - 1][1])
        local = ts + timedelta(hours=3)
        fk = _funding_key(ts)
        for name, key in (
            ("utc", str(ts.hour)),
            ("baghdad", str(local.hour)),
            ("weekday", str(ts.weekday())),
            ("funding", "outside" if fk is None else str(fk)),
        ):
            groups[name].setdefault(key, []).append(r)

    expected = {name: {key: _stats(xs) for key, xs in sorted(g.items())} for name, g in groups.items()}
    expected_json = json.dumps({"n": N - 1, **expected}, indent=2, sort_keys=True) + "\n"
    return csv_out.getvalue(), expected_json


if __name__ == "__main__":
    bars, exp = build()
    BARS.write_text(bars)
    EXPECTED.write_text(exp)
