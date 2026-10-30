import { CircleAlert } from 'lucide-react'
import { createContext, useContext, useId, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { normaliseRaw } from './historian'

/*
 * Untraced numbers (design v7 Historian cards §6, brief §7.11): a figure the number guard
 * (ATL-91) did not find in any tool output from the answer is marked with a warning wash, an
 * underline and an icon — never colour alone — and is focusable with an accessible description.
 */

interface UntracedValue {
  /** Raw figures from `unmatched_numbers` with location "text", minus normalised to −. */
  raws: string[]
  /** The id of the hidden "Not found in any tool output" element the marks describe themselves by. */
  descId: string
}

const UntracedContext = createContext<UntracedValue | null>(null)

/** The description every mark in this message points at. */
export function useUntracedDescId(): string | null {
  return useContext(UntracedContext)?.descId ?? null
}

/** True when a figure island in the message body carries one of the untraced raws. */
export function useIsUntraced(): (figure: string) => boolean {
  const ctx = useContext(UntracedContext)
  return (figure) => Boolean(ctx && ctx.raws.some((raw) => raw !== '' && figure.includes(raw)))
}

/**
 * Wraps one message (body + card): figures in its text that match `raws` are marked, and the
 * hidden description the marks point at is rendered once inside it.
 */
export function UntracedScope({ raws, children }: { raws: string[]; children: ReactNode }) {
  const { t } = useTranslation('warroom')
  const descId = useId()
  return (
    <UntracedContext.Provider value={{ raws: raws.map(normaliseRaw), descId }}>
      <span id={descId} className="sr-only">
        {t('hv.unv')}
      </span>
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
