# ATL-109 — Market Scout's intraday re-runs: triggers, debounce, the re-run card

For ATL-115 (the War Room watchlist card) and anyone touching Market Scout later.
Code: `services/agents/src/atlas_agents/scout/rerun.py` (rules, spike maths, debounce, card text `rerun_line`), `atlas_agents.mentions.resolve_alert_row` (the alert's War Room row) and the
"intraday re-runs" section at the end of `services/agents/src/atlas_agents/agents/market_scout.py`
(hooks). Tests: the ATL-109 section at the end of `services/agents/tests/test_market_scout.py`.

## The two-minute version

- Two code triggers (no model) re-run today's watchlist through ATL-108's `run_watchlist`, which
  saves a **new version** for the same Baghdad date and carries the owner's pins and removals:
  1. `HOOKS.event("market_scout", "news.alert")` — an ingest alert with `importance >=
     agents.scout.rerun_alert_importance` (5). News Intelligence still analyses every alert as before;
     Scout is now the second listener (the ATL-107 test that said "only News" was updated).
  2. `HOOKS.schedule("market_scout", "scout_spike_check", every_seconds=agents.scout.spike_check_seconds)`
     (300 s) — a universe instrument (`build_universe(ctx).kept`) that is **not** on today's
     watchlist (current version, items not removed by the owner) whose latest closed 1H bar's
     |return| > `agents.scout.spike_sigma` (3) × the sample stdev of its previous
     `spike_lookback_days` (30) of 1H returns (at least `spike_min_returns`, 100). "Latest closed"
     = the bar that opened one hour before the current UTC hour; a feed that is behind triggers
     nothing. The latest return is not part of its own stdev. When several spike, the strongest
     names the trigger.
- Re-runs start only inside `agents.scout.rerun_window` (`08:10`–`23:00` Asia/Baghdad, start
  inclusive, end exclusive) and at most once per `agents.scout.rerun_min_minutes` (60). The
  debounce counts from when a re-run **started**, is kept in memory per supervisor, and is also
  read from today's saved re-run watchlists (`data->'trigger'->>'kind'`), so a restart does not
  re-run twice. Only re-runs debounce each other: the 08:00 watchlist and @Scout mentions do not.
- A re-run runs in the background (like an @Scout mention) because the teammates' replies arrive
  through the War Room consumer that delivered the alert. `agent_runs.trigger` is `alert` for both
  kinds; `correlation_id` is `scout_rerun:<kind>:<uuid>`.
- The model is told it is an intraday re-run and gets `intraday_rerun_trigger` in its input table,
  **without** the alert headline (untrusted text). The headline is on the card only.

## The card (only on a re-run)

`trigger` is an object instead of the 08:00/mention string (`"schedule"` / `"mention"`), and
`changes` is added. Both are also stored in `watchlists.data`. Everything else is ATL-108's card.

News alert:

```json
"trigger": {
  "kind": "news_alert",
  "message_id": "9f1c…",            // the alert's warroom_messages row id; null if the bridge had not stored it
  "event_id": "2b7e…",              // the news.alert envelope id
  "headline": "Fed announces emergency rate cut",
  "importance": 5,
  "at": "2034-01-01T09:00:00+00:00" // the alert envelope's ts
}
```

Volatility spike:

```json
"trigger": {
  "kind": "vol_spike",
  "inst_id": "SOL-USDT-SWAP",
  "sigma": 3.5,                         // rounded to 2 places
  "bar": "1H",
  "bar_ts": "2034-01-01T08:00:00+00:00", // open time of the spiking bar
  "at": "2034-01-01T09:00:00+00:00"      // when that bar closed
}
```

Changes, against the version this one replaced (owner-removed items of that version are not
counted as "on the list"):

```json
"changes": {
  "since": "2034-01-01T05:01:00+00:00", // created_at of the previous version; null if there was none
  "version": 1,                          // the previous version; null if none
  "added": ["ETH-USDT-SWAP"],
  "dropped": ["SOL-USDT-SWAP"]
}
```

`content_md` starts with a code-written line, then ATL-108's text:
`**Intraday re-run** (importance-5 news alert: <headline>). Added: ETH-USDT-SWAP. Dropped: SOL-USDT-SWAP.`
or `**Intraday re-run** (SOL-USDT-SWAP moved 3.5 sigma on the 1H bar closed at <ISO>). Added: … Dropped: none.`
`trigger`, `changes` and that line are outside the number guard (code-written).

An empty re-run (empty shortlist) carries the same `trigger` and `changes` and the same first line.

## Differences from the design doc to settle in ATL-115

- The design names the spike kind `volatility_spike`; the task's criterion names it `vol_spike`,
  and that is what is built. The design's `importance` and `at` (news) and `at` (spike) are present.
- `trigger` is still a plain string on non-re-run cards (`"schedule"`, `"mention"`), as ATL-108
  built it. Treat a string as "not a re-run".
- "The older card says it was replaced": not stored on the old card. The current version is the
  highest `version` for the date (GET /api/watchlists); a card whose `version` is lower is replaced.

## Not built here

- @Scout mentions are not debounced, and a re-run does not wait for an 08:00 run still in progress
  (the window opens at 08:10, after the 08:00 wait of up to 5 minutes).
- A failed re-run (degraded model output) still uses up the 60-minute debounce.
