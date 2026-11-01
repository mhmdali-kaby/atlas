# ATL-164 — War Room proposal card (Approve, Reject, Edit, Ask the team)

For ATL-180 (approval pill + toast) and anyone drawing a proposal elsewhere.

## Two-minute version

- `atlas:bridge` now broadcasts **every `atlas.proposals` event on `private-warroom`**, named by its
  envelope type (`.proposal.approved`, `.proposal.rejected`, `.proposal.revised`, …), with the full
  envelope as the payload. `useLiveFeed` listens to them and invalidates `['proposals', <id>]` for
  `payload.proposal_id` and `payload.parent_id`. If the pill needs a list, invalidate your list key
  in the same handler (`PROPOSAL_EVENTS` in `useLiveFeed.ts`).
- The card is registered in `Message.tsx` under kind `proposal`. It reads the posted card
  (`readProposalCard`) for the id and a snapshot, then `GET /api/proposals/{id}` via `useProposal(id)`
  (query key `proposalKey(id)` = `['proposals', id]`). Buttons appear only from the API's data,
  never from the snapshot.
- **An owner's edit has no War Room message.** The card for version N follows `versions` to the
  newest version authored `owner` and renders that one ("Version 3 is your edit"). A revision by an
  agent has its own message, so the older card shows "Replaced".
- Status shown (`shownStatus`): `awaiting_owner` past `approval_expires_at` shows `expired` at once
  (and refetches); `advise_only` → `advice` (badge, Ask the team only, no Approve);
  `rejected/approval_timeout` → `expired`; `cancelled/revised` → `replaced`; other `cancelled` →
  `withdrawn`.

## Files

| File | What |
| --- | --- |
| `frontend/src/features/warroom/cards/proposal.ts` | Types for the ATL-162 resource, `readProposalCard`, `useProposal`, `proposalApi.{approve,reject,edit,askTeam}`, `invalidateProposal`, `refusalOf(err)` (409 reason code + server words), `stakeOf` (risk at stop, % of sizing equity, margin, per-trade cap from check 4 values), `sizePctOf` (USDT → % for PATCH `size`) |
| `frontend/src/features/warroom/cards/ProposalCard.tsx` | The card, the deadline band (`role="timer"`), the outcome line, the DA block |
| `frontend/src/features/warroom/cards/ProposalSheets.tsx` | `ReasonSheet` (reject / ask), `EditSheet`, `editErrors` (stops only tighten, sides, order, shares = 100, per-trade cap) |
| `frontend/src/features/warroom/cards/dec.ts` | BigInt decimal maths for money and stop comparisons |
| `frontend/src/features/warroom/fixtures/proposal.ts` | SOL design-day message + resource (`proposalDetail`, `sentDetail`) |
| `frontend/src/i18n/locales/{en,ar}/proposal.json` | Namespace `proposal` (own file, so warroom.json is untouched) |

Test ids: `pr-card` (`data-status`, `data-proposal-id`), `pr-status`, `pr-countdown`, `pr-band`,
`pr-approve`, `pr-reject`, `pr-edit`, `pr-ask`, `pr-actions`, `pr-outcome`, `pr-conflict`,
`pr-reject-submit`, `pr-reject-text`, `pr-edit-save`.

## Not built / differs from the design

- No human proposal id ("P-0412"): the API has none, so the title is "Proposal, version N".
- No "Main risk" row and no evidence chips: the resource has no main-risk field, and evidence ids
  are research_report ids, not War Room message ids. The DA's strongest objection is shown.
- Ask the team opens a question sheet that calls `POST /ask-team` (the design pre-fills the
  composer, which would not link the question to the proposal).
- The Edit sheet's size is in USDT as designed and is converted to % of sizing equity for PATCH.
- `wasStop` on the ladder is still not built; the edit preview shows the new stop only.
