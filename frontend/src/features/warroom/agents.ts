import { Binoculars, Briefcase, ChartCandlestick, ChessKnight, Flame, Gauge, Library, Newspaper, Workflow, type LucideIcon } from 'lucide-react'

/*
 * The nine agents (design v4 War Room §5), in the brief's preamble order. Static until the agent
 * runtime exists. `handle` is what an @mention types (GET /api/warroom/agents maps it to the
 * slug); names, short names and role lines are translated in warroom.json under agents.<slug>.
 */

export type AgentStatus = 'offline' | 'idle' | 'thinking' | 'waiting' | 'degraded' | 'paused'

export interface AgentDef {
  slug: string
  handle: string
  /** Extra mention spellings the API also resolves (defaults.yaml warroom.agents). */
  aliases: string[]
  glyph: LucideIcon
  /** CSS custom property holding the ring colour. */
  ring: string
  /** A short name exists for the narrow sidebar (agents.<slug>.short). */
  hasShort: boolean
}

export const AGENTS: readonly AgentDef[] = [
  { slug: 'orchestrator', handle: 'Orchestrator', aliases: ['Orch'], glyph: Workflow, ring: '--ring-orchestrator', hasShort: false },
  { slug: 'market_scout', handle: 'Scout', aliases: ['MarketScout'], glyph: Binoculars, ring: '--ring-market-scout', hasShort: false },
  { slug: 'news_intelligence', handle: 'News', aliases: ['NewsIntel', 'NewsIntelligence'], glyph: Newspaper, ring: '--ring-news-intelligence', hasShort: false },
  { slug: 'historian', handle: 'Historian', aliases: [], glyph: Library, ring: '--ring-historian', hasShort: false },
  { slug: 'technical_analyst', handle: 'Technical', aliases: ['TA', 'TechnicalAnalyst'], glyph: ChartCandlestick, ring: '--ring-technical-analyst', hasShort: false },
  { slug: 'derivatives_sentiment', handle: 'Derivatives', aliases: ['Sentiment'], glyph: Gauge, ring: '--ring-derivatives-sentiment', hasShort: true },
  { slug: 'strategist', handle: 'Strategist', aliases: [], glyph: ChessKnight, ring: '--ring-strategist', hasShort: false },
  { slug: 'devils_advocate', handle: 'Devil', aliases: ['DA', 'DevilsAdvocate'], glyph: Flame, ring: '--ring-devils-advocate', hasShort: false },
  { slug: 'portfolio_manager', handle: 'PM', aliases: ['Portfolio', 'PortfolioManager'], glyph: Briefcase, ring: '--ring-portfolio-manager', hasShort: false },
]

const BY_SLUG = new Map(AGENTS.map((a) => [a.slug, a]))

export function agentBySlug(slug: string): AgentDef | undefined {
  return BY_SLUG.get(slug)
}

/** The agent a typed @handle names: its handle, slug or an alias, case-insensitively. */
export function agentByHandle(handle: string): AgentDef | undefined {
  const h = handle.toLowerCase()
  return AGENTS.find(
    (a) => a.handle.toLowerCase() === h || a.slug.replace(/_/g, '') === h.replace(/_/g, '') || a.aliases.some((x) => x.toLowerCase() === h),
  )
}

/** Until the agent runtime (a later patch), every agent is offline. */
export function agentStatus(_slug: string): AgentStatus {
  return 'offline'
}

/** Agents whose handle starts with the query, or whose name contains it (case-insensitive). */
export function filterAgents(query: string, nameOf: (a: AgentDef) => string): AgentDef[] {
  const q = query.toLowerCase()
  if (!q) return [...AGENTS]
  return AGENTS.filter((a) => a.handle.toLowerCase().startsWith(q) || nameOf(a).toLowerCase().includes(q))
}
