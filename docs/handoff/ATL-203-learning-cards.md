# ATL-203 — War Room post-mortem and weekly review cards

Code: `frontend/src/features/warroom/cards/` — `learning.ts` (readers and rules), `PostMortemCard.tsx`,
`WeeklyReviewCard.tsx`, `learning.css`; wired in `warroom/Message.tsx`. Strings: `warroom.json` → `pmo.*`
and `wk.*` (en/ar). Fixtures: `warroom/fixtures/learning.ts` (also used by `e2e/warroom-learning-cards.spec.ts`).

## Two-minute version

- `kind: "post_mortem"` → `readPostMortem(card)` → `<PostMortemCard pm lang />`. A `lesson_update: true`
  card renders the same full card with a "Lesson added" note in its head (it carries the whole row).
- `kind: "research_report"` with `card.type` (or `card.kind`) `"weekly_review"` → `readWeeklyReview(card)` →
  `<WeeklyReviewCard review lang />`. It is checked first, before the other research_report cards.
  `no_trades` (or `trade_count` 0) draws the code-only quiet block; a non-`ok` status with figures draws the
  table and "The Historian couldn't write this week's verdict", with no suggested change.
- **Open in the Journal** links to `/journal?entry=<journal_id>` (ATL-202's URL contract).
- The suggested change links to Settings only when the card has `suggestion.where` = `risk|markets|money`.
  ATL-199 does not send `where` today, so the link is hidden and only the "Agents can't change settings" note shows.

## Ladder `travel` (design §8)

`<Ladder … travel={{ mae, mfe, exit, price }} />`, all R values as decimal strings (exit = realised R) and the
average exit price. Adds the MAE→MFE band (`ld-travel`) under the entry zone, an ink diamond at the exit
(`ld-exit`) and an above-row label "Closed 671.95 −1.0R"; the aria-label gains `ld.ariaTravel`. The R axis
widens to include the travel points. Nothing else on the ladder changes. The Journal's "How the trade went"
can swap its full-width mini ladder for this (ATL-202 handoff).

## Shared with the Journal

ATL-202 was not on develop when this was built, so `learning.ts` mirrors its `closeLabel`, `tp1Hit`,
`displayTags` and the outcome rule, and `pmo.rule.*` / `pmo.tag.*` copy `journal:rule.*` / `journal:tag.*`
word for word. If one set of words changes, change the other, or fold both onto one module once both are merged.
`useTagLabel()` and `SignedR`/`OutcomeLine` are exported for reuse (the Journal's weekly review rows).
