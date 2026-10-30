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

## OKX demo integration tests (brief §19, ATL-152)

These place real orders on the OKX **demo** account, so they are never part of `uv run pytest`
(marker `okx_demo`, skipped unless `ATLAS_OKX_DEMO_IT=1`). Run them by hand:

1. Save the demo key in Settings → Connections and press **Test connection** (the suite reads that
   encrypted row; it never takes a key from .env, fixtures or the command line).
2. `./atlas down` — trading_core's own 5-minute reconciliation must not act on the account at the
   same time.
3. Make sure this checkout's test database is migrated (`cd backend && php artisan test` once).
4. Run, from the repository root:

   ```
   cd services && APP_KEY="$(security find-generic-password -s atlas -a APP_KEY -w)" \
     ATLAS_OKX_DEMO_IT=1 uv run pytest -m okx_demo trading_core/tests/integration -v
   ```

   `APP_KEY` may be omitted: the suite reads the same Keychain entry itself. The app database is
   `DB_DATABASE` (default `atlas`); `ATLAS_OKX_DEMO_SOURCE_DB` points it elsewhere.

Cases: (1) a limit buy 30% below the market on BTC-USDT-SWAP is live on OKX, then cancelled;
(2) a minimum-size market buy with attached TP/SL on BTC-USDT-SWAP and XAU-USDT-SWAP shows an SL algo
on OKX, then is closed (XAU is skipped with its reason if the demo universe lacks it); (3) kill
switch with flatten leaves no order, algo or position; (4) the stop is cancelled directly on OKX and
one reconciliation run places a new one. Orders go through the real Risk Engine and Executor; the
trade size is OKX's minimum, so no demo balance is assumed. The working rows live in the
per-checkout test database, not the app database.

Every case cleans up its instrument (cancel orders and algos, close the position) even when it fails.
A case skips when its instrument already has a position or open orders on the demo account. The
kill-switch case closes everything on the account, so it skips when anything else is open unless
`ATLAS_OKX_DEMO_IT_FLATTEN_ALL=1`. If a run is interrupted, check the demo account's open
orders and positions on BTC-USDT-SWAP and XAU-USDT-SWAP.

Before touching any database the suite resolves and connects to the demo REST host. If that fails
every case errors at once with `OKX unreachable: ...` (DNS or network); fix the Mac's network or DNS
and run again. Nothing is placed and nothing needs cleaning up in that case.

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
