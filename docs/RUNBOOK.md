# RUNBOOK

Operational notes for running Atlas on the owner's Mac (brief §21). This file grows with each
patch; it only documents what exists today.

## Start / stop

- `./atlas up` — starts Postgres and Redis (Docker Compose), then the API, Reverb, the War Room
  bridge, Horizon, the scheduler, trading_core, the news ingest process (`ingest`) and the
  frontend dev server, each natively and each bound to `127.0.0.1` (ingest listens on no port). On first run it also generates `REVERB_APP_SECRET` (see "Rotate keys"
  below) and keeps the Mac awake with `caffeinate` while it runs.
- `./atlas down` — stops the application processes. Postgres and Redis keep running; `docker
  compose stop` stops them.
- `./atlas restart` — `down` then `up`.
- `./atlas status` — which processes and ports are up.
- `./atlas logs [name]` — tails `.atlas/logs/<name>.log`, or every log if `name` is omitted.
  `./atlas logs ingest` shows one JSON line per poll (`poll ok` with counts, `poll failed` with
  the error and `consecutive_failures`). A source that fails 3 times in a row is skipped for 30
  minutes (`disabled_until` in `news_sources`); it is retried automatically after that.
- News clustering needs the local embedding model (`paraphrase-multilingual-MiniLM-L12-v2`, about
  470 MB). It is downloaded once, on first use, into `~/Library/Caches/atlas/models`
  (`ATLAS_MODEL_CACHE` overrides the folder), and after that it loads offline. To fetch it ahead
  of time: `cd services && uv run --package atlas-ingest python -m atlas_ingest.evaluation report
  --embedder real`. Deleting that folder only costs a re-download.
- `./atlas doctor` — runs `php artisan atlas:doctor`: Postgres, extensions, migrations, Redis,
  trading_core, OKX clock drift, the demo connection, the withdraw-permission rule, free disk
  space, session/debug security, the Reverb secret, and the backup check. Any `✗` line fails the
  command (exit 1); `⚠` is a warning and still exits 0.

## Backup / restore

- `./atlas backup [dir]` — `pg_dump -Fc` of the `atlas` database into `dir` (default
  `../backups` next to the repository), named `atlas-<UTC timestamp>.dump`.
- Restore: `docker exec -i <postgres container> pg_restore -U atlas -d atlas --clean
  /path/to/the.dump` with the app stopped (`./atlas down`) first.
- `atlas:doctor`'s `backup` check only warns today (the scheduled-backup feature is not built
  yet); it never fails the command.

## Rotate keys

Every key below lives only in `backend/.env` or the macOS Keychain, never in git, a log or a
response. After rotating any of them, restart with `./atlas restart` so every process picks up
the new value.

- **`APP_KEY`** — encrypts `connections` (OKX key/secret/passphrase) and signs sessions. Loaded
  from the macOS Keychain (`security find-generic-password -s atlas -a APP_KEY`) when set there,
  else from `backend/.env`. Rotating it re-encrypts nothing automatically: generate the new key
  with `php artisan key:generate --show`, decrypt and re-save every `connections` row under the
  *old* key first (or re-enter the OKX keys in Settings → Connections after rotating), then store
  the new value in the Keychain (`security add-generic-password -U -s atlas -a APP_KEY -w
  '<new key>'`) or in `backend/.env`.
- **`INTERNAL_SHARED_SECRET`** — HMACs every Laravel → trading_core call. Generate a new one
  (`openssl rand -hex 32`), set it in `backend/.env` *and* in trading_core's environment (it reads
  the same `backend/.env`, decision #235), then restart both. A mismatched secret fails every
  `/internal/*` call with 401.
- **`REVERB_APP_SECRET`** — signs private-channel auth (`/broadcasting/auth`) and the Reverb HTTP
  publish API. An empty or under-32-character value lets anyone compute valid channel auth with
  `HMAC(key="", …)`, which is how security round 4's finding 1 read and injected War Room
  messages from any web page. `./atlas up` generates one with `openssl rand -hex 32` into
  `backend/.env` the first time it finds the value empty or short, and `atlas:doctor`'s
  `reverb_secret` check fails on anything under 32 characters so a bad value never passes
  unnoticed. To rotate it by hand: `openssl rand -hex 32`, write it into `REVERB_APP_SECRET` in
  `backend/.env`, then `./atlas restart` (every open browser tab's socket reconnects and
  re-authenticates automatically).
- **OKX API key/secret/passphrase** — entered by the owner in Settings → Connections, never in
  `.env` or a prompt to the agents. Rotate by creating a new key on OKX and re-entering it there;
  the old one is simply overwritten (encrypted at rest, shown back only as its last 4 characters).

## Alerts

Circuit breakers, the kill switch and most owner-facing alerts arrive with the Risk Engine
(Phase 2) and are documented here when they ship. Today, `./atlas doctor` is the alert surface:
run it after anything looks wrong, and fix the first `✗` line it prints.
