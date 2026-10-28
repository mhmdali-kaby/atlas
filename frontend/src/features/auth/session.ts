import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api, ApiError, onAuthExpired } from '../../lib/api'

/** The owner, as GET /api/auth/me and POST /api/auth/login return it (ATL-2). */
export interface SessionUser {
  id: string
  name: string
  email: string
  locale: 'en' | 'ar'
  theme: 'system' | 'light' | 'dark'
}

export const SESSION_KEY = ['auth', 'me'] as const
export const AUTH_STATUS_KEY = ['auth', 'status'] as const

/** Brief §18: lock after 15 minutes without input. */
export const IDLE_LOCK_MS = 15 * 60 * 1000

/** Where the login page sends the owner when nothing else was requested. */
export const HOME_PATH = '/'

/**
 * The signed-in owner, or `null` when there is no session (401).
 * Any other failure (server down, 500) is an error, not a sign-out.
 */
export function useSession() {
  return useQuery<SessionUser | null, ApiError>({
    queryKey: SESSION_KEY,
    queryFn: async ({ signal }) => {
      try {
        const res = await api.get<{ data: SessionUser }>('/api/auth/me', { signal })
        return res.data
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null
        throw err
      }
    },
    retry: false,
    staleTime: 60_000,
  })
}

/** Guest GET /api/auth/status: whether `./atlas create-owner` has run yet. */
export function useAuthStatus(enabled = true) {
  return useQuery<{ owner_exists: boolean }, ApiError>({
    queryKey: AUTH_STATUS_KEY,
    queryFn: ({ signal }) => api.get<{ owner_exists: boolean }>('/api/auth/status', { signal }),
    retry: false,
    enabled,
  })
}

export interface Credentials {
  email: string
  password: string
}

export function useLogin() {
  const client = useQueryClient()
  return useMutation<SessionUser, ApiError, Credentials>({
    mutationFn: async (credentials) => (await api.post<{ data: SessionUser }>('/api/auth/login', credentials)).data,
    onSuccess: (user) => {
      client.setQueryData(SESSION_KEY, user)
    },
  })
}

/**
 * Ends the session on the server, then forgets every cached query so the next
 * owner sees nothing of the last one. A failed request still signs out locally.
 */
export function useLogout() {
  const client = useQueryClient()
  return useMutation<void, ApiError, void>({
    mutationFn: async () => {
      try {
        await api.post<null>('/api/auth/logout')
      } catch (err) {
        if (!(err instanceof ApiError)) throw err
      }
    },
    onSettled: () => {
      client.removeQueries({ predicate: (q) => q.queryKey[0] !== SESSION_KEY[0] })
      client.setQueryData(SESSION_KEY, null)
    },
  })
}

/** Any 401 from any request marks the session gone, which sends the shell to /login. */
export function useAuthExpiredListener() {
  const client = useQueryClient()
  useEffect(() => onAuthExpired(() => client.setQueryData(SESSION_KEY, null)), [client])
}

/** A `next` value from the URL, accepted only as a same-origin path. */
export function safeNextPath(next: string | null): string {
  if (next === null || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return HOME_PATH
  if (next === '/login' || next.startsWith('/login?')) return HOME_PATH
  return next
}

/** Builds /login?next=<requested path>[&reason=idle]. */
export function loginPath(next: string, reason?: 'idle'): string {
  const params = new URLSearchParams()
  if (next !== '/' && !next.startsWith('/login')) params.set('next', next)
  if (reason) params.set('reason', reason)
  const query = params.toString()
  return query ? `/login?${query}` : '/login'
}
