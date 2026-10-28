import {
  useMutation,
  useQuery,
  type QueryKey,
  type UseMutationOptions,
  type UseQueryOptions,
} from '@tanstack/react-query'

/*
 * The one way the SPA talks to Laravel (brief §16, §18; ATL-2's Sanctum SPA contract).
 *
 * - Every request sends the session cookie (credentials: 'include') and asks for JSON.
 * - Before the first unsafe request (POST/PUT/PATCH/DELETE) it calls /sanctum/csrf-cookie
 *   once, then sends the URL-decoded XSRF-TOKEN cookie as X-XSRF-TOKEN. The cookie is read
 *   fresh per request because login and logout rotate it.
 * - 419 (CSRF mismatch) → one /sanctum/csrf-cookie refetch and one retry.
 * - 401 (no or expired session) → an `atlas:auth-expired` window event, then ApiError.
 */

export const API_URL: string = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/+$/, '')

export const AUTH_EXPIRED_EVENT = 'atlas:auth-expired'

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

/** A non-2xx response (status ≥ 400) or a network failure (status 0, `network: true`). */
export class ApiError extends Error {
  readonly status: number
  /** Parsed JSON body, if there was one. */
  readonly body: unknown
  readonly headers: Headers | null
  readonly network: boolean

  constructor(status: number, message: string, body: unknown = null, headers: Headers | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
    this.headers = headers
    this.network = status === 0
  }

  /** Laravel 422 field errors: `{ errors: { field: [message] } }`. */
  get fieldErrors(): Record<string, string[]> {
    const body = this.body as { errors?: Record<string, string[]> } | null
    return body?.errors ?? {}
  }
}

export interface ApiRequestInit extends Omit<RequestInit, 'body' | 'credentials'> {
  /** Sent as JSON. */
  json?: unknown
}

let csrfReady: Promise<void> | null = null

/** Reads a cookie by name, URL-decoded (Laravel URL-encodes XSRF-TOKEN). */
function readCookie(name: string): string | null {
  const prefix = `${name}=`
  for (const part of document.cookie.split(';')) {
    const trimmed = part.trim()
    if (trimmed.startsWith(prefix)) return decodeURIComponent(trimmed.slice(prefix.length))
  }
  return null
}

async function fetchCsrfCookie(): Promise<void> {
  let res: Response
  try {
    res = await fetch(`${API_URL}/sanctum/csrf-cookie`, {
      credentials: 'include',
      headers: { Accept: 'application/json' },
    })
  } catch {
    throw new ApiError(0, 'Network error')
  }
  if (!res.ok) throw new ApiError(res.status, `CSRF cookie request failed (${res.status})`)
}

/** Once per page load (or after a 419), before the first unsafe request. */
function ensureCsrf(force = false): Promise<void> {
  if (force || csrfReady === null) {
    csrfReady = fetchCsrfCookie().catch((err: unknown) => {
      csrfReady = null
      throw err
    })
  }
  return csrfReady
}

/** Tests only: forget that the CSRF cookie was fetched. */
export function resetCsrfForTests(): void {
  csrfReady = null
}

async function send(path: string, init: ApiRequestInit, method: string): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  let body: BodyInit | undefined
  if (init.json !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(init.json)
  }
  if (UNSAFE.has(method)) {
    const token = readCookie('XSRF-TOKEN')
    if (token !== null) headers.set('X-XSRF-TOKEN', token)
  }
  const { json: _json, ...rest } = init
  try {
    return await fetch(`${API_URL}${path}`, { ...rest, method, headers, body, credentials: 'include' })
  } catch {
    throw new ApiError(0, 'Network error')
  }
}

async function parseBody(res: Response): Promise<unknown> {
  if (res.status === 204) return null
  const text = await res.text()
  if (text === '') return null
  try {
    return JSON.parse(text) as unknown
  } catch {
    return text
  }
}

/** Calls the Laravel API. Resolves with the parsed JSON body (null for 204). */
export async function apiFetch<T = unknown>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const unsafe = UNSAFE.has(method)

  if (unsafe) await ensureCsrf()
  let res = await send(path, init, method)

  if (res.status === 419 && unsafe) {
    await ensureCsrf(true)
    res = await send(path, init, method)
  }

  const body = await parseBody(res)
  if (res.ok) return body as T

  if (res.status === 401) window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: { path } }))
  const message =
    typeof body === 'object' && body !== null && typeof (body as { message?: unknown }).message === 'string'
      ? (body as { message: string }).message
      : `Request failed (${res.status})`
  throw new ApiError(res.status, message, body, res.headers)
}

export const api = {
  get: <T>(path: string, init?: ApiRequestInit) => apiFetch<T>(path, { ...init, method: 'GET' }),
  post: <T>(path: string, json?: unknown, init?: ApiRequestInit) => apiFetch<T>(path, { ...init, method: 'POST', json }),
  put: <T>(path: string, json?: unknown, init?: ApiRequestInit) => apiFetch<T>(path, { ...init, method: 'PUT', json }),
  patch: <T>(path: string, json?: unknown, init?: ApiRequestInit) => apiFetch<T>(path, { ...init, method: 'PATCH', json }),
  delete: <T>(path: string, init?: ApiRequestInit) => apiFetch<T>(path, { ...init, method: 'DELETE' }),
}

/** Subscribes to 401s from any request. Returns the unsubscribe function. */
export function onAuthExpired(listener: () => void): () => void {
  window.addEventListener(AUTH_EXPIRED_EVENT, listener)
  return () => window.removeEventListener(AUTH_EXPIRED_EVENT, listener)
}

/** TanStack Query over a GET. `path` is relative to the API root, e.g. `/api/portfolio/summary`. */
export function useApiQuery<T>(
  key: QueryKey,
  path: string,
  options: Omit<UseQueryOptions<T, ApiError>, 'queryKey' | 'queryFn'> = {},
) {
  return useQuery<T, ApiError>({
    queryKey: key,
    queryFn: ({ signal }) => api.get<T>(path, { signal }),
    ...options,
  })
}

/** TanStack Query mutation over an unsafe request with a JSON body. */
export function useApiMutation<TData = unknown, TVariables = void>(
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string | ((variables: TVariables) => string),
  options: Omit<UseMutationOptions<TData, ApiError, TVariables>, 'mutationFn'> = {},
) {
  return useMutation<TData, ApiError, TVariables>({
    mutationFn: (variables) =>
      apiFetch<TData>(typeof path === 'function' ? path(variables) : path, {
        method,
        json: variables === undefined || variables === null ? undefined : variables,
      }),
    ...options,
  })
}
