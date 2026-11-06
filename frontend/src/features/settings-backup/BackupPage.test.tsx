import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'
import i18n from '../../i18n'
import { API_URL } from '../../lib/api'
import { BackupPage } from './BackupPage'
import type { BackupDoc, BackupRun } from './api'

/* ATL-232: Settings → Backup against a faked ATL-231 GET /api/backup, POST /api/backup/run and PATCH /api/settings. */

const strip = (s: string | null | undefined) => (s ?? '').replace(/[⁦-⁩]/g, '')
// 28 Oct 2026, 08:00 UTC = 11:00 Baghdad.
const NOW = Date.UTC(2026, 9, 28, 8, 0, 0)
const H = 3_600_000
const FOLDER = '/Volumes/Backup/Atlas'

function okRun(id: string, at: number, bytes = 684_900_000): BackupRun {
  const d = new Date(at).toISOString()
  const file = `atlas-${d.slice(0, 10).replace(/-/g, '')}-${d.slice(11, 19).replace(/:/g, '')}.dump`
  return { id, file, path: `${FOLDER}/${file}`, bytes, status: 'ok', error: null, started_at: d, finished_at: new Date(at + 48_000).toISOString() }
}
function failedRun(id: string, at: number): BackupRun {
  const r = okRun(id, at)
  return { ...r, path: null, bytes: null, status: 'failed', error: `pg_dump: error: could not open output file "${FOLDER}/${r.file}": No such file or directory` }
}

function docOf(runs: BackupRun[], now = NOW): BackupDoc {
  const good = runs.find((r) => r.status === 'ok') ?? null
  const age = good ? Math.round((now - Date.parse(good.finished_at ?? good.started_at)) / 1000) : 0
  return {
    env: 'demo',
    settings: { enabled: true, folder: FOLDER, time: '03:00', timezone: 'Asia/Baghdad', keep: 14 },
    runs,
    last_good: good ? { ...good, age_seconds: age } : null,
    stale_after_hours: 26,
    stale: !good || age > 26 * 3600,
  }
}

type Reply = { status: number; body?: unknown }

function backend(runs: BackupRun[]) {
  const state = {
    doc: docOf(runs),
    version: 23,
    patches: [] as Record<string, unknown>[],
    runs: 0,
    patchReply: null as Reply | null,
    /** Run recorded on the next GET after Back up now (when released). */
    queued: null as BackupRun | null,
    released: false,
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const path = String(input).replace(API_URL, '')
      const method = (init.method ?? 'GET').toUpperCase()
      const reply = (status: number, body?: unknown) =>
        new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
      if (path === '/sanctum/csrf-cookie') return reply(204)
      if (path === '/api/backup' && method === 'GET') {
        if (state.queued && state.released) {
          state.doc = docOf([state.queued, ...state.doc.runs].slice(0, 10))
          state.queued = null
        }
        return reply(200, { data: state.doc })
      }
      if (path === '/api/backup/run' && method === 'POST') {
        state.runs += 1
        return reply(202, { data: { queued: true, message: 'A backup will start in a moment.' } })
      }
      if (path.startsWith('/api/settings?env=demo')) {
        return reply(200, { data: { id: 's1', env: 'demo', version: state.version, settings: {}, presets: {}, updated_at: '2026-10-28T00:00:00Z' } })
      }
      if (path === '/api/settings' && method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        state.patches.push(body)
        if (state.patchReply) return reply(state.patchReply.status, state.patchReply.body)
        state.version += 1
        return reply(200, { data: { id: 's1', env: 'demo', version: state.version, settings: {}, presets: {}, updated_at: '2026-10-28T00:00:00Z' } })
      }
      return reply(404, { message: 'Not found' })
    }),
  )
  return state
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <BackupPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
})
afterEach(async () => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  await act(() => i18n.changeLanguage('en'))
})

