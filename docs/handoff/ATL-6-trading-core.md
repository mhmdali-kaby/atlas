# ATL-6 — trading_core internal API, HMAC signing, Laravel decrypt

Decision #235. trading_core is a FastAPI app on `127.0.0.1:8100` that Laravel calls over signed HTTP.
It never receives plaintext keys: it reads the `connections` row and decrypts it itself with
`atlas_shared.laravel_crypt`.

## Run it

```sh
cd services && INTERNAL_SHARED_SECRET=... uv run --package atlas-trading-core python -m trading_core
```

Environment (process env, read once at startup by `trading_core.config.Settings.from_env()`):

| Variable | Default | Notes |
| --- | --- | --- |
| `TRADING_CORE_HOST` | `127.0.0.1` | never bind 0.0.0.0 outside Docker |
| `TRADING_CORE_PORT` | `8100` | |
| `ATLAS_ENV` (or `ATLAS_DEFAULT_ENV`) | `DEFAULTS["env"]["default"]` = `demo` | must be in `DEFAULTS["env"]["allowed"]` |
| `INTERNAL_SHARED_SECRET` | empty | empty = **every** `/internal` call gets 401 (fails closed, warning logged) |
| `APP_KEY` | — | not read by ATL-6 yet; pass it to `laravel_crypt` when you decrypt connections |

## Routes

- `GET /health` — unsigned. `200 {"status":"ok","env":"demo","version":"0.0.0"}`
- `GET /internal/ping` — signed no-op. `200 {"ok":true}`. Use it to check the secret end to end.
- Anything else under `/internal` without a valid signature → `401 {"detail":"invalid or missing signature"}`
  (even routes that do not exist, so the API surface is not discoverable).
- No `/openapi.json`, `/docs` or `/redoc` (all 404; `openapi_url=None` since ATL-34).

## Signing recipe (both sides)

```
timestamp = unix seconds, as a decimal string                    -> header X-Atlas-Timestamp
nonce     = 32 lower-case hex chars, fresh per call              -> header X-Atlas-Nonce
            (PHP: bin2hex(random_bytes(16)); Python: secrets.token_hex(16))
message   = timestamp + nonce + METHOD(upper) + path + raw_body  (path includes "?query" when there is one)
signature = hex(HMAC-SHA256(INTERNAL_SHARED_SECRET, message))    -> header X-Atlas-Signature
```

- Rejected if any header is missing, the timestamp is not all digits, the nonce is not exactly 32
  lower-case hex chars, `|now - timestamp| > 30 s`, or the signature does not match
  (`hmac.compare_digest`). Body, query and nonce are covered, so tampering with any of them is a 401.
- Replay (ATL-34): a nonce is accepted once. The same nonce again within 60 s is a 401 even with a
  valid MAC; after 60 s the 30 s skew check refuses it anyway. The store is in-process memory
  (`NonceStore` in `security.py`), fine while trading_core is one process; a restart forgets it, and
  only requests still inside the 30 s window could be replayed across one. Move it to Redis if
  trading_core ever runs more than one worker. A nonce is only recorded after its MAC checks out.
- Empty body = empty string (GET). POST bodies are signed byte-for-byte as sent.
- Pinned test vector: `test-vectors/internal_hmac.json` (secret, timestamp, nonce, method, path, body, signature).
  Pest `backend/tests/Unit/TradingCoreSigningTest.php` and pytest
  `services/trading_core/tests/test_signing.py` both check it — if you change the recipe, both fail.

## Add an /internal route (Python)

Put it on `internal_router` in `services/trading_core/src/trading_core/app.py` (or a new module with
`APIRouter(prefix="/internal")` included in `create_app`). The middleware already protects the whole
`/internal` prefix; do nothing per route. Read the body normally (`await request.body()` or a pydantic
model) — the middleware replays it unchanged.

For tests, `create_app(settings, extra_internal=router)` builds an app with a test-only router; see
`trading_core.security.signed_headers(secret, method, path, body)` for the three headers of one
signed request (a new nonce each call — reuse the dict to test a replay).

## Call it from PHP

```php
use App\Support\TradingCore;

$core = TradingCore::fromConfig();              // services.trading_core.url / .secret / .timeout
$res  = $core->get('/internal/ping', ['env' => 'demo']);      // query is RFC3986-encoded and signed
$res  = $core->post('/internal/connections/test', ['connection_id' => $id]);  // JSON body, signed
$res  = $core->send('POST', '/internal/x?y=1', $rawBody);   // raw: you control the exact bytes
```

Returns `Illuminate\Http\Client\Response`. Throws `RuntimeException` if `INTERNAL_SHARED_SECRET` is
empty. Config lives in `backend/config/services.php` → `trading_core` (`TRADING_CORE_URL`,
`INTERNAL_SHARED_SECRET`, both already in `.env.example`). In tests use `Http::fake()`; the client
resolves the HTTP factory from the container, so the fake applies.

## Decrypt a Laravel `encrypted` cast (Python)

```python
from atlas_shared.laravel_crypt import decrypt_string, DecryptError
api_key = decrypt_string(row.api_key, os.environ["APP_KEY"])   # APP_KEY as "base64:..."
```

AES-256-CBC, MAC (hex HMAC-SHA256 over the base64 `iv`+`value` strings) checked first with
`compare_digest`; any problem raises `DecryptError` without the plaintext or key in the message.
Only the current APP_KEY is supported (no `APP_PREVIOUS_KEYS` rotation yet). Golden fixture:
`services/shared/tests/fixtures/laravel_crypt_fixture.json` (throwaway test-only key, made by
Laravel's `Encrypter::encryptString`).

## Logging

`trading_core.logs.configure_logging()` (called by `__main__`) emits one JSON object per line to stdout.
`AccessLogMiddleware` logs only method, path, status, duration and `internal`; for `/internal` it drops
the query string. Uvicorn's own access log is disabled. Never log headers, bodies, decrypted values.

## Two-minute version

- pytest needed `consider_namespace_packages = true` in `services/pyproject.toml`: the folder
  `services/trading_core` has the same name as the `trading_core` package, and pytest's importlib mode
  otherwise shadows the package with the folder.
- `atlas_shared` now depends on `cryptography`.
- Not built here: the scheduler, OKX client, DB access, `/internal/connections/test` — those are later tasks.
