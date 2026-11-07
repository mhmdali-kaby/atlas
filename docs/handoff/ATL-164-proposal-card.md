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
- The liquidation line ("← Liquidation 166.80") is built: `Ladder` takes `liq`, and the card passes
  check 7's `liquidation_buffer.values.estimated_liq_price` (`liqOf` in `cards/proposal.ts`). It shows
  only when the price lies beyond the rail's stop end, as in the design, and only on the full ladder.
- "Now" marker (ATL-188): `/api/proposals` list and show carry `mark` and `mark_at`, read by
  `App\Support\Proposals\MarkPrice` from the md hash `<market_ws.key_prefix>:<env>:<instId>`
  (field `mark` = the OKX mark-price push JSON, `mark-price_ms` = when trading_core received it).
  `GET /api/proposals/{id}/mark` returns `{data: {inst_id, mark, mark_at}}` for polling. While the
  card's status is awaiting_owner / advice / sent / partially_filled, `useProposalMark` refetches
  it every 10 s and on focus; `freshMark` takes the newer mark and shows it only while ≤ 30 s old,
  judged on the card's 1 s `useNow` tick. `Ladder` gained `live` (keeps the Now room:
  `has-now-room`) and `nowOnly` (compact rail: the Now line is its only label). The Edit sheet
  preview gets the same mark.
- Odds sub-line ("Historian analogs 48%, +4 for the ETF catalyst", `pr-odds-basis`): built by ATL-189
  from the optional `probability_basis` {analog_hit_rate 0-1, n, adjustment_pts, reason} that
  submit_proposal validates (|adjustment| <= 15 pts; hit rate + adjustment = probability within 1 pt)
  and /api/proposals returns. No basis, no line. `reason` is model text, rendered as a text node.
  Deferral of these two asked of UX in the room (QA round 1).
