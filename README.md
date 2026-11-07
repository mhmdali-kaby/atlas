# Atlas

A local, multi-agent trading platform for OKX that runs on the owner's Mac at `http://localhost:3000`.
Claude agents research and propose trades in a live War Room; a deterministic Risk Engine is the
only path to an order. Demo (paper) account first.

| Folder | What lives there |
| --- | --- |
| `backend/` | Laravel 13 JSON API, Sanctum SPA auth, Reverb, Horizon, scheduler |
| `frontend/` | React 19 + TypeScript + Vite SPA |
| `services/` | Python 3.12 uv workspace: `shared`, `trading_core`, `agents`, `ingest` |
| `docs/` | Decisions, architecture, runbook |

Developer instructions (commands, conventions, rules) are in `CLAUDE.md`.
