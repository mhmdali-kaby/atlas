import { createContext, useContext } from 'react'

/* The untraced-number context of one message (Untraced.tsx). */

export interface UntracedValue {
  /** Raw figures from `unmatched_numbers` with location "text", minus normalised to −. */
  raws: string[]
  /** The id of the hidden "Not found in any tool output" element the marks describe themselves by. */
  descId: string
}

export const UntracedContext = createContext<UntracedValue | null>(null)

/** The description every mark in this message points at. */
export function useUntracedDescId(): string | null {
  return useContext(UntracedContext)?.descId ?? null
}

/** True when a figure island in the message body carries one of the untraced raws. */
export function useIsUntraced(): (figure: string) => boolean {
  const ctx = useContext(UntracedContext)
  return (figure) => Boolean(ctx && ctx.raws.some((raw) => raw !== '' && figure.includes(raw)))
}

