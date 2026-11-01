# ATL-212 — Risk Engine check 9: autonomy 3 and 4 (handoff for ATL-213)

Decision #254 (1). This task only changes the **decision** the Risk Engine writes. Nothing acts on
`send_now` yet: ATL-213 makes `apply_risk_result` send it through the signed
`POST /internal/proposals/{id}/execute` with `approved_by = "autonomy:<level>"`.

## Settings

| key | type | default | where |
| --- | --- | --- | --- |
| `autonomy_level` | int 1–4 | 2 | `autonomy.default_level` |
| `autonomy_ask_above_usd` | decimal string, ≥ 0 | `"1000"` | `autonomy.ask_above_usd` in `config/defaults.yaml` |

`autonomy.max_enabled_level` is now **4**, so `PATCH /api/settings` accepts 3 and 4 (5 is still a
422). `GET /api/settings` keeps returning `data.max_enabled_autonomy_level` (now 4).
`autonomy_ask_above_usd` is versioned and diffed in `settings_history` like every other key;
-1, a JSON number, `"abc"`, `""` and `null` are 422. In Python it is
`EngineSettings.autonomy_ask_above_usd` (Decimal; a negative value read from an old row is
treated as 0, i.e. level 3 asks for everything — never fails open).

## `decide_autonomy` (trading_core/risk/rules.py)

```python
decide_autonomy(level, max_enabled_level, *, notional_usd: Decimal | None,
                ask_above_usd: Decimal, risk_reducing: bool = False, halted: bool = False)
```

Order of evaluation, first match wins:

| condition | decision | `values.note` |
| --- | --- | --- |
| level ≤ 1 | `advise_only` | — |
| level 2 | `awaiting_owner` | — |
| level > max_enabled_level | `awaiting_owner` | `level_not_enabled` |
| `halted` (paused or any breaker) | `awaiting_owner` | `halted` |
| level 4 | `send_now` | — |
| level 3, `risk_reducing` (close / partial_close) | `send_now` | `risk_reducing` |
| level 3, notional unknown | `awaiting_owner` | `notional_unknown` |
| level 3, notional ≤ ask_above (equality sends) | `send_now` | — |
| level 3, notional > ask_above | `awaiting_owner` | `above_ask_threshold` |

The check always passes (it never rejects). Its stored `values` are
`{autonomy_level, max_enabled_level, notional_usd, ask_above_usd[, note]}` — Decimals are stored
as strings in `risk_checks.checks[8].values` (entries) / the `autonomy` check (position actions).

### Entries (engine.py, check 9)

`notional_usd = sizing.final_notional_usd` — the final notional after every cap in check 4. Paused
trading and breakers reject at **check 1** (`trading_paused` / `circuit_breaker_active`) at every
level, so check 9 is `not_run` and the decision is null; an entry never reaches `halted`.

### Position actions (position_actions.py, check 7)

- `close`, `partial_close`: `risk_reducing=True` → level 3 sends at any size. `notional_usd` is the
  close notional from `close_size`.
- `amend_stop`, `amend_targets`: `notional_usd` = the **whole position's** notional
  (contracts × ctVal × ctMult × mark, or contracts × ctVal × ctMult for inverse); level 3 follows
  the threshold.
- `halted = paused or active_breakers`. Check 1 (`env_state`) still lets closes and amend_stop pass
  while halted (kill-switch protection), but check 7 then gives `awaiting_owner` with note
  `halted` at levels 3–4, so autonomy never acts past a pause or breaker; the owner can still
  approve. `amend_targets` while halted is still rejected at check 1.

## For ATL-213

- Act only on `risk_checks.decision == "send_now"`. The Executor's existing guard
  (`decision == send_now`, or `awaiting_owner` with `approved_by == "owner"`) is unchanged.
- `orchestrator/cycle.py` and `orchestrator/approval.py` docstrings still say send_now is treated
  as awaiting_owner while 3–4 are not enabled; that behaviour is yours to replace.
- The "level 4 refused in live for the first month" rule is Patch O, not here.
