# ATL-149: Stop trading button, Stop sheet, trading banner

For ATL-163 (slash commands `/pause /resume /kill /kill flatten`) and ATL-180 (the approval pill).

## Where it lives

- `frontend/src/features/kill-switch/`
  - `KillSwitch.tsx`: `KillSwitchButton` (the red toolbar button, rendered by `AppShell` inside the
    `data-slot="toolbar-actions"` slot) and `KillSheet` (controlled: `open`, `onOpenChange`, optional
    `trigger`). Every time `open` goes from false to true, with or without a trigger, the sheet starts
    over at the confirmation with close-all off.
  - `HoldButton.tsx` (the reusable 1.5 s press-and-hold), `KillConfirm.tsx` (`ConfirmBody`,
    `ConfirmActions`), `KillReport.tsx` (the per-item report).
  - `TradingBanner.tsx`: the paused/breaker banner, rendered by `AppShell` between the toolbar and
    `<main>`. It draws nothing while trading runs.
  - `api.ts`: `useSystemHealth()` (query key `['system','health']`, polled every 10 s), `useSystemEvents()`
    (refetches health on `.system.trading_state`, `.system.kill`, `.system.circuit_breaker` from
    private-system), `postKill(flatten)`, `postResume()`, `HOLD_MS = 1500`, `KILL_CONFIRM = 'KILL'`.
  - Strings: `src/i18n/locales/{en,ar}/kill-switch.json` (namespace `kill-switch`).
- `src/ui/Sheet.tsx` now pins its actions to the bottom with a hairline while content scrolls under them
  (design §12.2). Every sheet gets this. Its `tone` is now `default | destructive | warning | muted`
  (icon tile red, orange or grey).

## Behaviour (decision 251)

- Stop with close-all off is one press: `POST /api/system/kill {"confirm":"KILL","flatten":false}`.
- With close-all on, the button becomes "Hold to stop and close all": 1.5 s pointer hold, or Space/Enter
  held. Released early, nothing is sent and the hint says so. A click alone never fires it.
- Focus opens on Cancel. Escape closes and focus returns to the toolbar button.
- Before the press, the sheet counts open orders, take-profits (`protects: false`) and stops
  (`protects: true`) from `GET /api/portfolio/orders`, and positions from `/api/portfolio/positions`.
  If those fail it shows the same sentences without numbers.
- The report groups `items` by `action`: cancelled (with a Show all list), kept stops, closed positions,
  failed (OKX code + message), and "Try again", which re-sends the kill with the same `flatten`.
- Any error from the kill (503, 502, network) shows "Nothing was stopped", never a success state.
- The banner leads with the most serious active breaker (loss limits first, `data_stale` last), shows the
  server's `reason` and "Since" time, and "and N more breakers". With only a self-clearing breaker
  (`data_stale`) and trading not paused, it shows no Resume button. Paused by the owner shows the pause time.
  Resume opens a confirmation sheet with a per-breaker warning, then `POST /api/system/resume` and the
  toast "Trading resumed".

## For ATL-163

To open the sheet from `/kill`, render `<KillSheet open onOpenChange={…} />` (no trigger), or lift the
`open` state out of `KillSwitchButton`. `/kill flatten` should still go through the hold, not send
`flatten: true` directly. After any pause/resume/kill, invalidate `['system','health']`.

## For ATL-180

The pill goes in the same `toolbar-actions` slot, before `KillSwitchButton` (design: pill, then Stop).

## Not built

- Fill price and realised P&L per closed position: ATL-147's kill items don't carry them, so the report
  lists the closed instruments only.
- The ⋯ phone menu (design §12.3). At ≤ 480 px the language button drops its label instead, as the
  design's killswitch.css does.
