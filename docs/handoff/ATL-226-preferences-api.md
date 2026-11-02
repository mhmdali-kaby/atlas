# ATL-226 — Owner preferences API

Decision #255 (1). For the Settings → General page and the SPA boot.

## Endpoints

Both need the Sanctum SPA session (`auth:sanctum`); unauthenticated → `401 {"message":"Unauthenticated."}`.

### GET /api/me/preferences

```json
200
{ "data": { "name": "Owner", "locale": "en", "timezone": "Asia/Baghdad", "theme": "system" } }
```

Defaults for a fresh owner: `locale` `en`, `timezone` `Asia/Baghdad`, `theme` `system`.

### PATCH /api/me/preferences

Every field is optional; send only what changed. Throttle 60/min.

```json
{ "locale": "ar", "timezone": "Europe/London", "theme": "dark", "name": "Mohammed" }
```

```json
200
{ "data": { "name": "Mohammed", "locale": "ar", "timezone": "Europe/London", "theme": "dark" } }
```

Rules: `name` 1–80 characters, not blank (trimmed); `locale` `en|ar`; `timezone` an exact
PHP/IANA identifier (`DateTimeZone::listIdentifiers()`, e.g. `Europe/London`; no `UTC+3`
or aliases such as `US/Eastern`); `theme` `system|light|dark`.

422 (English):

```json
{
  "message": "The time zone must be a valid IANA time zone, such as Asia/Baghdad. (and 2 more errors)",
  "errors": {
    "locale": ["The selected language is invalid."],
    "timezone": ["The time zone must be a valid IANA time zone, such as Asia/Baghdad."],
    "theme": ["The selected appearance is invalid."]
  }
}
```

With `Accept-Language: ar` the same keys carry Arabic messages, e.g.
`"locale": ["القيمة المحددة لـ اللغة غير صالحة."]`,
`"timezone": ["يجب أن تكون المنطقة الزمنية منطقة IANA صالحة، مثل Asia/Baghdad."]`.
Field names come from `backend/lang/{en,ar}/preferences.php`.

## Audit

Each PATCH that changes something writes one `audit_log` row: `action` `preferences.updated`,
`subject_type` `users`, `subject_id` the user id, `before`/`after` holding only the changed
fields (e.g. `before {"theme":"system"}`, `after {"theme":"dark"}`). A PATCH that changes
nothing writes no row.

## Two-minute version

- Timezone is display-only. Schedules, the Baghdad business day and Python keep
  `timezone.display`; nothing server-side reads `users.timezone`.
- `GET /api/auth/me` (UserResource) is unchanged; read preferences from this endpoint.
- The server picks its response language from `Accept-Language` (ATL-222), not from
  `users.locale`. Sending `Accept-Language` from the chosen locale is the frontend task.
- The browser time-zone picker can use `Intl.supportedValuesOf('timeZone')`; a few of its
  names may be aliases PHP rejects — show the 422 message if so.
