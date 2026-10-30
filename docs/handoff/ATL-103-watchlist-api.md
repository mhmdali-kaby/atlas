# ATL-103 — Watchlist API: view, remove, restore, pin

For ATL-115 (War Room watchlist card), ATL-108 (Market Scout publishes and carries pins into
re-runs) and anyone consuming `watchlist.updated`.

## The two-minute version

- `GET /api/watchlists` returns the **highest version** for (env, Asia/Baghdad date). No row →
  `200` with `"watchlist": null`, never 404.
- Edits apply to **that version only**. An older version → `409 not_current_version`. After
  `edit_window_ends_at` → `409 edit_window_closed`. Both change nothing.
- `edit_window_ends_at` is the stored column; when it is null Laravel uses
  `created_at + agents.scout.watchlist_edit_window_minutes` (config/defaults.yaml, **10**).
  **ATL-108: stamp `edit_window_ends_at = created_at + that key`** when saving.
- Remove sets `watchlist_items.removed_by_owner_at`; restore clears it. Ranks are never given away
  (a pin takes `max(rank)+1` over every row, removed or not), so restore puts the item back at the
  same rank. Removed items come back in `removed_items` so the card can offer Undo.
- Pin of an instrument already on the list sets `pinned_by_owner = true` (and un-removes it). A new
  pin is a row with `bias: neutral`, `conviction: "0.0000"`, thesis `"Pinned by the owner."`,
  empty catalysts/key_levels/invalidation — **placeholders for ATL-108's re-run to analyse**.
  Pinning into an `empty` watchlist flips its status to `published`.
- Blocklist = settings row `data.blocklist` (list of instIds) when present, else
  `agents.scout.universe.blocklist` — the same rule as Scout's universe filter
  (`atlas_agents.scout.universe.config_from`). Case-insensitive. `App\Support\Watchlists\Blocklist`.
- Every write: one `audit_log` row (`watchlist.item_removed|item_restored|item_pinned`,
  subject `watchlist_items`/<item id>, before/after = the item shape) and one `watchlist.updated`
  envelope on `atlas.warroom`, both inside the DB transaction. The bridge broadcasts it on
  `private-warroom` as event `watchlist.updated` (it is not persisted as a message).

## Auth

All routes: `auth:sanctum` (SPA session cookie). No session → `401 {"message":"Unauthenticated."}`.
Policy `App\Policies\WatchlistPolicy` (`view`, `update`: any signed-in user — single owner).
Writes are throttled by `watchlists-write` (`watchlists_api.writes_per_minute`, 30/min/user).

## GET /api/watchlists?date=YYYY-MM-DD&env=demo|live

Both optional. `date` default: today in Asia/Baghdad. `env` default: `demo`. Bad date → 422 on `date`.

```json
{
  "env": "demo",
  "date": "2026-10-31",
  "timezone": "Asia/Baghdad",
  "watchlist": {
    "id": "9d4b0c1e-2f7a-4a55-8a0e-0c6c1d2b7e11",
    "env": "demo",
    "date": "2026-10-31",
    "version": 2,
    "status": "published",
    "created_by": "market_scout",
    "created_at": "2026-10-31T05:00:00.000000Z",
    "edit_window_ends_at": "2026-10-31T05:10:00.000000Z",
    "edit_window_open": true,
    "reason": null,
    "message_id": "1b2e6c3a-8f1d-4f7e-9a0b-2c3d4e5f6a7b",
    "data": {"backdrop": "BTC trending up"},
    "items": [
      {
        "id": "4f0a8f8e-7f0e-4d0d-9b4f-5a1d7a3d9c21",
        "inst_id": "BTC-USDT-SWAP",
        "rank": 1,
        "asset_class": "crypto",
        "bias": "long",
        "conviction": "0.7100",
        "thesis": "BTC-USDT-SWAP broke its 20-day high on rising OI.",
        "catalysts": [{"name": "US CPI", "time": "2026-10-31T12:30:00Z", "source": "forexfactory", "importance": 5}],
        "key_levels": {"support": 100.5, "resistance": 120.25, "invalidation": 98.0},
        "invalidation": "Daily close back below 98.0",
        "pinned_by_owner": false,
        "removed_by_owner_at": null,
        "data_as_of": "2026-10-31T04:50:00.000000Z"
      }
    ],
    "removed_items": [
      { "...same item shape...": "", "rank": 2, "removed_by_owner_at": "2026-10-31T05:03:12.418000Z" }
    ]
  }
}
```

