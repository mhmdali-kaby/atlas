import { ApiError } from '../../lib/api'

/** The message the API gave for a failed cancel: Laravel/trading_core `message`, `detail`, or the first failed item's. */
export function cancelErrorMessage(err: unknown, fallback: string): string {
  if (!(err instanceof ApiError) || err.network) return fallback
  const body = err.body as
    | { message?: unknown; detail?: unknown; failed?: { message?: unknown; detail?: unknown }[] }
    | null
  const pick = (v: unknown) => (typeof v === 'string' && v.trim() !== '' ? v : null)
  const failed = Array.isArray(body?.failed) ? body.failed[0] : undefined
  return pick(body?.message) ?? pick(body?.detail) ?? pick(failed?.message) ?? pick(failed?.detail) ?? fallback
}
