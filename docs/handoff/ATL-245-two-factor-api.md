# ATL-245 — TOTP enrolment API and the TwoFactor service

Decision #265 (1). Optional, off by default. Packages: `pragmarx/google2fa` ^9.1, `bacon/bacon-qr-code` ^3.1.

## The one thing to know

`TwoFactor::verifyCode()` checks against whatever secret is stored — **including an unconfirmed one**
(that is how `confirm` works). The login challenge must check `TwoFactor::enabled($user)` first and
only then call `verifyCode()` / `consumeRecoveryCode()`. `consumeRecoveryCode()` already returns false
when 2FA is not enabled; `verifyCode()` does not.

## Storage (users)

| column | type | contents |
| --- | --- | --- |
| `two_factor_secret` | text, `encrypted` cast | base32 secret (32 chars); set by start, kept after confirm |
| `two_factor_recovery_codes` | text, `encrypted:array` cast | JSON list of bcrypt hashes |
| `two_factor_confirmed_at` | timestamp | 2FA is on only when this and the secret are set |
| `two_factor_last_timestep` | bigint | 30 s step of the last accepted code (replay guard) |

All four except `confirmed_at` are in the model's `#[Hidden]`.

Defaults (`config/defaults.yaml` → `atlas_defaults.two_factor`): `window: 1`, `recovery_codes: 8`,
`attempts_per_minute: 5`, `qr_size_px: 192`.

## Routes — `backend/routes/api/two_factor.php`, all `auth:sanctum`

Writes share the `two-factor` rate limiter (5/min per user → 429). GET is `throttle:60,1`.

`GET /api/me/two-factor`
```json
{"data": {"enabled": true, "confirmed_at": "2026-11-06T09:12:03Z", "recovery_codes_left": 7}}
```
Off: `{"data": {"enabled": false, "confirmed_at": null, "recovery_codes_left": 0}}`. Never contains the secret or codes.

`POST /api/me/two-factor/start` `{"password": "…"}`
```json
{"data": {"qr_svg": "<?xml …><svg …>…</svg>", "secret": "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"}}
```
Replaces any earlier unconfirmed secret (and clears codes/last step). Wrong password → 422
`errors.password`. Already enabled → 409 `{"message": "…", "error": "already_enabled"}`.

`POST /api/me/two-factor/confirm` `{"code": "123456"}`
```json
{"data": {"enabled": true, "confirmed_at": "2026-11-06T09:12:03Z", "recovery_codes_left": 8,
          "recovery_codes": ["k3m9q-7xw2p", "… 8 in total, shown once"]}}
```
Wrong or reused code → 422 `errors.code`. No start yet → 409 `not_started`. Already on → 409 `already_enabled`.

`POST /api/me/two-factor/recovery-codes` `{"password": "…"}` → same shape as confirm with 8 new codes;
the old ones stop working. Wrong password → 422. 2FA off → 409 `not_enabled`.

`DELETE /api/me/two-factor` `{"password": "…"}` → the off status above; clears all four columns
(also abandons a pending setup). Wrong password → 422.

Messages are in `lang/{en,ar}/two_factor.php`.

## Audit rows (`subject_type` `users`, `subject_id` = user id)

| action | before | after |
| --- | --- | --- |
| `two_factor.started` | null | `{"enabled": false}` |
| `two_factor.confirmed` | `{"enabled": false}` | `{"enabled": true, "recovery_codes_left": 8}` |
| `two_factor.recovery_codes_regenerated` | `{"recovery_codes_left": n}` | `{"recovery_codes_left": 8}` |
| `two_factor.disabled` | `{"enabled": bool}` | `{"enabled": false}` |

## Service — `App\Support\TwoFactor\TwoFactor` (container-resolvable)

```php
enabled(User $u): bool
recoveryCodesLeft(User $u): int
startEnrolment(User $u): string             // new unconfirmed secret, saved
qrCodeSvg(User $u, string $secret): string
verifyCode(User $u, string $code): bool      // 6 digits, window 1, refuses a step <= last accepted; saves the step (row lock)
consumeRecoveryCode(User $u, string $code): bool  // case-insensitive, removes the match (row lock); false if 2FA off
confirm(User $u): list<string>               // sets confirmed_at, returns 8 plaintext codes
regenerateRecoveryCodes(User $u): list<string>
disable(User $u): void
```
`verifyCode` and `consumeRecoveryCode` refresh the passed `$user` from the locked row.

## Not built here

The login challenge (`POST /api/auth/login` returning `{two_factor: true}`, `/api/auth/two-factor-challenge`)
and the Settings UI belong to their own tasks.
