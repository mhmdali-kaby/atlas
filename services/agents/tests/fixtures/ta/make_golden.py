"""Reference values for the ATL-132 TA golden test (EMA20, RSI14, MACD 12/26/9, ADX14, ATR14).

Plain-Python loops over lists only (no numpy or pandas), so it is independent of
atlas_quant.indicators and atlas_agents.ta. It writes:

- ohlcv_1h_300.csv: 300 seeded random-walk 1H bars (ts = bar open time, UTC), 4 decimals;
- ohlcv_1h_300_expected.json: every bar's value of each indicator (null while undefined).

Definitions (the same as atlas_quant.indicators):
- EMA(span): y_0 = x_0, y_t = a x_t + (1 - a) y_{t-1}, a = 2 / (span + 1); reported once span values
  were seen.
- RSI(w): d_t = c_t - c_{t-1} (t >= 1); G and L are the same recursion with a = 1 / w over max(d, 0)
  and max(-d, 0), seeded at t = 1; reported from t = w; RSI = 100 - 100 / (1 + G / L).
- MACD = EMA12 - EMA26; signal = EMA9 of MACD seeded at the first MACD value; hist = MACD - signal.
- ATR(w): TR_0 = h_0 - l_0, TR_t = max(h - l, |h - c_{t-1}|, |l - c_{t-1}|); ATR_{w-1} = mean(TR_0..TR_{w-1}),
  then ATR_t = (ATR_{t-1} (w - 1) + TR_t) / w.
- ADX(w): +DM/-DM/TR from t = 1; S_w = sum(x_1..x_w) / w, then S_t = (S_{t-1} (w - 1) + x_t) / w;
  +DI = 100 S(+DM) / S(TR), -DI likewise; DX = 100 |+DI - -DI| / (+DI + -DI) from t = w;
  ADX_{2w-1} = mean(DX_w..DX_{2w-1}), then ADX_t = (ADX_{t-1} (w - 1) + DX_t) / w.

Run: ``uv run python agents/tests/fixtures/ta/make_golden.py`` (the golden test re-runs it).
"""

from __future__ import annotations

import csv
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
BARS = 300


