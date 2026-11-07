# ATL-2 — Owner sign-in (Sanctum SPA cookie session)

Atlas has one owner, authenticated with Laravel Sanctum's **SPA cookie mode** (session cookie +
CSRF). There are **no API tokens**: never store anything auth-related in `localStorage`.

SPA: `http://localhost:3000` (`FRONTEND_URL`). API: `http://localhost:8000` (`APP_URL`).

## Creating the owner

```shell
cd backend && php artisan atlas:create-owner
```

Prompts for name, email, password (min 12, typed twice; stored bcrypt). Exits **1** with
"An owner already exists…" if any user exists. Nothing is seeded: no default credentials.

## The CSRF dance (what the SPA must do)

1. Every request: `credentials: 'include'` (fetch) or `withCredentials = true` (axios), and
   `Accept: application/json`. The browser adds `Origin`/`Referer`; Sanctum only treats requests
   from `localhost:3000` (FRONTEND_URL host:port) as stateful.
2. Once, before the first state-changing request: `GET /sanctum/csrf-cookie` → `204`, sets the
   `XSRF-TOKEN` cookie (readable by JS) and the `atlas-session` cookie (HttpOnly; name from APP_NAME).
3. On every `POST/PUT/PATCH/DELETE`: send header `X-XSRF-TOKEN` = **URL-decoded** value of the
   `XSRF-TOKEN` cookie. Axios does this itself with `withXSRFToken = true`; with fetch, read
   `document.cookie` and `decodeURIComponent` it.
4. After login and after logout the session (and CSRF token) is regenerated; the new
   `XSRF-TOKEN` cookie arrives in that response, so always read the cookie fresh per request.

Missing/stale CSRF token → **419**. If you get 419, call `/sanctum/csrf-cookie` again and retry once.

## Endpoints

### GET /api/auth/status

Guest (no session, no CSRF needed for a GET). Throttled 60/min per IP. Always 200, and the body is
exactly this — never a name or email:

```json
{ "owner_exists": false }
```

Use it on the login screen: `owner_exists: false` → show the first-run state ("Run
`./atlas create-owner` in Terminal") instead of the sign-in form. It flips to `true` as soon as
`php artisan atlas:create-owner` succeeds.

Suggested boot order for the SPA: `GET /api/auth/me` → 200 means signed in; 401 → `GET /api/auth/status`
to choose between the sign-in form and the create-owner instructions.

### POST /api/auth/login

Guest. Throttled: 5 attempts per minute per IP (named limiter `login`); the 6th → 429.

Request:

```json
{ "email": "owner@atlas.test", "password": "correct horse battery staple" }
```

200:

```json
{
  "data": {
    "id": "01a18376-1d35-70c2-ad01-6d1d2cd870ad",
    "name": "Owner",
    "email": "owner@atlas.test",
    "locale": "en",
    "theme": "system"
  }
}
```

### GET /api/auth/me

`auth:sanctum`. 200 with the same `{ "data": { id, name, email, locale, theme } }` body as login.
Use it on app boot to decide between the app and the login screen (401 → login).

### POST /api/auth/logout

`auth:sanctum` + CSRF. **204**, empty body. Session invalidated; `/me` returns 401 afterwards.

### User shape

| field | type | notes |
| --- | --- | --- |
| id | string | UUID (v7, time-ordered) — users.id is a uuid column |
| name | string | |
| email | string | stored lower-case by `atlas:create-owner` |
| locale | `"en" \| "ar"` | default `en` (`User::LOCALES`) |
| theme | `"system" \| "light" \| "dark"` | default `system` (`User::THEMES`) |

No `password`/`remember_token` ever appears. Nothing in this task updates locale/theme; the
settings task that owns General settings will add that endpoint.

## Error shapes

422 — validation or wrong credentials (wrong password is reported on `email`, never `password`):

```json
{ "message": "These credentials do not match our records.",
  "errors": { "email": ["These credentials do not match our records."] } }
```

401 — no/expired session (always JSON, even without `Accept`; there is no login redirect):

```json
{ "message": "Unauthenticated." }
```

419 — CSRF token missing/mismatch:

```json
{ "message": "CSRF token mismatch." }
```

429 — too many login attempts (`Retry-After` header gives seconds):

```json
{ "message": "Too Many Attempts." }
```

## CORS and headers

- `config/cors.php`: paths `api/*`, `sanctum/csrf-cookie`; only `FRONTEND_URL` gets
  `Access-Control-Allow-Origin` (exact-origin pattern, `Vary: Origin`) with
  `Access-Control-Allow-Credentials: true`. Any other Origin gets no ACAO header.
- `App\Http\Middleware\SecurityHeaders` (global): `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` on every response, and on
  `api/*` + `sanctum/*` `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`.

## For other backend features

- Protect routes with `Route::middleware('auth:sanctum')` in your own `routes/api/<feature>.php`.
- Tests: `Sanctum::actingAs($user)` or `$this->actingAs($user)`; factories give a random
  password, so pass `'password' => '…'` if a test logs in for real.
- Foreign keys to users must be `foreignUuid('user_id')` (users.id is a UUID).
- `SANCTUM_STATEFUL_DOMAINS` is gone; stateful domains derive from `FRONTEND_URL` + `APP_URL`
  in `config/sanctum.php`. Change the SPA origin with `FRONTEND_URL` only.
