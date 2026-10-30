import { CircleAlert } from 'lucide-react'
import { useContext, useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { normaliseRaw } from './historian'
import { UntracedContext, useUntracedDescId } from './untracedContext'

/*
 * Untraced numbers (design v7 Historian cards §6, brief §7.11): a figure the number guard
 * (ATL-91) did not find in any tool output from the answer is marked with a warning wash, an
 * underline and an icon — never colour alone — and is focusable with an accessible description.
 */

/**
 * Wraps one message (body + card): figures in its text that match `raws` are marked, and the
 * hidden description the marks point at is rendered once inside it.
 */
export function UntracedScope({ raws, children }: { raws: string[]; children: ReactNode }) {
  const { t } = useTranslation('warroom')
  const ownId = useId()
  // A nested scope (the card's key stat) reuses the outer description: its own hidden element would
  // sit inside the text and, being Arabic in ar, turn the paragraph's dir="auto" to RTL.
  const outer = useContext(UntracedContext)
  const descId = outer?.descId ?? ownId
  return (
    <UntracedContext.Provider value={{ raws: raws.map(normaliseRaw), descId }}>
      {!outer && (
        <span id={descId} className="sr-only">
          {t('hv.unv')}
        </span>
      )}
      {children}
    </UntracedContext.Provider>
  )
}

export function UntracedMark({ children }: { children: ReactNode }) {
  const { t } = useTranslation('warroom')
  const descId = useUntracedDescId()
  return (
    <mark className="unv" tabIndex={0} aria-describedby={descId ?? undefined} title={t('hv.unvLong')} data-testid="unv">
      {children}
      <CircleAlert aria-hidden="true" strokeWidth={2} className="unv-ic" />
    </mark>
  )
}
