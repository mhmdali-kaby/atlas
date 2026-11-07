import { Fragment, type ReactNode } from 'react'

/*
 * Slot filling for translated templates whose values are React nodes. Untrusted values never go
 * through <Trans>, which parses its interpolated string for tags: a source name like
 * "<strong>x</strong>" would become markup. The template is translated with private-use slot
 * markers (U+E000) and split around them; the values are placed as React children.
 */

const MARK = ''
const SLOT_RE = /([a-z]+)/g

/** The slot marker passed to t() for a value that fillSlots replaces. */
export const slot = (name: string) => `${MARK}${name}${MARK}`

/** A translated template split around slot() markers, each placed as the React node named in `nodes`. */
export function fillSlots(template: string, nodes: Record<string, ReactNode>): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of template.matchAll(SLOT_RE)) {
    if (m.index > last) out.push(<Fragment key={`t${last}`}>{template.slice(last, m.index)}</Fragment>)
    out.push(<Fragment key={`s${m.index}`}>{nodes[m[1]!] ?? null}</Fragment>)
    last = m.index + m[0].length
  }
  if (last < template.length) out.push(<Fragment key={`t${last}`}>{template.slice(last)}</Fragment>)
  return out
}
