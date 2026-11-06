import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CircleAlert, CloudOff, DatabaseBackup, Folder, Minus, Plus, RotateCcw } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../lib/api'
import { formatFixed } from '../../lib/format'
import { TEAM_TZ, useOwnerZone } from '../../lib/ownerZone'
import { diffFromTeam, zoneCity } from '../../lib/zones'
import { Button, Skeleton, Toggle, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { settingsKey, useSettings, type SettingsDoc } from '../onboarding'
import { useLang, useNow } from '../portfolio/hooks'
import '../settings-connections/settings.css'
import '../settings-risk/risk.css'
import { historyKey } from '../settings-risk/model'
import { backupKey, useBackup, useRunBackup, type BackupDoc } from './api'
import './backup.css'
import {
  arrivedRun,
  bytesText,
  changesFor,
  clientErrors,
  clock,
  draftFrom,
  hm,
  KEEP_MAX,
  KEEP_MIN,
  keepValue,
  mmss,
  ms,
  nextRun,
  normTime,
  okRuns,
  TIME_RE,
  type Draft,
  type DraftField,
  type PendingRun,
} from './model'
import { Doctor, fill, isolate, NS, useWhen } from './parts'
import { Restore, RunsList } from './Runs'
import { StatusCard } from './Status'

/*
 * Settings → Backup (decision #255 (3), ATL-232; design v21 js/pages/settings-backup.js).
 * GET /api/backup is the page's one read. The four backup.* settings are versioned settings keys,
 * saved with PATCH /api/settings on the env GET /api/backup names. "Back up now" is POST
 * /api/backup/run (202, queued); a run is recorded only when it finishes, so the page polls until
 * a run it did not know appears. Atlas never restores: the restore steps are text to copy.
 */

const POLL_MS = 2000
/** RunBackup's job timeout is 1,860 s; past that the page stops waiting. */
const GIVE_UP_MS = 31 * 60_000

function Header({ version }: { version: number | null }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  return (
    <header className="st-pane st-risk-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <DatabaseBackup strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav')}</h2>
        <p className="max-w-[60ch] text-subheadline text-label-secondary">{t('intro')}</p>
      </div>
      {version !== null && (
        <span className="st-version" data-testid="settings-version">
          {t('version', { v: formatFixed(version, 0, lang) })}
        </span>
      )}
    </header>
  )
}

function Loading() {
  return (
    <div className="grid gap-7" aria-busy="true" data-testid="bk-loading">
      <div className="st-group bk-hero">
        <div className="bk-top">
          <div className="bk-now-col gap-2">
            <Skeleton className="h-3 w-[110px]" />
            <Skeleton className="h-6 w-[220px]" />
            <Skeleton className="h-3.5 w-[260px]" />
          </div>
          <div className="bk-shelf">
            {Array.from({ length: 14 }, (_, i) => (
              <Skeleton key={i} className="bk-sk-bay" />
            ))}
          </div>
        </div>
        <div className="h-5" />
      </div>
      <div className="st-group">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="bk-row">
            <Skeleton className="h-3.5 w-[22%]" />
            <Skeleton className="h-8 w-[200px] rounded-[8px]" />
          </div>
        ))}
      </div>
    </div>
  )
}

function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation(NS)
  const parts = t('err.body').split('./atlas up')
  return (
    <section role="alert" className="st-group st-empty" data-testid="bk-error">
      <span className="st-empty-icon is-error" aria-hidden="true">
        <CloudOff strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{t('err.title')}</h2>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">
        {parts.length === 2 ? (
          <>
            {parts[0]}
            <code dir="ltr">./atlas up</code>
            {parts[1]}
          </>
        ) : (
          t('err.body')
        )}
      </p>
      <Button variant="secondary" className="mt-2" onClick={onRetry}>
        {t('err.retry')}
      </Button>
    </section>
  )
}

function ErrLine({ id, message }: { id: string; message: string | undefined }) {
  return (
    <p className="st-field-error bk-ferr text-footnote" id={id} data-testid={id} hidden={!message}>
      <CircleAlert aria-hidden="true" />
      <span>{message}</span>
    </p>
  )
}

function Row({ error, off, children }: { error?: boolean; off?: boolean; children: ReactNode }) {
  return <div className={cx('bk-row', error && 'is-error', off && 'is-off')}>{children}</div>
}

