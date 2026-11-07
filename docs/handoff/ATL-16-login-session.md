# ATL-16: API client, session, login screen and idle lock

## The two-minute version

- **Call the API only through `src/lib/api.ts`.** It handles cookies, CSRF and the 419 retry, and it turns a 401 into a sign-out. Never call `fetch` directly against Laravel.
- **Everything inside the shell is already guarded.** `src/app/router.tsx` wraps `<AppShell />` in `<RequireAuth>`. Feature routes in `routes.tsx` need no auth code of their own.
- **Pages with no shell and no session** go in `publicRoutes` on the `FeatureModule`, not in `routes`. `/login` is the only one so far.
- **Sign out (ATL-17):** `const logout = useLogout(); logout.mutate()`. That is all it takes. The mutation clears the session, the guard redirects to `/login` by itself, and every other cached query is removed.
- The shell has a catch-all `*` child that renders nothing. Before a feature exists (for example `/portfolio` until ATL-22), its URL still redirects to `/login?next=…` when signed out, and shows an empty shell when signed in. ATL-17 may replace `element: null` with a not-found view.

## `src/lib/api.ts`

```ts
API_URL                    // import.meta.env.VITE_API_URL ?? 'http://localhost:8000' (frontend/.env.example)
api.get<T>(path, init?)    // path relative to API root: '/api/portfolio/summary'
api.post<T>(path, json?, init?) / api.put / api.patch / api.delete
apiFetch<T>(path, { method, json, signal, headers })
class ApiError { status; body; headers; network; fieldErrors }   // status 0 + network=true when fetch threw
AUTH_EXPIRED_EVENT = 'atlas:auth-expired'; onAuthExpired(fn) → unsubscribe
useApiQuery<T>(key, path, options?)                       // useQuery over GET, error type ApiError
useApiMutation<TData, TVars>(method, path | (vars) => path, options?)   // vars sent as the JSON body
```

- Every request sends `credentials: 'include'` and `Accept: application/json`.
- The first unsafe request (POST/PUT/PATCH/DELETE) of the page load triggers one `GET /sanctum/csrf-cookie`. Every unsafe request sends `X-XSRF-TOKEN`, read fresh from the cookie and URL-decoded.
- A 419 refetches the cookie once and retries once. A second 419 throws.
- A 401 dispatches `atlas:auth-expired` and then throws.
- Successful responses resolve with the parsed JSON body, or `null` for a 204.

## `src/features/auth/session.ts`

```ts
useSession()      // ['auth','me'] → SessionUser | null (null = 401). Other failures are errors, not sign-outs.
useAuthStatus()   // ['auth','status'] → { owner_exists }
useLogin()        // mutate({ email, password }); on success sets ['auth','me']
useLogout()       // POST /api/auth/logout; always ends local session (even if the request fails)
SessionUser = { id, name, email, locale: 'en'|'ar', theme: 'system'|'light'|'dark' }
IDLE_LOCK_MS = 900000
loginPath(next, reason?) / safeNextPath(next)
```

The shell can read the owner's name from `useSession().data` for the sidebar. Inside the shell it is always non-null.

## Routing

- `/login?next=/portfolio`: after sign-in the owner lands on `next`. `next` is accepted only as a same-origin path; anything else falls back to `/`.
- `/login?reason=idle`: shows the "Locked after 15 minutes idle" notice.
- Idle lock: `RequireAuth` restarts a 15-minute timer on any `pointerdown`, `pointermove`, `keydown`, `wheel`, `touchstart` or `scroll`. When the timer fires it calls logout, then redirects to `/login?next=<page>&reason=idle`.

## Login screen states (design js/pages/login.js)

The screen has these states:

- normal
- first run: `owner_exists:false` shows `./atlas create-owner` with a copy button and "Check again"
- wrong password: a 422 shows the error under the password field, clears the password and keeps the email
- throttled: a 429 starts a countdown from `Retry-After` and disables the button
- locked: shown for `?reason=idle`
- server unreachable: a network error shows a red notice

Any other error shows a generic "Sign-in failed" notice.

## Not built here (ATL-17)

- The language and theme buttons at the top end of the login page (`.auth-prefs` in the design). ATL-17 owns the language/theme switch. Add it to `LoginPage` when you build it.
- `<Toaster />` is still not mounted anywhere. The login page does not need it: copy feedback is inline.

## Also added

- `src/ui/Seal.tsx`: `<Seal seed size detail variant>`, a port of the design's guilloché seal, and `<AppIcon size>`, the blue tile with the white seal. The sidebar brand (design: 26 px) can reuse `AppIcon`.
- Tokens: `--ui-app-icon-fill` / `--ui-app-icon-shadow(-hero)` with the utilities `app-icon-tile` and `app-icon-hero`.
- i18n namespace `auth` (`src/i18n/locales/{en,ar}/auth.json`). The heading uses `common:appName`, so Arabic shows أطلس.
