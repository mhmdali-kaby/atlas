# Decisions

The authoritative log is kept by the team's tooling and mirrored into `CLAUDE.md`. This file records
decisions where the brief was silent, as the brief asks.

- **Phases ship as patches; calendar gates are owner-run.** Gates measured in days or weeks of demo
  running are listed in the handover for the owner; each patch is accepted on its tests and a check
  against the OKX demo API.
- **Laravel 13** is the latest stable release at build time (brief: "12.x+").
- **Monorepo layout:** `backend/`, `frontend/`, `services/` (one uv workspace). Laravel migrations own
  the whole schema, including Timescale hypertables (raw SQL inside migrations), so there is no
  separate `db/` migration tool.
- **Local ports:** Postgres (Timescale + pgvector) on `127.0.0.1:5433`, Redis on `127.0.0.1:6380`, so
  Atlas never collides with other Postgres/Redis instances on the Mac. API `:8000`, SPA `:3000`,
  Reverb `:8080`, trading core `:8100`.
- **Tests run on real Postgres**, never SQLite: jsonb, arrays and hypertables behave differently there.
- **Each checkout has its own test database** (ATL-29): `atlas_test_<first 8 hex of sha1(realpath of
  the repository root)>`, so parallel runs in different worktrees never reset each other's schema.
  `backend/tests/bootstrap.php` (rule in `Tests\Support\TestDatabase`) points Pest at it and creates
  it if missing; migrations then add the extensions. Python tests use `atlas_shared.testing.db_settings()`,
  which applies the same rule, so `php artisan test` migrates the database pytest reads. Exporting
  `DB_TEST_DATABASE` forces one name on both sides (e.g. `atlas_test`).
- **Redis client is predis** (pure PHP), so no PHP extension is needed on the Mac.
- **Redis Streams entry format:** every entry on an `atlas.*` stream has one field, `envelope`, holding the
  brief §3.2 envelope as JSON (the `atlas_shared.events.EventEnvelope` shape). PHP publishes with
  `App\Support\Events\EventBus` on the `events` Redis connection, which has no key prefix.
- **Settings values travel as strings for decimals and integers for counts/leverage/autonomy;** a settings
  change event carries the new version, not the values, and consumers reload from Postgres.
