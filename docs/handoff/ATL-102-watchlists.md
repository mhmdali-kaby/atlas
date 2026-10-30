# ATL-102 — Watchlists: tables, `save_watchlist`, `atlas_data.get_watchlist`

For ATL-103 (owner pin/remove API), ATL-108 (Market Scout publishes), ATL-109 (intraday re-runs),
ATL-110 (scores), ATL-112 (Scout evals) and ATL-115 (War Room card).

## The two-minute version

- A watchlist belongs to **an env and an Asia/Baghdad business day** (`watchlists.date`, a Postgres
  `date`). Compute the day with `atlas_agents.watchlists.baghdad_today(instant)`; never take
  `.date()` of a UTC timestamp — at 01:30 Baghdad the UTC date is still yesterday.
- **Every save is a new version.** A re-run (intraday alert, `/cycle now`) calls `save_watchlist`
  again for the same (env, date) and gets `version = max + 1`. The current watchlist is the
  **highest version**; there is no "superseded" status and nothing is updated in place.
  Unique key: `(env, date, version)`.
- `save_watchlist` validates **before** writing: one bad pick (e.g. conviction 1.2) raises and
  nothing is written. Both tables are written in one savepoint. It **flushes but does not commit**
  (same convention as `atlas_shared.db.add_usage`): the caller commits.
- `pinned_by_owner` is always written `false` by `save_watchlist`; owner pins/removals are ATL-103's
  (Laravel). Carrying the owner's pins into a re-run's new version is ATL-108's job (read the
  previous version's pinned items and include them in the picks), not done here.
- The partial-evidence conviction cap (≤ 0.5) is **not** enforced in `save_watchlist`; ATL-108
  applies it before saving.
- `message_id` has no FK to `warroom_messages`: the bridge persists the card asynchronously, maybe
  after this row exists.

## Tables (migration `2026_10_30_100000_create_watchlist_tables.php`)

`watchlists`

| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| env | enum demo\|live | |
| date | **date** | Asia/Baghdad business day |
| version | integer | CHECK ≥ 1; unique (env, date, version) |
| created_by | varchar(64) | agent slug (`market_scout`) or `owner` |
| status | enum draft\|published\|partial_evidence\|empty | |
| edit_window_ends_at | timestamptz(6) null | end of the owner's pin/remove window |
| reason | text null | "no watchlist today" reason / what evidence was missing |
| message_id | uuid null | warroom_messages id of the watchlist card (no FK) |
| data | jsonb NOT NULL default `{}` | backdrop note, rejected candidates, anything else on the card |
| created_at, updated_at | timestamptz(6) | `created_at` is what replay reads are bounded on |

Index: (env, date, created_at).

`watchlist_items`

| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| watchlist_id | uuid FK → watchlists, ON DELETE CASCADE | |
| inst_id | varchar(64) | unique per watchlist |
| rank | integer | CHECK ≥ 1; unique per watchlist; 1 = strongest |
| asset_class | varchar(32) | |
| bias | enum long\|short\|neutral | |
| conviction | numeric(6,4) | CHECK 0..1 |
| thesis | text | |
| catalysts | jsonb NOT NULL default `[]` | list of Catalyst dicts (below) |
| key_levels | jsonb NOT NULL default `{}` | `{"support": 100.5, ...}` |
| invalidation | text | |
| pinned_by_owner | boolean NOT NULL default false | set by ATL-103 |
| removed_by_owner_at | timestamptz(6) null | set by ATL-103; hides the item from get_watchlist |
| data_as_of | timestamptz(6) | |
| created_at, updated_at | timestamptz(6) | |

SQLAlchemy: `atlas_shared.db.Watchlist`, `atlas_shared.db.WatchlistItem` (the schema-drift test
covers both).

## Pydantic schemas (`atlas_agents.schemas`)

```python
class Catalyst(_Model):            # extra="forbid", frozen
    name: str                      # 1..200 chars, e.g. "US CPI"
    time: AwareDatetime | None     # when it happens; null if unscheduled
    source: str                    # e.g. "forexfactory" or a URL
    importance: int                # 1..5

class WatchlistPick(_Model):       # brief §6.3
    inst_id: str; rank: int (>=1); asset_class: str
    bias: Literal["long", "short", "neutral"]
    conviction: float (0..1)
    thesis: str
    catalysts: list[Catalyst] = []
    key_levels: dict[str, float] = {}
    invalidation: str
    data_as_of: AwareDatetime
```

