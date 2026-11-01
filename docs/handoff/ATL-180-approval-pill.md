# ATL-180: the approval pill and the "trade waiting" toast

## Where it lives

- `frontend/src/features/approvals/`
  - `ApprovalPill.tsx`: rendered by `AppShell` in the `toolbar-actions` slot, before `KillSwitchButton`,
    on every signed-in page. Draws nothing when no proposal is open for approval.
  - `api.ts`: `useAwaitingProposals(env)` (query key `['proposals','awaiting',env]`,
    `GET /api/proposals?env=demo&status=awaiting_owner&limit=50`, polled every 15 s),
    `useProposalEvents(env, onAwaiting)` (private-warroom `.proposal.*`), `awaitingFromEvent`,
    `openForApproval`, `nearest`, `proposalHref(id)` = `/warroom?proposal=<id>`. The countdown text uses the shared `mmss` from `warroom/time.ts`.
  - `approvals.css`: `.ks-pill` from design v11 `css/killswitch.css`.
  - Strings: `src/i18n/locales/{en,ar}/approvals.json` (namespace `approvals`, Arabic plurals).
- `frontend/src/features/warroom/useProposalFocus.ts`: the War Room side of `?proposal=<id>`. `Feed`
  finds the newest message whose proposal card shows that id (or an earlier version in its chain,
  read from `GET /api/proposals/{id}` `versions`, because an owner's edit has no message), scrolls
  to it, flashes it and focuses it. Once per navigation, so a second click on the pill jumps again.
- `useProposal(id, enabled = true)` gained the optional `enabled` flag.

## Behaviour

- Pill: "N to approve" + `m:ss` to the nearest `approval_expires_at`, ticking every second. Under
  60 s it takes `.is-warn` (orange) and the label becomes "Last minute: N to approve" (text, not only
  colour). At ≤ 760 px only the icon and the time show; the aria-label always has the whole sentence.
- Toast (`tone: info`) and, only if `Notification.permission === 'granted'`, a browser
  notification. Nothing ever calls `requestPermission`. Triggered by:
  - the `proposal.awaiting_owner` event (ATL-160 payload: `decision`, `inst_id`, `side`,
    `approval_expires_at`) at once;
  - a proposal id the list refetch finds that was not there before. What is waiting when the app
    opens is not toasted (the pill shows it), and an `author: owner` edit is never toasted.
  - Each id toasts once per page load.
- Every proposal event (approved, rejected, revised, advise_only, …) refetches the list, so the pill
  leaves as soon as the proposal stops waiting.

## Not built / differs from the design

- The toast lasts the shared 4.2 s (`ui/Toast.tsx` has no per-toast duration); the design's prototype
  used 7 s.
- The warning label "Last minute: …" is not in the design, which changed colour only; criterion 2
  asks for text as well.