- **Withdraw-enabled OKX keys: refused in live, accepted with a warning in demo** (ATL-30, CEO
  instruction of 2026-10-28, relaxing decision #237 for `env=demo` only). Test connection on a demo key
  whose `perm` includes `withdraw` returns `ok: true`, sets `last_verified_at` and carries
  `warnings: ["withdraw_permission_enabled"]`; sync runs for it like any verified key. A live key with
  `withdraw` is still refused with `error: "withdraw_permission_enabled"` and is never verified.
  `atlas:doctor` reports the demo case as a warning (exit 0) and fails on a live connection whose last
  test stored `withdraw`. Brief rule 5 is unchanged: no withdrawal or transfer code exists anywhere,
  and `NoWithdrawalCodeTest` guards it.
- **Money-limit defaults start small** (ATL-124). The brief (§14.4) names the money limits but gives
  no numbers, so a fresh env starts with `bot_capital_cap_usdt` 10,000, `max_usdt_per_trade` 1,000,
  `max_new_exposure_per_day_usdt` 5,000 and `max_trades_per_day` 10. The Risk Engine uses the smaller
  of these and the risk-profile percentages, so the first orders are small until the owner raises
  them in Settings. `bot_capital_cap_usdt` is the one nullable settings field: `null` means no cap
  (decision #250: sizing equity is then the account equity). Asset-class toggles are stored as `enabled_markets`, the list of
  `markets.kinds` that are on (dated futures and options start off); `whitelist` and `blocklist` are
  upper-case instId lists, the blocklist always wins.
- **Executor tables enforce rules 1 and 8 at the database** (ATL-141). `orders` refuses an
  `origin='atlas'` row with no `cl_ord_id`, and an atlas `entry` with no `proposal_id` and
  `risk_check_id`; protect/close/kill orders and reconciled `external` orders may leave them null.
  Trade-call retries resend the identical body, so a lost answer comes back as sCode 51016
  (duplicated clOrdId) and the Executor looks the order up rather than placing a second one. GET and
  POST `trade/order` share one rate-limit bucket. Executor defaults (`executor` in
  config/defaults.yaml): entry timeout 30 min, perps tdMode isolated, stop check 3 s, 3 tries,
  emergency stop 2.0 × ATR(1H).
- **Risk Engine check rules the brief leaves open** (ATL-127, `trading_core/risk/engine.py`, numbers in
  `risk_rules` of config/defaults.yaml). Check 1 reads freshness from the `tickers` channel stamp in
  Redis, not any-channel, so candle pushes cannot make a quiet ticker look fresh. Check 2's US hours
  are 09:30–16:00 America/New_York Mon–Fri, DST from the tz database; exchange holidays are not
  modelled (a holiday counts as open). Check 5 counts same-direction exposure in instruments with
  30-day corr > 0.8 (the instrument itself included; a pair with < 20 overlapping daily returns is
  "unknown" and not counted), and caps each asset class's **net** exposure at the same
  `max_correlated_exposure_pct`, since Settings has no separate per-class cap; it also enforces
  `max_trades_per_day`. Portfolio, loss, liquidation and blackout failures are hard rejects; only
  check 4's caps resize. Check 6's loss is start equity (first equity_curve point of the Baghdad day
  or ISO week) + net deposits since − current equity, so realised and unrealised both count;
  consecutive losses are trade bills (type 2) with a negative pnl, newest first. Check 7 estimates an
  isolated liquidation price as entry × (1 ∓ 1/leverage ± 0.005 maintenance). Check 9: autonomy 1 →
  `advise_only` (a new `risk_checks.decision` value), 2 → `awaiting_owner`, 3–4 → `awaiting_owner`
  until `autonomy.max_enabled_level` allows them (Phase 3), so nothing is sent without the owner.
  Fee rates are not stored yet, so sizing uses `risk_rules.fallback_taker_fee_rate` and the check
  records `fee_source: config_fallback`. A limit proposal with only an entry zone is sized at the
  zone bound farthest from the stop. Only proposals in `risk_rules.evaluable_statuses` (`passed`, and
  `risk_checked` so an owner Edit can re-run the check) are evaluated; any other status is refused
  before anything is written, so a cancelled, rejected or filled proposal cannot re-enter approval.
- **Executor rules the brief leaves open** (ATL-143, `trading_core/execution/executor.py`). The
  Executor refuses a risk check whose `settings_version` is not the current one (any settings save
  means the check must be re-run), one that is not the newest check of its proposal, `advise_only`,
  and `awaiting_owner` unless the caller says `approved_by: "owner"`. Client order IDs are derived
  from the risk check id (one letter + its last 31 hex digits: `e` entry, `s` attached SL/TP, `f` bare
  entry after attach was refused, `o` OCO, `c` emergency close), so a retry or restart can never mint a
  second order. The attached algo carries the stop and TP1 for the whole size; splitting TP2/TP3 is
  the Portfolio Manager's job. "Attach refused" means one of `executor.attach_refused_codes` (default
  `51010`). A stop counts as confirmed when an unfilled entry still carries its attached SL with no
  fail code, or a pending SL/OCO algo for it exists on OKX; a partially filled entry is also accepted
  on its attached SL, since OKX may create the algo only at full fill. With no confirmed stop the entry
  is cancelled and whatever filled is closed with a reduce-only market order.
- **Circuit breaker rules the brief leaves open** (ATL-145, `trading_core/risk/breakers.py`). A
  breaker pauses new orders by being active in `trading_state.breakers`; it does not set
  `trading_state.paused`, which stays the owner's/kill switch's flag. Only `data_stale` clears itself
  (after `circuit_breakers.data_stale.clear_after_fresh_seconds` of fresh tickers on every held and
  watchlist instrument, measured from the first 15 s pass that saw them fresh); all others wait for
  the owner's resume. Each trip may carry a cause key (the Baghdad day for `daily_loss`, the ISO week
  for `weekly_loss`, the newest losing close for `consecutive_losses`, instrument + bar for
  `volatility_halt`, agent + Baghdad day for `agent_budget`); after the owner clears it, the same
  cause does not trip it again, so a resume is not undone 15 s later by the loss already
  acknowledged. Risk Engine check 6 still rejects while the loss itself is over the limit. OKX errors
  counted are every error a trade-client call ends in after its own retries (network and HTTP errors
  included) plus refused batch rows, except `circuit_breakers.okx_errors.ignored_codes` (51016 and
  51400-51402, which are normal recovery). `volatility_halt` compares the newest closed 1m log
  return with the standard deviation of the 1m returns before it in the last 24 h (at least 60),
  and only judges a bar that closed within 3 minutes and follows its predecessor with no gap.
  "Agent budget exceeded" is `system.agent_state` with `reason: over_budget`.
