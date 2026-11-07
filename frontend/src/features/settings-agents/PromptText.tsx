import { ChevronsUpDown } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed } from '../../lib/format'
import { cx } from '../../ui/cx'
import { useLang } from '../portfolio/hooks'
import { counts, diffRows, hunks, linesOf, type DiffLine } from './prompts'

/*
 * The prompt text (design v22 §7 "Text pane"): numbered, monospace, read-only, scrolling, and
 * always dir="ltr" lang="en" because the prompts are English, also inside Arabic pages.
 */

const ZWSP = '​'

export function PromptReader({ body }: { body: string }) {
  const lines = useMemo(() => linesOf(body), [body])
  return (
    <div className="pmt-text" dir="ltr" lang="en" data-testid="prompt-text" tabIndex={0}>
      <ol className="pmt-lines">
        {lines.map((l, i) => (
          <li key={i}>
            <span className="pmt-ln" aria-hidden="true">
              {i + 1}
            </span>
            <span className="pmt-l">{l || ZWSP}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}

function Row({ line }: { line: DiffLine }) {
  const { t } = useTranslation('settings-agents')
  return (
    <li className={cx('pmt-d', `is-${line.kind}`)} data-kind={line.kind}>
      <span className="pmt-ln">{line.old ?? ''}</span>
      <span className="pmt-ln">{line.new ?? ''}</span>
      <span className="pmt-sign" aria-hidden="true" data-testid="diff-sign">
        {line.kind === 'add' ? '+' : line.kind === 'rem' ? '−' : ''}
      </span>
      <span className="pmt-l">
        {line.kind !== 'ctx' && <span className="sr-only">{t(line.kind === 'add' ? 'pr.addedSr' : 'pr.removedSr')} </span>}
        {line.text || ZWSP}
      </span>
    </li>
  )
}

/** The counts line above a diff: "4 lines added  1 line removed". */
export function DiffCounts({ from, to }: { from: string; to: string }) {
  const { t } = useTranslation('settings-agents')
  const lang = useLang()
  const c = useMemo(() => counts(diffRows(from, to)), [from, to])
  if (!c.add && !c.rem) return null
  return (
    <p className="pmt-counts" data-testid="diff-counts">
      {c.add > 0 && <span className="pmt-add">{t('pr.added', { count: c.add, n: formatFixed(c.add, 0, lang) })}</span>}
      {c.rem > 0 && <span className="pmt-rem">{t('pr.removed', { count: c.rem, n: formatFixed(c.rem, 0, lang) })}</span>}
    </p>
  )
}

/** A unified line diff, three lines of context, longer unchanged runs folded. */
export function PromptDiff({ from, to }: { from: string; to: string }) {
  const { t } = useTranslation('settings-agents')
  const lang = useLang()
  const blocks = useMemo(() => hunks(diffRows(from, to)), [from, to])
  const [open, setOpen] = useState<ReadonlySet<number>>(new Set())
  if (!blocks.some((b) => b.lines.some((l) => l.kind !== 'ctx'))) return <p className="pmt-same">{t('pr.same')}</p>
  return (
    <div className="pmt-text is-diff" dir="ltr" lang="en" data-testid="prompt-diff" tabIndex={0}>
      <ol className="pmt-difflines">
        {blocks.flatMap((b) =>
          b.type === 'lines' || open.has(b.start)
            ? b.lines.map((l, i) => <Row key={b.start + i} line={l} />)
            : [
                <li key={`f${b.start}`} className="pmt-fold">
                  <button type="button" className="pmt-fold-btn" lang={lang} onClick={() => setOpen((s) => new Set(s).add(b.start))}>
                    <ChevronsUpDown aria-hidden="true" strokeWidth={2} />
                    <span dir="auto">{t('pr.unchanged', { count: b.lines.length, n: formatFixed(b.lines.length, 0, lang) })}</span>
                  </button>
                </li>,
              ],
        )}
      </ol>
    </div>
  )
}
