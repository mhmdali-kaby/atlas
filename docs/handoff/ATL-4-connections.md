# ATL-4 — OKX connections (encrypted, masked)

## The two-minute version

- One row per `(exchange, env)` in `connections`. Phase 0 only accepts `env = demo`; `live` → 422
  "Live connections are enabled in Phase 4".
- `api_key`, `api_secret`, `passphrase` are Laravel `encrypted` casts = `Crypt::encryptString()` with
  `APP_KEY` (AES-256-CBC + MAC, **no PHP serialize**). `atlas_shared.laravel_crypt.decrypt_string(ct, app_key)`
  decrypts them as-is — checked by encrypting in PHP and decrypting in Python.
- The API never returns a secret: only `api_key_last4`, `has_secret`, `has_passphrase`.
- Changing any secret by PATCH **clears** `permissions`, `account_mode`, `position_mode`, `uid_last4`,
  `last_verified_at`, `last_test_error` — they described the old key, so Test connection must run again.
- ATL-8 (Test connection) writes those six columns. Write them with a plain update; do not touch the secrets.

## Table `connections`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid PK | |
| `exchange` | varchar(16), default `okx` | only `okx` accepted |
| `env` | enum `demo` \| `live` | unique with `exchange` |
| `label` | varchar(100) | |
| `api_key` | text | ciphertext |
| `api_secret` | text | ciphertext |
| `passphrase` | text | ciphertext |
| `permissions` | jsonb null | set by ATL-8 (whatever shape it chooses; model casts as `array`) |
| `account_mode` | varchar(32) null | set by ATL-8 |
| `position_mode` | varchar(32) null | set by ATL-8 |
| `uid_last4` | varchar(4) null | set by ATL-8 |
| `last_verified_at` | timestamptz(6) null | set by ATL-8; null = not verified |
| `last_test_error` | text null | set by ATL-8 |
| `created_at`, `updated_at` | timestamptz(6) | not auto-filled by SQLAlchemy |

Unique index: `(exchange, env)`.

### Ciphertext format

Each secret column holds `base64(json)` where json is
`{"iv": base64, "value": base64 AES-256-CBC ciphertext, "mac": hex HMAC-SHA256(key, iv + value), "tag": ""}`.
Key = `APP_KEY` without its `base64:` prefix, base64-decoded (32 bytes). The plaintext is the raw string
(not serialized). Every write produces a different ciphertext, so never `WHERE api_key = …`.

Python (trading_core, decision #235):

```python
from trading_core.okx import credentials_from_ciphertexts, OkxClient
creds = credentials_from_ciphertexts(row.api_key, row.api_secret, row.passphrase, os.environ["APP_KEY"])
```

The column names match what `client_for_connection(row, app_key)` expects (`.env`, `.api_key`,
`.api_secret`, `.passphrase`).

PHP: `$connection->api_key` is already the plaintext (cast). The secrets are `#[Hidden]` on the model,
so `toArray()`/`toJson()` never include them, but don't log the model's attributes either.

## Endpoints

All need the Sanctum session (`auth:sanctum`); unauthenticated → 401. Ids are UUIDs; anything else → 404.

### Resource shape (every response)

```json
{
  "data": {
    "id": "a0c7c3a2-6f4e-4f0b-9a7f-0d6b8c1e2f30",
    "exchange": "okx",
    "env": "demo",
    "label": "OKX Demo",
    "api_key_last4": "ab12",
    "has_secret": true,
    "has_passphrase": true,
    "permissions": null,
    "account_mode": null,
    "position_mode": null,
    "uid_last4": null,
    "last_verified_at": null,
    "last_test_error": null,
    "created_at": "2026-10-28T22:00:00.000000Z",
    "updated_at": "2026-10-28T22:00:00.000000Z"
  }
}
```

### GET /api/connections

200, `{"data": [ <resource>, … ]}` ordered by exchange, env. Empty list when none.

### POST /api/connections

```json
{ "env": "demo", "label": "OKX Demo", "api_key": "…", "api_secret": "…", "passphrase": "…" }
```

`exchange` optional (only `okx`). `label` ≤ 100, `api_key` 4–255, `api_secret`/`passphrase` ≤ 255.
201 + resource. 422 cases (standard Laravel `{message, errors}`):

- `env: live` → `errors.env = ["Live connections are enabled in Phase 4"]`
- a connection already exists for okx + env → `errors.env = ["A connection for this exchange and environment already exists. Edit it instead."]`
  (a concurrent duplicate hitting the DB unique index gets the same 422)
- missing/invalid fields → errors on those fields. Error messages never echo the submitted values.

### PATCH /api/connections/{id}

Any subset of `label`, `api_key`, `api_secret`, `passphrase`. An omitted secret keeps its stored value.
`env` and `exchange` are `prohibited` (422) — delete and re-create instead. 200 + resource. A PATCH that
changes nothing (same label, same secret values) writes no audit row.

### DELETE /api/connections/{id}

204, no body. 404 when it does not exist.

## Audit log

Via `App\Support\Auditable`, `subject_type = "connections"`, `subject_id` = connection id:

| action | before | after |
| --- | --- | --- |
| `connection.created` | null | `{exchange, env, label, api_key_last4, secrets_changed: ["api_key","api_secret","passphrase"]}` |
| `connection.updated` | `{exchange, env, label, api_key_last4}` | same + `secrets_changed: [names of secrets replaced]` |
| `connection.deleted` | `{exchange, env, label, api_key_last4}` | null |

## Logging

`bootstrap/app.php` adds `api_key`, `api_secret`, `passphrase` to the exception handler's `dontFlash`.
`tests/Feature/Connections/ConnectionsApiTest.php` forces a ValidationException with secrets in the payload
to be reported and asserts the secrets are absent from the log file, the log context, the exception
message/errors/trace and the response.

## Not built here

- Test connection (`POST /api/connections/{id}/test` → trading_core) — ATL-8.
- Live connections — Phase 4 (the 422 above).
- Data-source and Anthropic keys — later patches; they will need their own table or a `kind` column.