describe('Settings → Backup', () => {
  it('shows a good backup from 3 h ago with its time, size and file', async () => {
    backend([okRun('b1', NOW - 3 * H), okRun('b0', NOW - 27 * H)])
    renderPage()
    const card = await screen.findByTestId('bk-status')
    expect(card).toHaveAttribute('data-status', 'ok')
    // 05:00 UTC = 08:00 Baghdad (the owner's default zone).
    expect(strip(within(card).getByTestId('bk-when').textContent)).toBe('Today at 08:00')
    expect(within(card).getByTestId('bk-size')).toHaveTextContent('685 MB')
    expect(within(card).getByTestId('bk-file')).toHaveTextContent('atlas-20261028-050000.dump')
    expect(screen.queryByText(/No recent backup/)).not.toBeInTheDocument()
  })

  it('turns orange with "no recent backup" when the last good one is 30 h old', async () => {
    backend([okRun('b1', NOW - 30 * H)])
    renderPage()
    const card = await screen.findByTestId('bk-status')
    expect(card).toHaveAttribute('data-status', 'overdue')
    expect(within(card).getByTestId('bk-overdue')).toHaveTextContent('No recent backup: none for 1 day')
    expect(within(card).getByTestId('bk-overdue')).toHaveAttribute('data-tone', 'warning')
    expect(within(card).getByTestId('bk-when')).toHaveClass('is-warn')
  })

  it('shows the orange "no recent backup" state when there has never been a backup', async () => {
    backend([])
    renderPage()
    const card = await screen.findByTestId('bk-status')
    expect(card).toHaveAttribute('data-status', 'none')
    expect(within(card).getByRole('heading', { name: 'No recent backup: none yet' })).toBeInTheDocument()
    expect(card.querySelector('.st-empty-icon')).toHaveClass('is-warn')
    expect(strip(card.textContent)).toContain('The first runs tomorrow at 03:00')
  })

  it('"Back up now" disables itself while the run is in progress, then the new run tops the list', async () => {
    const state = backend([okRun('b1', NOW - 3 * H)])
    const user = userEvent.setup()
    renderPage()
    const button = await screen.findByTestId('bk-now')
    expect(button).toBeEnabled()
    state.queued = okRun('bnew', NOW + 1000, 690_000_000)
    await user.click(button)
    await waitFor(() => expect(screen.getByTestId('bk-now')).toBeDisabled())
    expect(state.runs).toBe(1)
    expect(screen.getByTestId('bk-status')).toHaveAttribute('data-status', 'running')
    expect(screen.getAllByTestId('bk-run')[0]).toHaveAttribute('data-status', 'running')
    // A second press while running sends nothing.
    await user.click(screen.getByTestId('bk-now'))
    expect(state.runs).toBe(1)

    state.released = true
    await waitFor(() => expect(screen.getByTestId('bk-now')).toBeEnabled(), { timeout: 5000 })
    const first = screen.getAllByTestId('bk-run')[0]!
    expect(first).toHaveAttribute('data-status', 'ok')
    expect(first).toHaveTextContent('atlas-20261028-080001.dump')
    expect(first).toHaveTextContent('690 MB')
  })

  it('shows the server’s error under the folder field for a relative path and does not save it', async () => {
    const state = backend([okRun('b1', NOW - 3 * H)])
    const message = 'The Backup folder must be a full path starting with "/", such as "/Users/you/Atlas Backups".'
    state.patchReply = { status: 422, body: { message, errors: { 'changes.backup.folder': [message] } } }
    const user = userEvent.setup()
    renderPage()
    const folder = await screen.findByLabelText('Folder')
    await user.clear(folder)
    await user.type(folder, 'Backups/Atlas')
    await user.click(screen.getByTestId('bk-save'))
    await waitFor(() => expect(screen.getByTestId('bk-folder-err')).toHaveTextContent(message))
    expect(screen.getByTestId('bk-folder-err')).toBeVisible()
    expect(folder).toHaveAttribute('aria-invalid', 'true')
    expect(state.patches).toEqual([{ env: 'demo', version: 23, changes: { 'backup.folder': 'Backups/Atlas' } }])
    // Not saved: the draft is still unsaved and the stored folder is unchanged.
    expect(screen.getByTestId('save-bar')).toBeInTheDocument()
    expect(state.doc.settings.folder).toBe(FOLDER)
    expect(folder).toHaveValue('Backups/Atlas')
  })

  it('shows a failed run’s error text in the list', async () => {
    backend([failedRun('bf', NOW - 2 * H), okRun('b1', NOW - 26 * H)])
    renderPage()
    const rows = await screen.findAllByTestId('bk-run')
    expect(rows[0]).toHaveAttribute('data-status', 'failed')
    expect(within(rows[0]!).getByTestId('bk-run-error')).toHaveTextContent('pg_dump: error: could not open output file')
    expect(screen.getByTestId('bk-status')).toHaveAttribute('data-status', 'failed')
  })

  it('saves changed settings with PATCH /api/settings on the env GET /api/backup names', async () => {
    const state = backend([okRun('b1', NOW - 3 * H)])
    const user = userEvent.setup()
    renderPage()
    const time = await screen.findByLabelText('Time')
    await user.clear(time)
    await user.type(time, '4:15')
    await user.tab()
    await user.click(screen.getByRole('button', { name: 'More copies' }))
    await user.click(screen.getByTestId('bk-save'))
    await waitFor(() => expect(state.patches).toHaveLength(1))
    expect(state.patches[0]).toEqual({ env: 'demo', version: 23, changes: { 'backup.time': '04:15', 'backup.keep': 15 } })
  })

  it('puts the chosen dump’s path into the restore commands', async () => {
    backend([okRun('b1', NOW - 3 * H)])
    renderPage()
    const cmd = await screen.findByTestId('bk-cmd-2')
    expect(cmd.textContent).toContain(`"${FOLDER}/atlas-20261028-050000.dump"`)
    expect(cmd.textContent).toContain('timescaledb_pre_restore')
    expect(screen.queryByRole('button', { name: /^restore$/i })).not.toBeInTheDocument()
  })

  it('reads in Arabic', async () => {
    await act(() => i18n.changeLanguage('ar'))
    backend([okRun('b1', NOW - 30 * H)])
    renderPage()
    const card = await screen.findByTestId('bk-status')
    expect(within(card).getByTestId('bk-overdue')).toHaveTextContent('لا توجد نسخة حديثة')
    expect(screen.getByTestId('bk-now')).toHaveTextContent('إنشاء نسخة الآن')
  })
})