/** PATCH /api/settings with booleans allowed (backup.enabled is a strict boolean). */
function useSaveBackup(env: SettingsDoc['env']) {
  const client = useQueryClient()
  return useMutation<SettingsDoc, ApiError, { version: number; changes: Record<string, string | number | boolean> }>({
    mutationFn: async ({ version, changes }) => (await api.patch<{ data: SettingsDoc }>('/api/settings', { env, version, changes })).data,
    onSuccess: (doc) => {
      client.setQueryData(settingsKey(env), { data: doc })
      void client.invalidateQueries({ queryKey: historyKey(env) })
      void client.invalidateQueries({ queryKey: backupKey })
    },
  })
}

/** `changes.backup.folder` → `folder`. */
function serverErrors(err: unknown): Partial<Record<DraftField | 'enabled', string>> {
  if (!(err instanceof ApiError) || err.status !== 422) return {}
  const out: Partial<Record<DraftField | 'enabled', string>> = {}
  for (const [key, messages] of Object.entries(err.fieldErrors)) {
    const m = /^changes\.backup\.(folder|time|keep|enabled)$/.exec(key)
    if (m?.[1] && messages[0]) out[m[1] as DraftField] = messages[0]
  }
  return out
}

function SettingsForm({ doc, version, env }: { doc: BackupDoc; version: number | null; env: SettingsDoc['env'] }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const zone = useOwnerZone()
  const when = useWhen()
  const client = useQueryClient()
  const save = useSaveBackup(env)
  const [edits, setEdits] = useState<Draft | null>(null)
  const [srv, setSrv] = useState<Partial<Record<DraftField | 'enabled', string>>>({})
  const [conflict, setConflict] = useState<number | null>(null)
  const focusError = useRef(false)
  const ref = useRef<HTMLDivElement>(null)

  const d = edits ?? draftFrom(doc)
  const ce = clientErrors(d)
  const err = (k: DraftField) => srv[k] ?? (ce[k] ? t(`e.${ce[k]}`) : undefined)
  const changes = changesFor(d, doc)
  const dirty = Object.keys(changes).length > 0 || (edits !== null && Object.keys(ce).length > 0)
  const invalid = Object.keys(ce).length > 0

  useEffect(() => {
    if (!focusError.current) return
    focusError.current = false
    ref.current?.querySelector<HTMLInputElement>('input[aria-invalid="true"]')?.focus()
  })

  const set = (patch: Partial<Draft>, field?: DraftField | 'enabled') => {
    setEdits({ ...d, ...patch })
    if (field && srv[field]) setSrv(({ [field]: _gone, ...rest }) => rest)
  }
  const stepKeep = (dir: 1 | -1) => {
    const v = keepValue(d.keep) ?? doc.settings.keep
    set({ keep: String(Math.min(KEEP_MAX, Math.max(KEEP_MIN, v + dir))) }, 'keep')
  }

  const onSave = () => {
    if (version === null || invalid || save.isPending || Object.keys(changes).length === 0) return
    save.mutate(
      { version, changes },
      {
        onSuccess: () => {
          setEdits(null)
          setSrv({})
          setConflict(null)
          const on = d.enabled
          toast({
            tone: 'success',
            title: t('savedToast'),
            body: on ? t('savedBody', { when: when(nextRun(d.time, Date.now()), Date.now(), lang, true) }) : t('savedOffBody'),
          })
        },
        onError: async (e) => {
          if (e.status === 409) {
            await client.invalidateQueries({ queryKey: settingsKey(env) })
            setConflict((e.body as { current_version?: number } | null)?.current_version ?? null)
            return
          }
          const fields = serverErrors(e)
          if (Object.keys(fields).length > 0) {
            setSrv(fields)
            focusError.current = true
            return
          }
          toast({ tone: 'error', title: t('e500.title'), body: t('e500.body') })
        },
      },
    )
  }

  const diff = diffFromTeam(zone)
  const local = TIME_RE.test(d.time) && diff !== 0 && zone !== TEAM_TZ ? clock(hm(d.time) + diff) : null
  const latest = okRuns(doc.runs)[0]
  const keepN = keepValue(d.keep)
  const keepSub =
    latest?.bytes && keepN !== null
      ? t('keepSub', { size: isolate(bytesText(latest.bytes * keepN, lang)) })
      : t('keepSubNone')
  const fe = err('folder')
  const te = err('time')
  const ke = err('keep')

  return (
    <div ref={ref} className="contents">
      <section className="grid" aria-labelledby="bk-set-title">
        <h2 className="bk-group-title" id="bk-set-title">
          {t('settings')}
        </h2>
        {conflict !== null && (
          <p className="bk-conflict text-footnote" role="status" data-testid="bk-conflict">
            {t('conflict', { v: formatFixed(conflict, 0, lang) })}
          </p>
        )}
        <div className="st-group bk-settings">
          <Row>
            <span className="bk-row-main">
              <span className="bk-row-label" id="bk-on-l">
                {t('enabled')}
              </span>
              <span className="bk-row-sub" id="bk-on-s">
                {d.enabled ? t('enabledSub') : fill(t('enabledOffSub'), { cmd: <Doctor /> })}
              </span>
            </span>
            <Toggle
              checked={d.enabled}
              onCheckedChange={(on) => set({ enabled: on }, 'enabled')}
              aria-labelledby="bk-on-l"
              aria-describedby="bk-on-s"
              data-testid="bk-enabled"
            />
          </Row>

          <Row error={!!fe}>
            <span className="bk-row-main">
              <label className="bk-row-label" htmlFor="bk-folder">
                {t('folder')}
              </label>
              <span className="bk-row-sub" id="bk-folder-hint">
                {t('folderHint')}
              </span>
              <ErrLine id="bk-folder-err" message={fe} />
            </span>
            <div className="bk-folder-field">
              <span className="bk-folder-icon" aria-hidden="true">
                <Folder />
              </span>
              <input
                className="bk-input"
                id="bk-folder"
                value={d.folder}
                dir="ltr"
                spellCheck={false}
                autoComplete="off"
                placeholder="/Users/you/Backups/Atlas"
                aria-describedby="bk-folder-hint bk-folder-err"
                aria-invalid={fe ? true : undefined}
                onChange={(e) => set({ folder: e.target.value }, 'folder')}
              />
            </div>
          </Row>

          <Row error={!!te} off={!d.enabled}>
            <span className="bk-row-main">
              <label className="bk-row-label" htmlFor="bk-time">
                {t('time')}
              </label>
              <span className="bk-row-sub" id="bk-time-sub">
                {t('timeSub')}
              </span>
              {local && (
                <span className="bk-local" data-testid="bk-local">
                  {t('timeLocal', { time: isolate(d.time), local: isolate(local), city: zoneCity(zone, lang) })}
                </span>
              )}
              <ErrLine id="bk-time-err" message={te} />
            </span>
            <input
              className="bk-input bk-time"
              id="bk-time"
              value={d.time}
              dir="ltr"
              inputMode="numeric"
              maxLength={5}
              spellCheck={false}
              autoComplete="off"
              placeholder="HH:MM"
              disabled={!d.enabled}
              aria-describedby="bk-time-sub bk-time-err"
              aria-invalid={te ? true : undefined}
              onChange={(e) => set({ time: e.target.value }, 'time')}
              onBlur={(e) => {
                const v = normTime(e.target.value)
                if (v !== d.time) set({ time: v }, 'time')
              }}
              onKeyDown={(e) => {
                if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
                e.preventDefault()
                const cur = normTime(d.time)
                const v = TIME_RE.test(cur) ? hm(cur) : 180
                set({ time: clock(v + (e.shiftKey ? 60 : 5) * (e.key === 'ArrowUp' ? 1 : -1)) }, 'time')
              }}
            />
          </Row>

          <Row error={!!ke}>
            <span className="bk-row-main">
              <label className="bk-row-label" htmlFor="bk-keep">
                {t('keep')}
              </label>
              <span className="bk-row-sub" id="bk-keep-sub">
                {keepSub}
              </span>
              <ErrLine id="bk-keep-err" message={ke} />
            </span>
            <div className="bk-keep">
              <div className="st-stepper-field" dir="ltr">
                <input
                  className="st-stepper-input no-unit bk-keep-input"
                  id="bk-keep"
                  inputMode="numeric"
                  autoComplete="off"
                  spellCheck={false}
                  value={d.keep}
                  aria-describedby="bk-keep-sub bk-keep-err"
                  aria-invalid={ke ? true : undefined}
                  onChange={(e) => set({ keep: e.target.value }, 'keep')}
                  onKeyDown={(e) => {
                    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
                    e.preventDefault()
                    stepKeep(e.key === 'ArrowUp' ? 1 : -1)
                  }}
                />
              </div>
              <span className="bk-keep-unit" aria-hidden="true">
                {t('unit', { count: keepN ?? doc.settings.keep })}
              </span>
              <div className="st-stepper-buttons" dir="ltr">
                <button type="button" aria-label={t('decrease')} disabled={(keepN ?? doc.settings.keep) <= KEEP_MIN} onClick={() => stepKeep(-1)}>
                  <Minus />
                </button>
                <span className="st-stepper-sep" />
                <button type="button" aria-label={t('increase')} disabled={(keepN ?? doc.settings.keep) >= KEEP_MAX} onClick={() => stepKeep(1)}>
                  <Plus />
                </button>
              </div>
            </div>
          </Row>
        </div>
      </section>

      {dirty && (
        <div className="st-save-bar" role="region" aria-label={t('unsaved')} data-testid="save-bar">
          <span className="st-save-dot" aria-hidden="true" />
          <span className="st-save-text" aria-live="polite">
            {t('unsaved')}
          </span>
          <Button
            variant="plain"
            size="sm"
            icon={<RotateCcw className="rtl:-scale-x-100" />}
            disabled={save.isPending}
            onClick={() => {
              setEdits(null)
              setSrv({})
            }}
          >
            {t('revert')}
          </Button>
          <Button variant="primary" size="sm" loading={save.isPending} disabled={invalid || version === null || Object.keys(changes).length === 0} onClick={onSave} data-testid="bk-save">
            {save.isPending ? t('saving') : t('save')}
          </Button>
        </div>
      )}
    </div>
  )
}

