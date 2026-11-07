/** A link from untrusted card text: kept only when it parses as an http(s) URL, else null. */
export function httpUrl(v: unknown): URL | null {
  if (typeof v !== 'string' || v.trim() === '') return null
  try {
    const u = new URL(v)
    return u.protocol === 'https:' || u.protocol === 'http:' ? u : null
  } catch {
    return null
  }
}
