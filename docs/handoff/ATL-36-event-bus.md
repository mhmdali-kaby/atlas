# ATL-36 — War Room tables and the Redis Streams event bus

For ATL-37 (bridge + War Room API) and ATL-38 (War Room UI), and anyone publishing events later.

## The two-minute version

- Publish from PHP with `App\Support\Events\EventBus` (it already existed for `system.settings_changed`;
  ATL-36 added stream trimming). From Python, `atlas_shared.bus.publish(stream, envelope)`.
- One stream entry = one field, `envelope`, holding the JSON envelope. Nothing else in the entry.
- Persist a `warroom.message` event by inserting a `warroom_messages` row with `event_id = envelope.id`.
  The column is unique, so a replayed/duplicate event fails with `UniqueConstraintViolationException`
  (catch it, or use `insertOrIgnore`/`upsert` on `event_id`) — that is the "at most once" guarantee.
- Render every message with `App\Http\Resources\WarroomMessageResource`, in the API and in the Reverb
  broadcast, so the SPA sees one shape.
- `mentions` is Postgres `text[]`, cast to `list<string>` by `App\Casts\PostgresTextArray`.
- Both models store microsecond timestamps (`$dateFormat = 'Y-m-d H:i:s.uP'`). Cursor-page on
  `(created_at, id)`; the index `(thread_id, created_at, id)` exists for exactly that.

## Tables

`warroom_threads`

| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| env | enum demo\|live | |
| kind | enum main\|instrument\|agent | |
| key | varchar(64) | `main` for the main thread; an instId (`BTC-USDT-SWAP`) for instrument; an agent key (`historian`) for agent |
| title | varchar(255) | seeded main threads are titled `Main` (display copy lives in the SPA's i18n) |
| created_at, updated_at | timestamptz(6) | |

Unique `(env, kind, key)`. `Database\Seeders\WarroomThreadSeeder` (called by `DatabaseSeeder` and
`E2eSeeder`) creates exactly one `main` thread per env, idempotently (`firstOrCreate`). Look it up with
`WarroomThread::query()->where(['env' => $env, 'kind' => 'main', 'key' => WarroomThread::MAIN_KEY])`.

`warroom_messages`

| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| env | enum demo\|live | |
| thread_id | uuid FK → warroom_threads, cascade | |
| event_id | uuid NULL, **unique** | envelope id it came from; null for rows written directly |
| author_type | enum agent\|human\|system | |
| author | varchar(64) | agent key (`historian`), `owner`, or `system` |
| kind | varchar(32) | see `WarroomMessage::KINDS` (brief §12: text, watchlist, research_report, news_alert, proposal, vote_summary, risk_check, order, fill, position_update, post_mortem, system_alert). Not an enum on purpose, so new card kinds need no migration |
| content_md | text | |
| card | jsonb NULL | |
| reply_to | uuid NULL FK → warroom_messages, set null on delete | |
| mentions | text[] NOT NULL default `{}` | |
| reasoning_md | text NULL | |
| tool_calls | jsonb NULL | list of objects |
| tokens_in, tokens_out | integer NULL | |
| cost_usd | numeric(18,6) NULL | cast `decimal:6` → string |
| created_at, updated_at | timestamptz(6) | |

Index `(thread_id, created_at, id)`.

No SQLAlchemy model for these tables yet; add one to `atlas_shared.db.models` when a Python service
first reads them (the schema-reflection test will then guard it).

## Streams

The seven from brief §3.2, nothing else: `atlas.warroom`, `atlas.market`, `atlas.account`,
`atlas.proposals`, `atlas.risk`, `atlas.orders`, `atlas.system`. Both publishers refuse any other name
before touching Redis (PHP `InvalidArgumentException("Unknown event stream [atlas.nope].")`, Python
`atlas_shared.bus.UnknownStreamError`, a `ValueError`).

Redis: the Laravel `events` connection (`config/database.php`), **no key prefix**, so stream keys are the
literal names above. Python `atlas_shared.bus.make_client()` reads the same `REDIS_HOST`, `REDIS_PORT`
(default 6380), `REDIS_DB`, `REDIS_USERNAME`, `REDIS_PASSWORD` (`null` means none), with
`decode_responses=True`.

Entry layout, exactly:

```
XADD atlas.warroom MAXLEN ~ 100000 * envelope '<json>'
```

`MAXLEN` comes from `config/defaults.yaml` → `event_bus.stream_maxlen` (generated into
`config('atlas_defaults.event_bus.stream_maxlen')` and `atlas_shared.defaults.DEFAULTS`). The bus is a
transport, not storage: the bridge must persist to Postgres; trimmed entries are gone.

## Envelope

```json
{
  "id": "0b8f3c2e-6a51-4f0e-9a43-1c2d7e5b9f10",
  "type": "warroom.message",
  "ts": "2026-10-29T05:00:00.123Z",
  "env": "demo",
  "source": "human:owner",
  "correlation_id": null,
  "payload": { }
}
```

Keys always in that order and all present (`correlation_id` may be null). PHP writes `ts` with
milliseconds and `Z`; Python (pydantic) writes microseconds and `Z`. Parse either as ISO-8601.

PHP signature (env and source are required, unlike the task text's shorter signature, because the
envelope needs them):

```php
app(EventBus::class)->publish(
    stream: 'atlas.warroom',
    type: 'warroom.message',
    env: 'demo',
    source: 'human:owner',
    payload: [...],
    correlationId: null,
); // returns the envelope array it published
```

Python:

```python
from atlas_shared import bus
from atlas_shared.events import EventEnvelope

bus.publish("atlas.warroom", EventEnvelope(type="warroom.message", env="demo",
            source="agent:historian", payload={...}))  # returns the stream entry id
```

`source` convention: `agent:<agent_key>`, `human:owner`, `system:<component>` (e.g. `system:bridge`).

## `warroom.message` payload (the contract ATL-37 consumes)

The payload is the message to persist, in the column names, minus what the bridge assigns
(`id`, `created_at`, `updated_at`; `event_id` = envelope `id`; `env` = envelope `env`):

```json
{
  "thread": { "kind": "main", "key": "main" },
  "author_type": "agent",
  "author": "historian",
  "kind": "research_report",
  "content_md": "**Verdict:** weakly supports",
  "card": { "verdict": "weakly_supports", "n": 14 },
  "reply_to": null,
  "mentions": ["market_scout"],
  "reasoning_md": null,
  "tool_calls": [{ "tool": "history.compute_stats", "input": { "inst_id": "BTC-USDT-SWAP" } }],
  "tokens_in": 1200,
  "tokens_out": 340,
  "cost_usd": "0.0123"
}
```

- `thread` is `{kind, key}` rather than a uuid so a Python publisher need not look thread ids up; the
  bridge resolves it with `firstOrCreate` (title: the key, or `Main`). A `thread_id` (uuid) may be sent
  instead when the publisher knows it.
- Optional keys may be omitted: `card`, `reply_to`, `reasoning_md`, `tool_calls`, `tokens_*`,
  `cost_usd` → null; `mentions` → `[]`.
- `cost_usd` is a decimal string, never a float.
- ATL-37 tightened the validation rules for this shape: see `docs/handoff/ATL-37-bridge.md`.

## `WarroomMessageResource` JSON

```json
{
  "id": "9a1f…",
  "thread_id": "3c7e…",
  "author_type": "agent",
  "author": "historian",
  "kind": "research_report",
  "content_md": "**Verdict:** weakly supports",
  "card": { "n": 14, "verdict": "weakly_supports" },
  "reply_to": "51d0…",
  "mentions": ["market_scout"],
  "reasoning_md": "n < 20",
  "tool_calls": [{ "tool": "history.compute_stats", "input": { "inst_id": "BTC-USDT-SWAP" } }],
  "tokens_in": 1200,
  "tokens_out": 340,
  "cost_usd": "0.012300",
  "created_at": "2026-10-29T05:00:00.123456Z"
}
```

`card` keys come back in jsonb order (not insertion order). `env` and `event_id` are not in the resource.

## Not built here

The bridge (`atlas:bridge`), Reverb channels, the War Room API and its routes, thread titles for
instrument/agent threads, and any consumer group naming — all ATL-37.