export function BackupPage() {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const client = useQueryClient()
  const [pending, setPending] = useState<PendingRun | null>(null)
  const query = useBackup(pending ? POLL_MS : false)
  const doc = query.data?.data
  const env = doc?.env ?? 'demo'
  const settings = useSettings(env, !!doc)
  const run = useRunBackup()
  const now = useNow(pending ? 1000 : 30_000)
  const [chosen, setChosen] = useState<string | null>(null)

  // The newest good run is the default choice for the restore steps.
  const chosenId = chosen && doc?.runs.some((r) => r.id === chosen) ? chosen : (doc ? okRuns(doc.runs)[0]?.id ?? null : null)

  // The requested run has been recorded: stop polling and say how it went.
  const arrived = arrivedRun(doc, pending)
  useEffect(() => {
    if (!arrived || !pending) return
    setPending(null)
    if (arrived.status === 'ok') {
      setChosen(arrived.id)
      const took = arrived.finished_at ? ms(arrived.finished_at) - ms(arrived.started_at) : 0
      toast({ tone: 'success', title: t('doneToast'), body: t('doneBody', { size: bytesText(arrived.bytes ?? 0, lang), d: mmss(took) }) })
    } else {
      toast({ tone: 'error', title: t('failToast'), body: t('failBody') })
    }
  }, [arrived, pending, t, lang])

  useEffect(() => {
    if (pending && now - pending.requestedAt > GIVE_UP_MS) setPending(null)
  }, [pending, now])

  const onRun = () => {
    if (!doc || pending || run.isPending) return
    const known = new Set(doc.runs.map((r) => r.id))
    run.mutate(undefined, {
      onSuccess: () => {
        setPending({ requestedAt: Date.now(), known })
        void client.invalidateQueries({ queryKey: backupKey })
      },
      onError: (e) => {
        if (e instanceof ApiError && e.status === 429) toast({ tone: 'info', title: t('e429.title'), body: t('e429.body') })
        else toast({ tone: 'error', title: t('eRun.title'), body: t('eRun.body') })
      },
    })
  }

  let body: ReactNode
  if (query.isPending) body = <Loading />
  else if (query.isError || !doc) body = <LoadError onRetry={() => void query.refetch()} />
  else {
    const p = pending ?? (run.isPending ? { requestedAt: now, known: new Set<string>() } : null)
    body = (
      <>
        <StatusCard doc={doc} now={now} lang={lang} pending={p} onRun={onRun} />
        <SettingsForm doc={doc} env={env} version={settings.data?.data.version ?? null} />
        <RunsList doc={doc} lang={lang} pending={pending} now={now} chosen={chosenId} onChoose={setChosen} />
        <Restore doc={doc} chosen={chosenId} />
      </>
    )
  }

  return (
    <div className="st-page bk-page" data-testid="bk-page">
      <Header version={settings.data?.data.version ?? null} />
      {body}
    </div>
  )
}
