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
- **Tests run on real Postgres** (`atlas_test`), never SQLite: jsonb, arrays and hypertables behave
  differently there.
- **Redis client is predis** (pure PHP), so no PHP extension is needed on the Mac.
