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
/**
 * Set while the owner signs out on purpose (the shell's Sign out), so the guard sends
 * them to a plain /login instead of /login?next=<the page they left>.
 */
export const SIGNED_OUT_KEY = ['auth', 'signed-out'] as const

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

/**
 * Guest GET /api/auth/status: whether `./atlas create-owner` has run yet, and (ATL-246) whether
 * this session holds an unexpired two-factor challenge, so a reload reopens the code step.
 */
export interface AuthStatus {
  owner_exists: boolean
  two_factor_pending?: boolean
}

export function useAuthStatus(enabled = true) {
  return useQuery<AuthStatus, ApiError>({
    queryKey: AUTH_STATUS_KEY,
    queryFn: ({ signal }) => api.get<AuthStatus>('/api/auth/status', { signal }),
    retry: false,
    enabled,
  })
}

export interface Credentials {
  email: string
  password: string
}

/**
 * What POST /api/auth/login answered: signed in, or (ATL-246) the password was right but the
 * owner has two-factor sign-in on, so nobody is signed in until the code step passes.
 */
export type LoginResult = { kind: 'signed_in'; user: SessionUser } | { kind: 'two_factor' }

export function useLogin() {
  const client = useQueryClient()
  return useMutation<LoginResult, ApiError, Credentials>({
    mutationFn: async (credentials) => {
      const res = await api.post<{ data: SessionUser } | { two_factor: true }>('/api/auth/login', credentials)
      return 'two_factor' in res && res.two_factor ? { kind: 'two_factor' } : { kind: 'signed_in', user: (res as { data: SessionUser }).data }
    },
    onSuccess: (result) => {
      if (result.kind === 'two_factor') {
        client.setQueryData<AuthStatus>(AUTH_STATUS_KEY, (s) => ({ owner_exists: true, ...s, two_factor_pending: true }))
        return
      }
      client.setQueryData(SIGNED_OUT_KEY, false)
      client.setQueryData(SESSION_KEY, result.user)
    },
  })
}

/** The code step's answer: a 6-digit code from the authenticator app, or one recovery code. */
export type ChallengeAnswer = { code: string } | { recovery_code: string }

/** The `error` key of a challenge 422: a wrong code, or no live challenge (expired after 5 min). */
export function challengeErrorKind(err: ApiError): 'code_wrong' | 'challenge_expired' | null {
  if (err.status !== 422) return null
  const kind = (err.body as { error?: string } | null)?.error
  return kind === 'challenge_expired' ? 'challenge_expired' : 'code_wrong'
}

/** Seconds from a 429's Retry-After, or a minute when it is missing. */
export function retryAfterSeconds(err: ApiError): number {
  const header = Number(err.headers?.get('Retry-After'))
  return Number.isFinite(header) && header > 0 ? Math.ceil(header) : 60
}

/** POST /api/auth/two-factor-challenge: on success the owner is signed in, exactly as after /login. */
export function useTwoFactorChallenge() {
  const client = useQueryClient()
  return useMutation<SessionUser, ApiError, ChallengeAnswer>({
    mutationFn: async (answer) => (await api.post<{ data: SessionUser }>('/api/auth/two-factor-challenge', answer)).data,
    onSuccess: (user) => {
      client.setQueryData<AuthStatus>(AUTH_STATUS_KEY, (s) => ({ owner_exists: true, ...s, two_factor_pending: false }))
      client.setQueryData(SIGNED_OUT_KEY, false)
      client.setQueryData(SESSION_KEY, user)
    },
    onError: (err) => {
      if (challengeErrorKind(err) === 'challenge_expired')
        client.setQueryData<AuthStatus>(AUTH_STATUS_KEY, (s) => ({ owner_exists: true, ...s, two_factor_pending: false }))
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