- **Orchestrator cycle rules the brief leaves open** (ATL-160, `atlas_agents/orchestrator/cycle.py`).
  Slots run every 30 min from 08:10 while the slot is at or before 23:00 Baghdad (08:10 … 22:40);
  a slot is caught up only inside its own interval, runs once (`cycles` is unique on env + slot_at)
  and never while another cycle runs. The cycle is code, but it is hooked on the `orchestrator` agent,
  which is off by default, so no cycle runs until the owner enables the Orchestrator. A late
  research run is recorded in `cycles.timeouts` and left to finish (its card still serves the next
  cycle); it is not cancelled mid-run. "Degraded" for the no-new-proposals rule means the Technical
  Analyst or the Strategist is not enabled or is `degraded`, `rate_limited` or `paused`. The vote
  closes early once all five voters have voted. A risk-rejected proposal is closed `rejected` (so it
  does not block the instrument for the rest of the day); autonomy 1 gives the new proposal status
  `advise_only` (never an Executor status) even if the Risk Engine said otherwise; `send_now` is
  treated as `awaiting_owner` while autonomy 3–4 are not enabled, so the cycle never calls the
  Executor and the `executing` state is not reached yet. The approval window defaults to 15 minutes
  (`autonomy.approval_timeout_minutes`, overridable by a settings value of the same name).
