"""Writes backtest_1h.csv: 400 hourly bars from a seeded random walk with trending stretches (ATL-82).

Run once; the CSV is committed and is the input of make_backtest_golden.py. Not re-run by the tests.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

rng = np.random.default_rng(82)
n = 400
drift = np.tile(np.repeat([0.002, -0.0015, 0.0005, -0.002, 0.0025, -0.0005, 0.001, -0.001], n // 16), 2)
r = drift + rng.normal(0.0, 0.006, n)
c = 100.0 * np.exp(np.cumsum(r))
o = np.concatenate(([100.0], c[:-1])) * (1.0 + rng.normal(0.0, 0.0005, n))
h = np.maximum(o, c) * (1.0 + np.abs(rng.normal(0.0, 0.002, n)))
lo = np.minimum(o, c) * (1.0 - np.abs(rng.normal(0.0, 0.002, n)))
start = datetime(2026, 9, 1, tzinfo=UTC)
with (Path(__file__).parent / "backtest_1h.csv").open("w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["ts", "o", "h", "l", "c", "vol"])
    for i in range(n):
        ts = (start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        w.writerow([ts, f"{o[i]:.4f}", f"{h[i]:.4f}", f"{lo[i]:.4f}", f"{c[i]:.4f}", "1000"])