def make_bars() -> list[dict[str, float | str]]:
    rng = random.Random(132)  # noqa: S311 - seeded fixture data, not crypto
    start = datetime(2026, 9, 1, tzinfo=UTC)
    price = 100.0
    rows: list[dict[str, float | str]] = []
    for i in range(BARS):
        o = price
        c = o * (1.0 + rng.gauss(0.0002, 0.008))
        h = max(o, c) * (1.0 + abs(rng.gauss(0.0, 0.003)))
        lo = min(o, c) * (1.0 - abs(rng.gauss(0.0, 0.003)))
        o, h, lo, c = (round(v, 4) for v in (o, h, lo, c))
        rows.append(
            {
                "ts": (start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "o": o,
                "h": max(h, o, c),
                "l": min(lo, o, c),
                "c": c,
                "vol": round(1000.0 + rng.random() * 500.0, 2),
            }
        )
        price = c
    return rows


def ema(xs: list[float | None], span: int) -> list[float | None]:
    a = 2.0 / (span + 1.0)
    out: list[float | None] = []
    y: float | None = None
    seen = 0
    for x in xs:
        if x is None:
            out.append(None)
            continue
        y = x if y is None else a * x + (1.0 - a) * y
        seen += 1
        out.append(y if seen >= span else None)
    return out


def rsi(c: list[float], w: int) -> list[float | None]:
    a = 1.0 / w
    out: list[float | None] = [None]
    g = lo = 0.0
    for t in range(1, len(c)):
        d = c[t] - c[t - 1]
        up, dn = max(d, 0.0), max(-d, 0.0)
        if t == 1:
            g, lo = up, dn
        else:
            g = a * up + (1.0 - a) * g
            lo = a * dn + (1.0 - a) * lo
        out.append(100.0 - 100.0 / (1.0 + g / lo) if t >= w else None)
    return out


def true_ranges(h: list[float], lo: list[float], c: list[float]) -> list[float]:
    tr = [h[0] - lo[0]]
    for t in range(1, len(c)):
        tr.append(max(h[t] - lo[t], abs(h[t] - c[t - 1]), abs(lo[t] - c[t - 1])))
    return tr


def atr(h: list[float], lo: list[float], c: list[float], w: int) -> list[float | None]:
    tr = true_ranges(h, lo, c)
    out: list[float | None] = [None] * len(tr)
    v = sum(tr[:w]) / w
    out[w - 1] = v
    for t in range(w, len(tr)):
        v = (v * (w - 1) + tr[t]) / w
        out[t] = v
    return out


def wilder(xs: list[float], w: int) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    v = sum(xs[1 : w + 1]) / w
    out[w] = v
    for t in range(w + 1, len(xs)):
        v = (v * (w - 1) + xs[t]) / w
        out[t] = v
    return out


def adx(h: list[float], lo: list[float], c: list[float], w: int) -> dict[str, list[float | None]]:
    n = len(c)
    pdm, mdm, tr = [0.0] * n, [0.0] * n, [0.0] * n
    for t in range(1, n):
        up, down = h[t] - h[t - 1], lo[t - 1] - lo[t]
        pdm[t] = up if up > down and up > 0 else 0.0
        mdm[t] = down if down > up and down > 0 else 0.0
        tr[t] = max(h[t] - lo[t], abs(h[t] - c[t - 1]), abs(lo[t] - c[t - 1]))
    s_tr, s_p, s_m = wilder(tr, w), wilder(pdm, w), wilder(mdm, w)
    pdi: list[float | None] = [None] * n
    mdi: list[float | None] = [None] * n
    dx: list[float | None] = [None] * n
    for t in range(w, n):
        st, sp, sm = s_tr[t], s_p[t], s_m[t]
        assert st is not None and sp is not None and sm is not None
        p, m = 100.0 * sp / st, 100.0 * sm / st
        pdi[t], mdi[t] = p, m
        dx[t] = 100.0 * abs(p - m) / (p + m)
    out: list[float | None] = [None] * n
    first = [d for d in dx[w : 2 * w] if d is not None]
    v = sum(first) / len(first)
    out[2 * w - 1] = v
    for t in range(2 * w, n):
        d = dx[t]
        assert d is not None
        v = (v * (w - 1) + d) / w
        out[t] = v
    return {"adx14": out, "plus_di14": pdi, "minus_di14": mdi}


def main() -> None:
    rows = make_bars()
    with (HERE / "ohlcv_1h_300.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ts", "o", "h", "l", "c", "vol"])
        writer.writeheader()
        writer.writerows(rows)
    # Re-read the CSV so the reference sees exactly the stored numbers.
    with (HERE / "ohlcv_1h_300.csv").open() as fh:
        stored = list(csv.DictReader(fh))
    h = [float(r["h"]) for r in stored]
    lo = [float(r["l"]) for r in stored]
    c = [float(r["c"]) for r in stored]
    cc: list[float | None] = list(c)
    e12, e26 = ema(cc, 12), ema(cc, 26)
    macd = [a - b if a is not None and b is not None else None for a, b in zip(e12, e26, strict=True)]
    signal = ema(macd, 9)
    hist = [m - s if m is not None and s is not None else None for m, s in zip(macd, signal, strict=True)]
    expected: dict[str, list[float | None]] = {
        "ema20": ema(cc, 20),
        "rsi14": rsi(c, 14),
        "macd": macd,
        "macd_signal": signal,
        "macd_hist": hist,
        "atr14": atr(h, lo, c, 14),
        **adx(h, lo, c, 14),
    }
    (HERE / "ohlcv_1h_300_expected.json").write_text(json.dumps(expected, indent=1) + "\n")


if __name__ == "__main__":
    main()
