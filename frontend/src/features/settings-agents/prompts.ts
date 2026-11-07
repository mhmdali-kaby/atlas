import { useMutation, useQueryClient } from '@tanstack/react-query'
import { diffLines } from 'diff'
import { api, ApiError, useApiQuery } from '../../lib/api'
import { settingsKey, type SettingsDoc } from '../onboarding'
import { historyKey, type HistoryEntry } from '../settings-risk/model'

/*
 * Prompt history (brief §14.7, decision 265 (3); design v22 §7, js/pages/settings-prompts.js).
 * ATL-251's API (docs/handoff/ATL-251-prompt-versions-api.md):
 *   GET /api/agents/{slug}/prompts            → versions, newest first, + meta.pinned_version
 *   GET /api/agents/{slug}/prompts/{version}  → { version, hash12, body }
 *   PUT /api/agents/{slug}/prompt-pin {version: int|null}
 * All default to the active env. The diff is computed here with jsdiff, line by line.
 */

export interface PromptVersion {
  version: number
  hash12: string
  created_at: string
  runs_count: number
  is_current_file: boolean
  is_pinned: boolean
}

export interface PromptList {
  data: PromptVersion[]
  meta: { agent: string; env: SettingsDoc['env']; pinned_version: number | null }
}

export interface PromptBody {
  data: { version: number; hash12: string; body: string }
}

export const promptsKey = (slug: string) => ['agent-prompts', slug] as const
export const promptBodyKey = (slug: string, version: number) => ['agent-prompts', slug, 'body', version] as const

export function usePromptVersions(slug: string) {
  return useApiQuery<PromptList>(promptsKey(slug), `/api/agents/${slug}/prompts`, { staleTime: 15_000 })
}

export function usePromptBody(slug: string, version: number | null) {
  return useApiQuery<PromptBody>(promptBodyKey(slug, version ?? 0), `/api/agents/${slug}/prompts/${version ?? 0}`, {
    enabled: version !== null,
    staleTime: Infinity,
  })
}

export interface PinResult {
  data: { agent: string; env: SettingsDoc['env']; pinned_version: number | null; settings_version: number }
}

/** PUT prompt-pin; on success the list, the settings doc and the settings history are refetched. */
export function usePinPrompt(slug: string) {
  const client = useQueryClient()
  return useMutation<PinResult, ApiError, { version: number | null }>({
    mutationFn: (body) => api.put<PinResult>(`/api/agents/${slug}/prompt-pin`, body),
    onSuccess: async (res) => {
      client.setQueryData<PromptList>(promptsKey(slug), (old) =>
        old
          ? {
              ...old,
              meta: { ...old.meta, pinned_version: res.data.pinned_version },
              data: old.data.map((v) => ({ ...v, is_pinned: v.version === res.data.pinned_version })),
            }
          : old,
      )
      await Promise.all([
        client.invalidateQueries({ queryKey: promptsKey(slug) }),
        client.invalidateQueries({ queryKey: settingsKey(res.data.env), exact: true }),
        client.invalidateQueries({ queryKey: historyKey(res.data.env) }),
      ])
    },
    /* 409: settings moved on in another window; reload what this page shows. */
    onError: (err) => {
      if (err.status !== 409) return
      void client.invalidateQueries({ queryKey: promptsKey(slug) })
      void client.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}

/** What the agent is told now: the pinned version, else the prompt file's version (null when unknown). */
export function usage(list: PromptList) {
  const file = list.data.find((v) => v.is_current_file)?.version ?? null
  const pinned = list.meta.pinned_version
  return { file, pinned, inUse: pinned ?? file }
}

/** When the pin was set: the newest settings-history entry that moved the key to `version`. */
export function pinnedAt(entries: HistoryEntry[] | undefined, slug: string, version: number | null): string | null {
  if (version === null || !entries) return null
  const k = `agents.${slug}.prompt_version`
  const hit = [...entries].sort((a, b) => b.version - a.version).find((e) => e.diff[k] && Number(e.diff[k]?.to) === version)
  return hit?.created_at ?? null
}

/** A prompt body as lines (a trailing newline does not make an empty last line). */
export function linesOf(body: string): string[] {
  const lines = body.replace(/\r\n/g, '\n').split('\n')
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
  return lines
}

export type DiffKind = 'add' | 'rem' | 'ctx'
export interface DiffLine {
  kind: DiffKind
  /** Line number in the older version (removed and context lines). */
  old: number | null
  /** Line number in the newer version (added and context lines). */
  new: number | null
  text: string
}

/** A unified line diff of `from` → `to` (jsdiff diffLines), one row per line. */
export function diffRows(from: string, to: string): DiffLine[] {
  const out: DiffLine[] = []
  let o = 1
  let n = 1
  /* Both end in one newline, so a last line that only gained a newline is not a change. */
  const norm = (s: string) => `${s.replace(/\r\n/g, '\n').replace(/\n+$/, '')}\n`
  for (const part of diffLines(norm(from), norm(to))) {
    const lines = linesOf(part.value)
    for (const text of lines) {
      if (part.added) out.push({ kind: 'add', old: null, new: n++, text })
      else if (part.removed) out.push({ kind: 'rem', old: o++, new: null, text })
      else out.push({ kind: 'ctx', old: o++, new: n++, text })
    }
  }
  return out
}

export type DiffBlock = { type: 'lines'; start: number; lines: DiffLine[] } | { type: 'fold'; start: number; lines: DiffLine[] }

/** Changed lines with `context` unchanged lines around them; longer unchanged runs fold. */
export function hunks(rows: DiffLine[], context = 3): DiffBlock[] {
  const keep = rows.map(() => false)
  rows.forEach((r, i) => {
    if (r.kind === 'ctx') return
    for (let k = Math.max(0, i - context); k <= Math.min(rows.length - 1, i + context); k++) keep[k] = true
  })
  const out: DiffBlock[] = []
  let i = 0
  while (i < rows.length) {
    const kept = keep[i] === true
    let j = i
    while (j < rows.length && (keep[j] === true) === kept) j++
    out.push({ type: kept ? 'lines' : 'fold', start: i, lines: rows.slice(i, j) })
    i = j
  }
  return out
}

export function counts(rows: DiffLine[]) {
  return { add: rows.filter((r) => r.kind === 'add').length, rem: rows.filter((r) => r.kind === 'rem').length }
}

/** The words for a failed PUT prompt-pin. */
export function pinErrorText(err: ApiError, t: (k: string) => string): string {
  if (err.status === 409) return t('pr.e409')
  if (err.network || err.status >= 500) return t('pr.eOffline')
  const field = err.fieldErrors.version?.[0]
  return field ?? err.message
}
