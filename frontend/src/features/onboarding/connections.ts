import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, ApiError, useApiQuery } from '../../lib/api'

/** A connection as /api/connections returns it (ATL-4): never a secret, only the key's last 4. */
export interface Connection {
  id: string
  exchange: 'okx'
  env: 'demo' | 'live'
  label: string
  api_key_last4: string
  has_secret: boolean
  has_passphrase: boolean
  /** OKX `perm` split and sorted, e.g. ["read_only", "trade", "withdraw"]; null until tested. */
  permissions: string[] | null
  account_mode: string | null
  position_mode: string | null
  uid_last4: string | null
  last_verified_at: string | null
  /** The error slug of the last failed test, or null. */
  last_test_error: string | null
  created_at: string
  updated_at: string
}

/** POST /api/connections/{id}/test, 200 body (ATL-8, ATL-30). */
export interface ConnectionTestResponse {
  ok: boolean
  acct_lv: string | null
  account_mode: string | null
  position_mode: string | null
  permissions: string[]
  warnings: string[]
  uid_last4: string | null
  error: string | null
  okx_code: string | null
  verified_at: string | null
  message: string | null
  connection: Connection
}

/** What a Test connection press ended in, for the result view. */
export type TestOutcome =
  | { status: 'ok'; connection: Connection; acctLv: string | null; warnings: string[] }
  | { status: 'failed'; error: string; okxCode: string | null; connection?: Connection }

/** The fields the owner types. Secrets live only in component state until they are sent. */
export interface ConnectionValues {
  label: string
  apiKey: string
  secret: string
  passphrase: string
}

export const CONNECTIONS_KEY = ['connections'] as const
export const WITHDRAW_WARNING = 'withdraw_permission_enabled'

export function useConnections() {
  return useApiQuery<{ data: Connection[] }>(CONNECTIONS_KEY, '/api/connections', { staleTime: 30_000 })
}

/** The OKX connection for one environment, or null when there is none. */
export function useConnection(env: Connection['env'] = 'demo') {
  const query = useConnections()
  const connection = query.data?.data.find((c) => c.exchange === 'okx' && c.env === env) ?? null
  return { ...query, connection }
}

export const isVerified = (c: Connection | null | undefined): c is Connection => !!c && c.last_verified_at !== null

/**
 * A verified demo key that can withdraw: accepted in demo with a warning (decision 240). It is read
 * from the stored permissions, so the notice survives a reload and is never a toast.
 */
export function canWithdraw(c: Connection | null | undefined, warnings: readonly string[] = []): boolean {
  return warnings.includes(WITHDRAW_WARNING) || (!!c && (c.permissions ?? []).includes('withdraw'))
}

/** Error slug from a non-200 test or save (503 trading_core_unavailable, 502 trading_core_error…). */
function errorSlug(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body as { error?: unknown } | null
    if (body && typeof body.error === 'string') return body.error
    if (err.network) return 'trading_core_unavailable'
  }
  return 'unknown'
}

export class ConnectionValidationError extends Error {
  readonly fields: Partial<Record<keyof ConnectionValues, true>>
  constructor(fields: Partial<Record<keyof ConnectionValues, true>>) {
    super('validation')
    this.fields = fields
  }
}

const FIELD_FROM_API: Record<string, keyof ConnectionValues> = {
  label: 'label',
  api_key: 'apiKey',
  api_secret: 'secret',
  passphrase: 'passphrase',
}

/**
 * Saves the keys (POST when there is no connection yet, PATCH when there is) and tests them.
 * The response bodies never carry a secret; the cache gets the fresh connection either way.
 */
export function useSaveAndTest(env: Connection['env'] = 'demo') {
  const client = useQueryClient()

  const store = (connection: Connection) =>
    client.setQueryData<{ data: Connection[] }>(CONNECTIONS_KEY, (old) => {
      const rest = (old?.data ?? []).filter((c) => c.id !== connection.id)
      return { data: [...rest, connection] }
    })

  return useMutation<TestOutcome, unknown, { existing: Connection | null; values: ConnectionValues }>({
    mutationFn: async ({ existing, values }) => {
      const payload = {
        label: values.label.trim(),
        api_key: values.apiKey.trim(),
        api_secret: values.secret.trim(),
        passphrase: values.passphrase.trim(),
      }
      let saved: Connection
      try {
        saved = existing
          ? (await api.patch<{ data: Connection }>(`/api/connections/${existing.id}`, payload)).data
          : (await api.post<{ data: Connection }>('/api/connections', { env, ...payload })).data
      } catch (err) {
        if (err instanceof ApiError && err.status === 422) {
          const errors = ((err.body as { errors?: Record<string, unknown> } | null)?.errors ?? {}) as Record<string, unknown>
          const fields: Partial<Record<keyof ConnectionValues, true>> = {}
          for (const name of Object.keys(errors)) {
            const field = FIELD_FROM_API[name]
            if (field) fields[field] = true
          }
          if (Object.keys(fields).length > 0) throw new ConnectionValidationError(fields)
        }
        return { status: 'failed', error: errorSlug(err), okxCode: null }
      }
      store(saved)

      try {
        const result = await api.post<ConnectionTestResponse>(`/api/connections/${saved.id}/test`)
        store(result.connection)
        return result.ok
          ? { status: 'ok', connection: result.connection, acctLv: result.acct_lv, warnings: result.warnings ?? [] }
          : { status: 'failed', error: result.error ?? 'unknown', okxCode: result.okx_code, connection: result.connection }
      } catch (err) {
        return { status: 'failed', error: errorSlug(err), okxCode: null, connection: saved }
      }
    },
  })
}
