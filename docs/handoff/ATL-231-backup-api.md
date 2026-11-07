# ATL-231 handoff — daily backup: settings keys, API, command, notification

For ATL-232 (Settings → Backup page). Decision #255 (3).

## The two-minute version

- The four settings are ordinary versioned settings keys. Edit them with the existing
  `PATCH /api/settings` (`{env, version, changes}`), not with a backup endpoint.
- Settings rows are per env, but a backup is of the whole database. **The scheduler, the command and
  `GET /api/backup` read the active env's row** (`ActiveEnv::get()`, today always `demo`). So the page
  should PATCH with `env` = `data.env` from `GET /api/backup`.
- `GET /api/backup` is the page's one read: settings in force, last 10 runs, newest good run and its
  age, and a `stale` flag (same 26 h rule as `atlas:doctor`).
- "Back up now" is `POST /api/backup/run`. It only queues; poll `GET /api/backup` to see the new run
  appear (a run is recorded only when it finishes; there is no "running" row).
- Validation never creates the folder. The run creates it (mode 0700) and writes dumps 0600.

## Settings keys

| key | type | default | rule |
| --- | --- | --- | --- |
| `backup.enabled` | bool | `true` | strict boolean. Off = the daily schedule skips; "Back up now" and the command still run. |
| `backup.folder` | string | `<repo>/../backups` (absolute, resolved at runtime) | absolute (starts with `/`, no `.`/`..` segments, ≤ 1024 chars), exists or can be created, writable, **not inside the repository** (symlinks resolved). Trailing `/` is dropped on save. |
| `backup.time` | string `HH:MM` | `"03:00"` | 00:00–23:59, **Asia/Baghdad** (no DST) |
| `backup.keep` | int | `14` | strict integer 1–90 |

Defaults live in `config/defaults.yaml` → `backup`.

### PATCH examples

```json
PATCH /api/settings
{ "env": "demo", "version": 7, "changes": { "backup.folder": "/Users/owner/Atlas Backups", "backup.time": "04:15", "backup.keep": 30 } }
→ 200, data.settings["backup.folder"] === "/Users/owner/Atlas Backups"
```

422s (the error key is the dotted field, under `changes.`):

```json
{ "message": "...", "errors": { "changes.backup.folder": [
  "The Backup folder must be a full path starting with \"/\", such as \"/Users/you/Atlas Backups\"." ] } }
```

| case | message (en) |
| --- | --- |
| relative / `..` | `The Backup folder must be a full path starting with "/", such as "/Users/you/Atlas Backups".` |
| inside the repo | `The Backup folder must be outside the Atlas folder, so backups are never stored next to the code.` |
| not writable / a file in the way | `Atlas cannot write to the Backup folder. Choose a folder you can write to, or one that can be created.` |
| bad time | `The Backup time must be a time between 00:00 and 23:59, written as HH:MM such as "03:00".` |
| keep out of range | Laravel's `between` with attribute "Number of backups to keep" |

Arabic is served with `Accept-Language: ar` (lang/ar/settings.php).

## GET /api/backup

Auth: Sanctum session. Example:

```json
{ "data": {
  "env": "demo",
  "settings": { "enabled": true, "folder": "/Users/owner/backups", "time": "03:00", "timezone": "Asia/Baghdad", "keep": 14 },
  "runs": [
    { "id": "01a19b2a-…", "file": "atlas-20261102-000001.dump", "path": "/Users/owner/backups/atlas-20261102-000001.dump",
      "bytes": null, "status": "failed", "error": "pg_dump exited with code 1: pg_dump: error: …",
      "started_at": "2026-11-02T00:00:01.000000Z", "finished_at": "2026-11-02T00:00:02.000000Z" },
    { "id": "01a19b29-…", "file": "atlas-20261101-000000.dump", "path": "…", "bytes": 174250, "status": "ok",
      "error": null, "started_at": "…", "finished_at": "…" }
  ],
  "last_good": { "id": "01a19b29-…", "file": "atlas-20261101-000000.dump", "path": "…", "bytes": 174250,
                 "status": "ok", "error": null, "started_at": "…", "finished_at": "…", "age_seconds": 86400 },
  "stale_after_hours": 26,
  "stale": false
} }
```

- `runs`: newest first by `started_at`, at most `backup.runs_listed` (10). `status` is `ok` | `failed`.
- `last_good`: null when there has never been an ok run; then `stale` is `true`.
- `error` is pg_dump's own text (English, from the tool), cut to 2,000 chars. Show it as LTR code text.

## POST /api/backup/run

Auth: Sanctum session + CSRF. No body. `202 { "data": { "queued": true, "message": "A backup will start in a moment." } }`.
Throttle `backup-run`: 1 per minute for the whole app (not per user) → 429 on the second press.
Audited as `backup.run_requested` (`subject_type` `backups`, `after.folder`). The job `RunBackup` runs on
the `redis-backups` connection / `backups` queue (Horizon `supervisor-backups`, timeout 1,860 s).
If a backup is already running (cache lock `atlas:backup:run`), the job does nothing.

## Command, files, schedule

- `php artisan atlas:backup` → `pg_dump --format=custom` (binary `PG_DUMP_BIN`, default `pg_dump`; on
  the owner's Mac `/opt/homebrew/opt/libpq/bin/pg_dump`). Password only via `PGPASSWORD`. Timeout
  1,800 s, `->throw()`. Exit 0 ok / already running, 1 failed.
- File name `atlas-<UTC yyyymmdd-HHMMSS>.dump`. After an ok run, dumps above `keep` are deleted,
  oldest first; only names matching `^atlas-\d{8}-\d{6}\.dump$` are ever deleted.
- Scheduled in `routes/console.php`: `dailyAt(backup.time)->timezone('Asia/Baghdad')`,
  `when(backup.enabled)`, `withoutOverlapping(40)` (minutes). Read at each `schedule:run`, so a
  settings change applies with no restart. `php artisan schedule:list --timezone=Asia/Baghdad`.

## Notification

A failed run creates a `backup_failed` notification (severity `warning`, link `/settings/backup`,
params `{ backup_id, started_at }` — never the error text). It goes through the normal Notifier, so
the `notifications.backup_failed.{in_app,browser,telegram}` settings switches exist (in_app and browser
on by default). I added only the title translation (`events.backup_failed.title`) to the frontend
`notifications.json`; `features/notifications/events.ts` does not list `backup_failed` yet, so the bell
renders it through the default case and Settings → Notifications does not show its switches. Adding
it to the catalogue (group, tone, icon, action) is a UI decision for the Backup/notifications screens.

## Doctor

`atlas:doctor`'s `backup` line: ✓ `Last good backup 2.0 h ago (atlas-….dump)`; ⚠ `No good backup yet…`;
⚠ `Last good backup is 27.3 h old (over 26 h)…`. Never a failure.

## Not built here

The Settings → Backup page (ATL-232), a restore command (restore stays manual, docs/RUNBOOK.md),
and a "running" state for an in-flight backup.