`items` and `removed_items` are each in rank order. `conviction` is a decimal **string** (4 dp).
`key_levels` and `data` are always JSON objects (`{}` when empty). Times are UTC with microseconds.

No watchlist that day:

```json
{"env": "demo", "date": "2026-10-02", "timezone": "Asia/Baghdad", "watchlist": null}
```

## POST /api/watchlists/{watchlist}/items/{item}/remove
## POST /api/watchlists/{watchlist}/items/{item}/restore

No body. `{watchlist}`, `{item}` are UUIDs; an item of another watchlist → 404 (scoped binding).

## POST /api/watchlists/{watchlist}/pin

```json
{"inst_id": "SOL-USDT-SWAP"}
```

`inst_id` is upper-cased. It must exist in `instruments` **for the watchlist's env**, have
`state = "live"`, and not be on the blocklist.

## Write responses

200, all three:

```json
{
  "watchlist": { "...full watchlist shape as in GET, after the change...": "" },
  "item": {
    "id": "7c2…", "inst_id": "SOL-USDT-SWAP", "rank": 3, "asset_class": "crypto",
    "bias": "neutral", "conviction": "0.0000", "thesis": "Pinned by the owner.",
    "catalysts": [], "key_levels": {}, "invalidation": "",
    "pinned_by_owner": true, "removed_by_owner_at": null,
    "data_as_of": "2026-10-31T05:04:00.000000Z"
  }
}
```

Errors:

| status | body | when |
| --- | --- | --- |
| 401 | `{"message":"Unauthenticated."}` | no session |
| 404 | Laravel not-found | unknown watchlist, or item not on it |
| 409 | `{"ok":false,"error":"edit_window_closed","message":"The edit window for this watchlist has closed.","edit_window_ends_at":"…Z"}` | now ≥ edit_window_ends_at (checked first) |
| 409 | `{"ok":false,"error":"not_current_version",…}` | a newer version exists for that day |
| 409 | `{"ok":false,"error":"item_already_removed",…}` | remove of a removed item |
| 409 | `{"ok":false,"error":"item_not_removed",…}` | restore of a visible item |
| 422 | `{"message":"…","errors":{"inst_id":["No instrument with this ID exists in the demo environment."]}}` | pin: unknown / other env |
| 422 | `errors.inst_id: ["This instrument is not live (state: suspend)."]` | pin: state ≠ live |
| 422 | `errors.inst_id: ["This instrument is on the blocklist in Settings."]` | pin: blocklisted |
| 429 | throttle | > 30 writes/min |

Validation (422) runs before the window check, so an invalid pin after the window is 422.
Messages live in `backend/lang/en/watchlists.php`.

## Event: `watchlist.updated` on `atlas.warroom`

```json
{
  "id": "uuid", "type": "watchlist.updated", "ts": "2026-10-31T05:03:12.418Z",
  "env": "demo", "source": "human:owner", "correlation_id": null,
  "payload": {
    "action": "removed",
    "watchlist_id": "9d4b…", "date": "2026-10-31", "version": 2, "status": "published",
    "edit_window_ends_at": "2026-10-31T05:10:00.000000Z",
    "item": { "...item shape after the change...": "" },
    "items": [ "...visible items in rank order, item shape..." ]
  }
}
```

`action` ∈ `removed | restored | pinned`.

## Not built here

- Carrying pins into a Scout re-run (ATL-108): read the previous version's `pinned_by_owner` items.
- The Settings → Markets blocklist editor; until it exists the list comes from config.
- Any frontend (ATL-115).

Tests: `backend/tests/Feature/Watchlists/WatchlistApiTest.php`.