- **Reconciliation rules the brief leaves open** (ATL-146, `trading_core/execution/reconcile.py`).
  "DB position size" is the sum of the stored fills on the instrument (and posSide in long/short
  mode) since the position's OKX `cTime`; it is compared only for positions opened inside the
  `reconciliation.fills_lookback_days` window the run re-reads, so a position older than that never
  trips `reconciliation_mismatch` on missing history. A position counts as protected when a pending
  conditional/oco algo on its closing side carries an `slTriggerPx` (size coverage is ATL-144's
  job). The missing stop goes at the opening proposal's stop when that is still on the right side
  of the mark; a stop already crossed falls back to the emergency distance
  (`emergency_stop_atr_mult` x ATR(1H), capped at `max_stop_pct`; with no ATR the cap itself), and
  the alert says so. Reconciliation leaves a position alone while an Atlas order on that instrument
  is new/sending or less than `protect_grace_seconds` old (the Executor's own stop check owns it).
  An unsent `new`/`sending` order OKX does not know stays resumable. `/execute` answers 409
  `not_reconciled` until the first run after startup succeeded or found no verified connection.
- **Protection follows the filled size; one stop per entry (ATL-144).** On every fill of an Atlas
  entry the protective SL/OCO is amended (`trade/amend-algos`, size only) to exactly the filled size;
  when OKX shows none, a stand-alone stop `p` + 31 hex of the risk check id is placed, and any second
  protective algo for the same entry is cancelled. A stop that already fired, or one removed on
  purpose (kill-switch flatten, a close order), is never replaced. Limit and post_only entries still
  open `executor.entry_timeout_minutes` after they were created are cancelled; a partial fill keeps
  its stop and the proposal stays `partially_filled`, an unfilled one becomes `expired`.
- **Kill switch reads OKX, not the DB, and resume leaves `data_stale` alone (ATL-147).** The kill lists pending orders, algo orders and positions from OKX so it also catches orders Atlas does not know; per decision #251 it keeps SL/OCO algos unless their position was closed by flatten. OKX's "already gone" answers (51400/51401/51402, 51023) count as done, not failed. Resume clears every breaker except the self-clearing `data_stale`, which would re-trip at once while data is still stale.
- **Position actions get their own risk checks (ATL-184, `trading_core/risk/position_actions.py`).**
  A Portfolio Manager amend_stop/amend_targets/partial_close/close runs seven checks instead of the
  nine entry checks: env_state, position_open, stop_tightens, stop_vs_mark, targets_vs_mark,
  close_size, autonomy. A pause or tripped breaker refuses only amend_targets; stop tightening and
  closes reduce risk and pass. "Tightens" is measured against the tightest live sl/oco algo order on
  the position's side; with no live stop any stop on the safe side of mark passes (it adds
  protection). The position must be in a positions snapshot no older than
  `risk_rules.position_actions.positions_max_age_seconds` (300 s). A proposal side (buy = long) only
  picks between a long and a short in long/short mode; two positions and no side is refused as
  ambiguous. No vote; the autonomy decision is the entries' one. evaluate() leaves the row
  `risk_checked` for both kinds; the caller moves it to awaiting_owner/advise_only/rejected.
