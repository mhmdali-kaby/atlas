import { useQueryClient } from '@tanstack/react-query'
import { CONNECTIONS_KEY, type Connection } from '../onboarding'

/** A failed test as this page shows it: the error slug and OKX's code, when there is one. */
export type Failure = { error: string; okxCode: string | null }

/** Slugs that mean Atlas could not run the test at all, as opposed to OKX refusing the key. */
export const SERVICE_ERRORS = new Set(['trading_core_unavailable', 'trading_core_error'])

/** A live key passes only when OKX tested it and listed no withdraw (ATL-235, the pre-flight's rule). */
export const isLiveVerified = (c: Connection) => c.last_verified_at !== null && !!c.withdraw_off_verified_at

type ConnectionsList = { data: Connection[] }

/** Writes a tested connection into, or drops a removed one from, the ['connections'] cache. */
export function useStoreConnection() {
  const client = useQueryClient()
  return {
    put: (connection: Connection) =>
      client.setQueryData<ConnectionsList>(CONNECTIONS_KEY, (old) => ({
        data: [...(old?.data ?? []).filter((c) => c.id !== connection.id), connection],
      })),
    drop: (id: string) =>
      client.setQueryData<ConnectionsList>(CONNECTIONS_KEY, (old) => ({ data: (old?.data ?? []).filter((c) => c.id !== id) })),
  }
}
