"""Hand calculation of an SMA 10/30 crossover on backtest_1h.csv (ATL-82 golden reference).

Plain-Python loops only (no numpy, pandas or vectorbt), so it is independent of atlas_quant.backtest.
It writes, for two cost cases (zero cost; fee 0.05 % + 2 bps slippage):

- backtest_1h_worked_<case>.csv: every bar's SMA10, SMA30, raw signals, the fill (if any), cash,
  units and equity, like a spreadsheet;
- backtest_1h_expected.json: total return, trade count, per-trade PnL, Sharpe, max drawdown and
  final equity per case.

Rules (the same as the module docstring of atlas_quant.backtest):
- SMA_n(t) = mean of closes t-n+1..t, defined from t = n-1.
- Entry signal on bar t: SMA10 crosses above SMA30 (SMA10(t) > SMA30(t) and SMA10(t-1) <= SMA30(t-1));
  exit signal: the reverse cross. Both need the SMAs defined at t and t-1.
- A signal on bar t fills at bar t+1's open; a signal on the last bar is dropped; a position open on
  the last bar is sold at that bar's close.
- Buy: px = open x (1 + s), units = cash / (px x (1 + f)), fee = units x px x f, cash = 0.
  Sell: px = price x (1 - s), cash = units x px x (1 - f). Trade PnL = cash after sell - cash before buy.
- Equity(t) = cash + units x close(t). Sharpe = mean / stdev (n - 1) of bar returns x sqrt(8760).

Run: ``uv run python quant/tests/fixtures/make_backtest_golden.py`` (the golden test re-runs it).
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
PERIODS_PER_YEAR = 365 * 24
CASES = {"zero_cost": (0.0, 0.0), "after_cost": (0.0005, 0.0002)}


def load() -> list[dict[str, float | str]]:
    with (HERE / "backtest_1h.csv").open() as fh:
        return [
            {"ts": row["ts"], **{k: float(row[k]) for k in ("o", "h", "l", "c")}}
            for row in csv.DictReader(fh)
        ]


def sma(closes: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    for t in range(len(closes)):
        if t < n - 1:
            out.append(None)
        else:
            total = 0.0
            for k in range(t - n + 1, t + 1):
                total += closes[k]
            out.append(total / n)
    return out


def simulate(rows: list[dict[str, float | str]], fee: float, slip: float) -> dict[str, object]:
    closes = [float(r["c"]) for r in rows]
    opens = [float(r["o"]) for r in rows]
    fast, slow = sma(closes, 10), sma(closes, 30)
    n = len(rows)
    entry_sig = [False] * n
    exit_sig = [False] * n
    for t in range(1, n):
        f0, s0, f1, s1 = fast[t - 1], slow[t - 1], fast[t], slow[t]
        if f0 is None or s0 is None or f1 is None or s1 is None:
            continue
        entry_sig[t] = f1 > s1 and f0 <= s0
        exit_sig[t] = f1 < s1 and f0 >= s0

    cash, units, cash_at_entry = 1.0, 0.0, 0.0
    trades: list[float] = []
    worked: list[dict[str, object]] = []
    equity: list[float] = []
    for t in range(n):
        enter = t >= 1 and entry_sig[t - 1] and t < n - 1
        leave = t >= 1 and exit_sig[t - 1]
        price = opens[t]
        if t == n - 1 and not leave:
            leave, price = True, closes[t]
        action, fill = "", None
        if enter and leave:
            pass  # conflicting signals cancel out
        elif units > 0 and leave:
            fill = price * (1 - slip)
            cash = units * fill * (1 - fee)
            units = 0.0
            trades.append(cash - cash_at_entry)
            action = "sell"
        elif units == 0 and enter:
            fill = price * (1 + slip)
            cash_at_entry = cash
            units = cash / (fill * (1 + fee))
            cash = 0.0
            action = "buy"
        eq = cash + units * closes[t]
        equity.append(eq)
        worked.append(
            {
                "ts": rows[t]["ts"],
                "o": opens[t],
                "c": closes[t],
                "sma10": "" if fast[t] is None else repr(fast[t]),
                "sma30": "" if slow[t] is None else repr(slow[t]),
                "entry_signal": int(entry_sig[t]),
                "exit_signal": int(exit_sig[t]),
                "action": action,
                "fill_price": "" if fill is None else repr(fill),
                "cash": repr(cash),
                "units": repr(units),
                "equity": repr(eq),
            }
        )

    rets = [equity[t] / equity[t - 1] - 1 for t in range(1, n)]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    peak, max_dd = equity[0], 0.0
    for eq in equity:
        peak = max(peak, eq)
        max_dd = min(max_dd, eq / peak - 1)
    return {
        "worked": worked,
        "summary": {
            "total_return": equity[-1] / equity[0] - 1,
            "final_equity": equity[-1],
            "n_trades": len(trades),
            "trade_pnl": trades,
            "wins": sum(1 for p in trades if p > 0),
            "sharpe": mean / math.sqrt(var) * math.sqrt(PERIODS_PER_YEAR),
            "max_drawdown": max_dd,
        },
    }


def main() -> None:
    rows = load()
    expected: dict[str, object] = {}
    for name, (fee, slip) in CASES.items():
        result = simulate(rows, fee, slip)
        worked = result["worked"]
        assert isinstance(worked, list)
        with (HERE / f"backtest_1h_worked_{name}.csv").open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(worked[0]))
            writer.writeheader()
            writer.writerows(worked)
        expected[name] = {"fee_rate": fee, "slippage": slip, **result["summary"]}  # type: ignore[dict-item]
    (HERE / "backtest_1h_expected.json").write_text(json.dumps(expected, indent=2) + "\n")


if __name__ == "__main__":
    main()
