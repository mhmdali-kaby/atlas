import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { API_URL, ApiError, AUTH_EXPIRED_EVENT, api, resetCsrfForTests } from './api'

type Handler = (url: string, init: RequestInit) => Response

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

function mockFetch(handler: Handler) {
  const fn = vi.fn((input: RequestInfo | URL, init: RequestInit = {}) => Promise.resolve(handler(String(input), init)))
  vi.stubGlobal('fetch', fn)
  return fn
}

function urls(fn: ReturnType<typeof mockFetch>): string[] {
  return fn.mock.calls.map(([u]) => String(u).replace(API_URL, ''))
}

function clearCookies() {
  for (const c of document.cookie.split(';')) {
    const name = c.split('=')[0]?.trim()
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
  }
}

beforeEach(() => {
  resetCsrfForTests()
  clearCookies()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('apiFetch', () => {
  it('defaults the base URL to http://localhost:8000', () => {
    expect(API_URL).toBe('http://localhost:8000')
  })

  it('sends credentials and Accept: application/json on a GET, with no CSRF call', async () => {
    const fetch = mockFetch(() => json(200, { data: { ok: true } }))
    await expect(api.get('/api/auth/me')).resolves.toEqual({ data: { ok: true } })
    expect(urls(fetch)).toEqual(['/api/auth/me'])
    const init = fetch.mock.calls[0]![1]!
    expect(init.credentials).toBe('include')
    expect(new Headers(init.headers).get('Accept')).toBe('application/json')
  })

  it('fetches /sanctum/csrf-cookie once before the first unsafe request and sends the decoded XSRF token', async () => {
    const fetch = mockFetch((url) => {
      if (url.endsWith('/sanctum/csrf-cookie')) {
        document.cookie = 'XSRF-TOKEN=abc%3D%3Dxyz; path=/'
        return new Response(null, { status: 204 })
      }
      return json(200, { ok: 1 })
    })
    await api.post('/api/auth/login', { email: 'a@b.c', password: 'x' })
    await api.post('/api/auth/logout')
    expect(urls(fetch)).toEqual(['/sanctum/csrf-cookie', '/api/auth/login', '/api/auth/logout'])
    const headers = new Headers(fetch.mock.calls[1]![1]!.headers)
    expect(headers.get('X-XSRF-TOKEN')).toBe('abc==xyz')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(fetch.mock.calls[1]![1]!.body).toBe(JSON.stringify({ email: 'a@b.c', password: 'x' }))
  })

  it('on a 419 refetches the CSRF cookie exactly once and retries the POST exactly once', async () => {
    let posts = 0
    const fetch = mockFetch((url) => {
      if (url.endsWith('/sanctum/csrf-cookie')) {
        document.cookie = `XSRF-TOKEN=token-${fetch.mock.calls.length}; path=/`
        return new Response(null, { status: 204 })
      }
      posts += 1
      return posts === 1 ? json(419, { message: 'CSRF token mismatch.' }) : json(200, { saved: true })
    })
    // First unsafe request of the page already fetched the cookie once.
    await api.post('/api/settings', {})
    fetch.mockClear()
    posts = 0

    await expect(api.post('/api/settings', { a: 1 })).resolves.toEqual({ saved: true })
    expect(urls(fetch)).toEqual(['/api/settings', '/sanctum/csrf-cookie', '/api/settings'])
    expect(new Headers(fetch.mock.calls[2]![1]!.headers).get('X-XSRF-TOKEN')).toBe('token-2')
  })

  it('a second 419 after the retry is an error, with no further retries', async () => {
    const fetch = mockFetch((url) =>
      url.endsWith('/sanctum/csrf-cookie') ? new Response(null, { status: 204 }) : json(419, { message: 'CSRF token mismatch.' }),
    )
    await expect(api.post('/api/x')).rejects.toMatchObject({ status: 419 })
    expect(urls(fetch)).toEqual(['/sanctum/csrf-cookie', '/api/x', '/sanctum/csrf-cookie', '/api/x'])
  })

  it('maps a 401 to the auth-expired event and an ApiError', async () => {
    mockFetch(() => json(401, { message: 'Unauthenticated.' }))
    const listener = vi.fn()
    window.addEventListener(AUTH_EXPIRED_EVENT, listener)
    const error = await api.get('/api/portfolio/summary').catch((e: unknown) => e)
    window.removeEventListener(AUTH_EXPIRED_EVENT, listener)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(401)
    expect((error as ApiError).message).toBe('Unauthenticated.')
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('exposes 422 field errors and network failures', async () => {
    mockFetch(() => json(422, { message: 'bad', errors: { email: ['bad'] } }))
    const e422 = (await api.get('/api/x').catch((e: unknown) => e)) as ApiError
    expect(e422.fieldErrors).toEqual({ email: ['bad'] })

    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))
    const net = (await api.get('/api/x').catch((e: unknown) => e)) as ApiError
    expect(net.network).toBe(true)
    expect(net.status).toBe(0)
  })

  it('resolves null for a 204', async () => {
    mockFetch((url) => (url.endsWith('/sanctum/csrf-cookie') ? new Response(null, { status: 204 }) : new Response(null, { status: 204 })))
    await expect(api.post('/api/auth/logout')).resolves.toBeNull()
  })
})