`WatchlistPick` is **not** registered in `CARD_TYPES`; the watchlist card shape (picks + backdrop +
rejected candidates) is ATL-108's to define.

## `save_watchlist` (`atlas_agents.watchlists`)

```python
def save_watchlist(
    session: Session,
    env: str,                       # "demo" | "live"
    date: date,                     # Baghdad day; a datetime is refused
    picks: Sequence[WatchlistPick | Mapping[str, Any]],
    status: Literal["draft", "published", "partial_evidence", "empty"],
    *,
    created_by: str = "market_scout",
    reason: str | None = None,
    edit_window_ends_at: datetime | None = None,
    message_id: uuid.UUID | None = None,
    data: Mapping[str, Any] | None = None,
    created_at: datetime | None = None,   # default now(UTC); evals seeding a past day pass it
) -> Watchlist
```

Raises, writing nothing:

- `pydantic.ValidationError` — a pick fails `WatchlistPick` (conviction 1.2, an unknown bias, a naive data_as_of…)
- `WatchlistError` (a `ValueError`) — env not demo/live; `date` is a datetime; unknown status;
  duplicate rank or inst_id; `empty` with picks; any other status with no picks.

Example:

```python
from atlas_agents.watchlists import baghdad_today, save_watchlist

with session_factory() as s:
    wl = save_watchlist(
        s, "demo", baghdad_today(now), picks, "published",
        edit_window_ends_at=now + timedelta(minutes=10),
        data={"backdrop": "...", "rejected": [...]},
    )
    s.commit()
# wl.id, wl.version
```

## `atlas_data.get_watchlist`

Input: `{"date": "2026-10-31"}` or `{}` (default: `baghdad_today(ctx.now())`; in replay that is the
Baghdad day of `replay_as_of`).

Reads the highest version for (ctx.env, date) with `created_at <= replay_as_of` in replay. Items
with `removed_by_owner_at` set are left out (in replay, only if removed at or before the cutoff).
`pinned_by_owner` is the current value — it has no timestamp, so replay cannot bound it.

Found:

```json
{
  "as_of": "2026-10-31T05:00:00+00:00",
  "source": "postgres:watchlists,watchlist_items",
  "data": {
    "date": "2026-10-31",
    "available": true,
    "watchlist_id": "6f1c…",
    "version": 1,
    "status": "published",
    "created_by": "market_scout",
    "created_at": "2026-10-31T05:00:00+00:00",
    "edit_window_ends_at": "2026-10-31T05:10:00+00:00",
    "message_id": "1b2e…",
    "data": {"backdrop": "BTC trending up"},
    "items": [
      {
        "inst_id": "BTC-USDT-SWAP", "rank": 1, "asset_class": "crypto", "bias": "long",
        "conviction": 0.71,
        "thesis": "BTC-USDT-SWAP broke its 20-day high on rising OI.",
        "catalysts": [{"name": "US CPI", "time": "2026-10-31T12:30:00Z", "source": "forexfactory", "importance": 5}],
        "key_levels": {"support": 100.5, "resistance": 120.25, "invalidation": 98.0},
        "invalidation": "Daily close back below 98.0",
        "pinned_by_owner": false,
        "data_as_of": "2026-10-31T04:50:00+00:00"
      }
    ],
    "order": "rank asc",
    "reason": null
  }
}
```

`as_of` is the watchlist's `created_at`. An `empty` watchlist comes back `available: true`,
`items: []`, with its `reason`.

Not found: `available: false`, `items: []`, every watchlist field null, `reason: "Market Scout has
not saved a watchlist for 2026-10-31 (Asia/Baghdad)."`, `as_of` = `ctx.now()`, source
`postgres:watchlists`.

Tests: `services/agents/tests/test_watchlists.py`, `backend/tests/Feature/Schema/WatchlistSchemaTest.php`.
