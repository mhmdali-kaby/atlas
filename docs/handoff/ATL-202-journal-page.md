# ATL-202 — Journal page

The fifth shell area, **Journal** (`/journal`, icon `notebook-pen`), between Portfolio and Settings.
`AreaId` in `frontend/src/app/feature.ts` now includes `'journal'`. Code: `frontend/src/features/journal/`.
Reads ATL-200's `GET /api/journal` and `GET /api/journal/{id}` only.

## URL (the contract for anything that links here)

`/journal?inst=&agent=&tag=&outcome=&range=&from=&to=&entry=`

| key | values |
| --- | --- |
| `inst` | exact instId (upper-cased on read) |
| `agent` | agent slug; matches ATL-194 `data.agents` |
| `tag` | full tag as stored, e.g. `setup:breakout`, `asset:crypto`, `regime:trend`, `catalyst:earnings` |
| `outcome` | `win` \| `loss` \| `scratch` |
| `range` | `7d` \| `30d` \| `custom` (custom needs `from` and `to`, Baghdad `YYYY-MM-DD`, from ≤ to) |
| `entry` | a `journal_entries.id`; opens that post-mortem. Another env's id shows "isn't in the demo journal". |

The War Room post-mortem card's **Open in the Journal** (ATL-203) should link to `/journal?entry=<journal_id>`.
A filter change replaces the history entry; opening an entry on a window under 900px pushes one.
With no `entry` on a wide window, the newest entry is shown without writing it to the URL.

## What it derives (model.ts)

- `closeLabel(entry)` maps `data.actual.close_reason` to one label key (`journal:rule.*`):
  `stop_loss → stop`, `take_profit → tp1|tp2|tp3` (the furthest target `mfe_r` reached; `tp` if none),
  `time_stop`, `invalidation`, `manual → owner`, `kill`, `liquidation`, `adl`, `external`, any other PM rule → `pm`.
  ATL-203 should reuse it (and the `journal:rule.*` strings) so the label reads the same in both places.
- `outcomeOf`, `realisedR`, `tp1Hit`, `miniGeometry` (the row's mini ladder), `displayTags`, `voteRight`, `weekEnd`
  (review weeks end Sunday 18:00 Baghdad).

## Deliberately not built

- **Weekly review rows** in the list (design §7) and the review reading pane: they need
  `GET /api/journal/weekly-reviews` plus the weekly-review renderer ATL-203 builds for the War Room card.
  The week group headers are there; a review row can be inserted at the top of each finished week's group.
- **The full ladder with `travel`** (design §8) in "How the trade went": the entry draws the mini ladder
  at full width with the same band and diamond. Once ATL-203 adds `travel` to `warroom/cards/Ladder.tsx`,
  swap it into `Shape` in `JournalEntryView.tsx`.
- The pending-lesson block cannot name the retry time ("tries again at 17:28"): ATL-194 stores no retry
  time, so it says Atlas keeps trying.
