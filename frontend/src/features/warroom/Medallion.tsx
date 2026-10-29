import type { CSSProperties } from 'react'
import { AppIcon } from '../../ui'
import { agentBySlug, type AgentStatus } from './agents'

/*
 * AgentMedallion (design v4 War Room §7): a monochrome Lucide glyph on a neutral disc inside a
 * 1.5px ring in the agent's colour — the only place that colour appears. Offline mutes the ring;
 * thinking opens it into a turning arc. The status dot is drawn only where `dot` is set (sidebar).
 */
export function Medallion({
  slug,
  size,
  status = 'offline',
  dot = false,
}: {
  slug: string
  size: 22 | 26 | 28 | 30 | 32 | 40 | 44
  status?: AgentStatus
  dot?: boolean
}) {
  const agent = agentBySlug(slug)
  if (!agent) return <SystemMedallion size={size} />
  const Glyph = agent.glyph
  const style = { '--s': `${size}px`, '--ring': `var(${agent.ring})` } as CSSProperties
  const r = size / 2 - 0.75
  return (
    <span className="medal" data-status={status} style={style} aria-hidden="true">
      <svg className="medal-ring" viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={r} pathLength={1} />
      </svg>
      <span className="medal-disc">
        <Glyph strokeWidth={1.5} className="medal-glyph" />
      </span>
      {dot && <span className="st" data-status={status} />}
    </span>
  )
}

/** System messages use Atlas's app icon at the same size. */
export function SystemMedallion({ size }: { size: number }) {
  return (
    <span className="medal is-atlas" style={{ '--s': `${size}px` } as CSSProperties} aria-hidden="true">
      <AppIcon size={size - 2} />
    </span>
  )
}
