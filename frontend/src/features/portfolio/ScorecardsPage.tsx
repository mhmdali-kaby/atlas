import { ScorecardsView } from '../agents/scorecards'

/*
 * Portfolio → Scorecards (ATL-204; brief §13 "Agent scorecards (also on the Agents page)",
 * decision 253 (patch K); design v16 §6). Reuses ATL-201's ScorecardsView body: the same
 * GET /api/agents/scores?days=7|30|90, no vote-weight panel, and each card shown with
 * `{ pnl: true }` so it adds "P&L of trades it led" when the API sends `pnl_attributed_usd`
 * (ATL-204 wires that figure to the same closed trades as Portfolio → Performance's
 * By-agent breakdown). Uses the shell's own full-width page frame, like every Portfolio page.
 */
export function PortfolioScorecardsPage() {
  return <ScorecardsView portfolio />
}
