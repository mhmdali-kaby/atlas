# ATL-246 — Two-step sign-in API (TOTP / recovery code)

Decision #265 (1). For ATL-248 (the login screen's code step).

## The flow

1. `POST /api/auth/login` `{"email", "password"}` — unchanged for an owner without 2FA.
2. If the owner has 2FA **confirmed** (an unconfirmed setup does not count), a correct password returns
   `200 {"two_factor": true}` and does **not** sign in. The session (regenerated) now holds a pending
   challenge that lives `atlas_defaults.two_factor.challenge_minutes` (5) minutes.
3. `POST /api/auth/two-factor-challenge` `{"code": "123456"}` **or** `{"recovery_code": "k3m9q-7xw2p"}`
   → `200` with exactly the normal login body, the owner is signed in and the session id is regenerated.

Tell the two `/login` answers apart by the key: `two_factor` (step 2) vs `data` (signed in).

## Responses

Normal / challenge success (UserResource):
```json
{"data": {"id": "9d2…uuid", "name": "Owner", "email": "owner@atlas.test", "locale": "ar", "theme": "dark"}}
```

Wrong or reused code (the challenge stays open; the owner can retry):
```json
{"message": "The code is incorrect or has already been used. …", "error": "code_wrong",
 "errors": {"code": ["…same message…"]}}
```
With `recovery_code` the error key is `errors.recovery_code`. A recovery code works once.

No live challenge — expired after 5 min, never started, or 2FA turned off meanwhile (422):
```json
{"message": "Your sign-in has expired. Enter your email and password again.", "error": "challenge_expired",
 "errors": {"code": ["…same message…"]}}
```
On `challenge_expired` go back to the email + password step; the pending state is cleared.

Neither field sent → 422 `errors.code` and `errors.recovery_code` (standard validation).

6th attempt within a minute (per session id + IP, `challenge_attempts_per_minute` = 5) → `429`
`{"message": "Too Many Attempts."}` with `Retry-After`. The limit is applied in the controller, not route
middleware, because throttle middleware runs before the Sanctum session is started.

Messages are in `lang/{en,ar}/two_factor.php` (`challenge_expired`, `challenge_code_wrong`) and follow
`Accept-Language`.

## Resuming after reload

`GET /api/auth/status` (guest) now returns:
```json
{"owner_exists": true, "two_factor_pending": true}
```
`two_factor_pending` is true only while this session has an unexpired challenge. When it is true, show the
code step straight away.

## Audit rows (`subject_type` `users`, `subject_id` = owner id; no codes ever)

| action | after |
| --- | --- |
| `auth.two_factor_challenge.passed` | `{"method": "code"}` or `{"method": "recovery_code", "recovery_codes_left": 7}` |
| `auth.two_factor_challenge.failed` | `{"method": "code"\|"recovery_code", "reason": "wrong_code"\|"expired"}` |

An expired row is written only when the session still held the stale challenge.

## Testing note

Pest JSON requests send cookies only after `$this->withCredentials()`. Without it each request gets a fresh
session and the array session driver's in-memory store hides that. `tests/Feature/Auth/TwoFactorChallengeTest.php`
calls it; copy that setup for anything that depends on the session.

## Not built

The SPA code step (ATL-248) and the lost-everything reset (`./atlas reset-two-factor`, raised by UX, no task yet).