- **Orchestrator agent: code classifies and acknowledges, the model only routes** (ATL-161,
  `atlas_agents/orchestrator/classify.py`, `owner.py`, `why.py`, `status.py`, `loops.py`). The Orchestrator is now enabled by
  default, so the ATL-160 analysis cycles run without the owner turning it on. An owner message with
  no @mention (or an @Orch one) is classified by keyword rules; a limit change or an approval is
  decisive and gets a fixed code answer ("Only the owner can change that in Settings → <section>
  (<setting>)…" / "Approvals count only from the Approve / Reject buttons…") with no model run. Every
  other message gets a code acknowledgement at once (its latency is stored in `agent_scores`,
  agent `orchestrator`, `data.ack_latency_seconds`, hit_rate = share within 10 s), then one model
  run answers a JSON routing object and code posts its line and the assignment (an @mention of the
  agent with a deadline, in the owner's thread). §7.9's `open_vote`/`close_vote` are not tools: the
  vote is code. Loop stop counts replies (`reply_to` set) in one thread alternating between the same
  two agents; the 4th gets a code summary (each side's last point) and `cycle.call_vote`, which makes
  a running cycle stop waiting for the Devil's Advocate review and open the vote; a proposal already
  voting is unaffected. "Where we are" is a code card every 2 h from 08:10 to 23:00 Baghdad (08:10 …
  22:10), never after 23:00. Other slash commands than `/why` are left to their own handlers.
- **Executing a position action re-checks against OKX, amends in place, closes before cancelling
  (ATL-185, `trading_core/execution/position_action.py`).** Right before sending, the seven ATL-184
  checks run again on the position and pending algos OKX returns, so a stop that would widen the stop
  OKX holds now is not sent (a `rejected` risk check is written and the proposal is `rejected` /
  `risk_rejected`). amend_stop amends every live SL/OCO on the position in place and places a new SL
  only when OKX shows none; amend_targets moves only the first target (the TP leg of the OCO, else a
  TP-only order), like entries; partial_close sends one reduce-only market order then shrinks every
  larger algo to the remainder; close is `trade/close-position`, then that position's algos are
  cancelled (a refused close cancels nothing). Client ids derive from the risk check (`x` close,
  `n` new stop, `t` new TP). A close order records `raw.reduces_order_id`, and EntryLifecycle sizes
  the entry's protection to filled − reduced, so its sweep keeps the resized stop. Done actions end
  `filled` (a close OKX has not reported filled stays `sent`), which is what the PM reads as
  tp1_filled.
- **Analyst vote runs** (ATL-182, `atlas_agents/orchestrator/voters.py`). When the cycle opens a vote,
  and again for each version revised during the vote, code starts one routine-model run per
  `agents.votes.run_voters` agent (Technical, Derivatives, News, Historian) that is enabled, not in
  `unavailable_states`, inside its daily budget and has not yet voted on that version. The prompt
  holds the proposal card, the agent's newest `voter_cards` card on the instrument and its deadline:
  min(the cycle's vote deadline, run start + `voter_run_timeout_seconds`). The agent must call
  `warroom.cast_vote` itself; code never votes for it. `cast_vote` now refuses (`vote_deadline_passed`)
  a vote after the cycle's `deadlines.vote` or after the agent's run deadline, so a late or missing
  answer leaves the voter missing (skip at 0.5). The vote closes early only when every voter has
  voted and no run on a current version is still going; runs left at the deadline finish on their own.
  The four voters' prompt files gain one VOTING section after the brief's role text (new versions).
- **Portfolio Manager: rules in code first, the model only when one fires (ATL-169).** Every 5 min
  `atlas_agents.pm.manage.tick` runs ATL-168's rules on each open position. No rule → no model call
  and no message. A rule → one run on the strong model with the action prefilled; the model returns
  the action block and code submits it only if it is exactly the computed action and does not widen
  the stop (`atlas_agents.pm.guard`, also enforced inside `warroom.submit_proposal`). A computed action
  the model drops or gets wrong after one retry is still submitted by code (rule compliance is 100%).
  PM proposals skip the vote, get one `/internal/risk/evaluate` and follow autonomy; a refusal
  rejects the proposal with `status_reason = risk_<code>`. A kind already pending for the position is
  not proposed again. Immediate runs: a news alert or fill on a held instrument, a circuit breaker,
  and once 30 min before an importance-4+ event. Limit warnings (70% of the daily/weekly loss or
  open-position limit) are code, at most once per hour per limit.
- **ATL-148: shared api limiter; the kill switch has its own.** Every `/api` route now sits behind
  a per-user `throttle:api` (`system_api.api_per_minute`, 1200). `POST /api/system/pause|resume|kill`
  remove it and use `system-control` (30/min per user), so a flood of other requests can never make
  the kill switch answer 429. Kill takes a fixed `confirm: "KILL"` the sheet sends on Stop (decision
  #251: the owner never types it). The owner's control calls always write one audit row, also when
  trading_core is down (503). "Active env" for these routes and the orders list is
  `atlas_defaults.env.default` until the live switch exists.
- **ATL-170: the 23:30 daily report is code's figures with the model's two notes.**
  `atlas_agents.pm.daily_report` runs at `agents.schedule.pm_daily_report` (23:30 Baghdad) under the
  Portfolio Manager, so it runs only while that agent is enabled. For the Baghdad day up to now:
  realised = Σ bills `pnl`, fees = Σ bills `fee` (OKX sign, negative = paid), funding = Σ `bal_chg` of
  type 8, over USD-like bills only (others are named in `notes`); equity start = the last equity point
  at or before 00:00 Baghdad; change = end − start − the day's net deposits; closed trades are the
  day's `positions_closed`, with R by the History API's formula and the closing rule = an executed PM
  close/partial_close's rule label, else liquidation/ADL, else a triggered stop or target, else
  `manual`. A peak daily loss above `max_daily_loss_pct`, or a peak open-position count above
  `max_open_positions`, is a breach row plus `system.alert` (code `limit_breach`) and a War Room
  `system_alert`. The message is a `research_report` whose card `type` is `daily_report` (no new
  message kind); the model may only write `best_note`/`worst_note`, every other card field is code's.
  Stored as `research_reports` kind `daily_report`; an existing row for the day stops a second post.
