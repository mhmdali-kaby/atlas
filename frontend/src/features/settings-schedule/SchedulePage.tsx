import { useQueryClient } from '@tanstack/react-query'
import { CalendarClock, CircleAlert, Earth, Info, Minus, MoonStar, Plus, RotateCcw, Undo2 } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv, type TradingEnv } from '../../app/shell/tradingEnv'
import { useOwnerZone } from '../../lib/ownerZone'
import { diffFromTeam, spanWords, zoneCity } from '../../lib/zones'
import { ApiError } from '../../lib/api'
import { formatFixed, type Lang } from '../../lib/format'
import { Button, Skeleton, Toggle, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { Notice, useSettings } from '../onboarding'
import { useLang, useNow } from '../portfolio/hooks'
import { formatDayTime } from '../portfolio/time'
import '../settings-connections/settings.css'
import '../settings-risk/risk.css'
import { fieldErrors, historyKey, useSaveSettings, useSettingsHistory, type HistoryEntry } from '../settings-risk/model'
import { DayLine } from '../settings-general/DayLine'
import { SCHEDULE_DEFAULTS } from '../settings-general/dayModel'
import { Medallion } from '../warroom/Medallion'
import '../warroom/warroom.css'
import {
  changedFields,
  changesFor,
  clientErrors,
  clock,
  crossesMidnight,
  draftFrom,
  FIELDS,
  hm,
  inQuiet,
  inZone,
  intervalOf,
  JOB_KEYS,
  parseTime,
  previewTimes,
  scheduleErrors,
  scheduleFrom,
  settingKey,
  shiftDay,
  previewSlots,
  steppedInterval,
  TIME_RE,
  unsendable,
  usWeekend,
  weeklyParts,
  western,
  WEEKLY_KEY,
  type Draft,
  type Field,
  type JobKey,
  type Schedule,
  type TimeField,
  type Weekday,
  type Weekend,
} from './model'
import { DaySelect, FieldError, TimeInput } from './parts'
import { dayName, iso, NS, teamWeekdayTime } from './words'
import './schedule.css'

/*
 * Settings → Schedule (decision 255 (2), ATL-230; design v21 js/pages/settings-schedule.js).
 * When the team works, in Baghdad time. Edits batch behind the save bar and go out as one
 * versioned PATCH /api/settings of schedule.* keys (ATL-228); the supervisor picks them up on
 * its next tick (ATL-229). Order and range rules are the server's: its 422 shows under the field.
 */

const JOB_AGENT: Record<JobKey | typeof WEEKLY_KEY, string> = {
  news_morning_brief: 'news_intelligence',
  historian_regime_table: 'historian',
  scout_watchlist: 'market_scout',
  pm_daily_report: 'portfolio_manager',
  historian_weekly_review: 'historian',
}

const SCHEDULE_PREFIX = 'schedule.'

export function SchedulePage() {
  const env = useTradingEnv()
  return <SchedulePane key={env} env={env} />
}

function PaneHeader({ version }: { version: number | null }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  return (
    <header className="st-pane st-risk-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <CalendarClock strokeWidth={1.75} />
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

function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation(NS)
  const parts = t('error.body').split('./atlas up')
  return (
    <section role="alert" className="st-group st-empty" data-testid="sch-error">
      <span className="st-empty-icon is-error" aria-hidden="true">
        <CircleAlert strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{t('error.title')}</h2>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">
        {parts.length === 2 ? (
          <>
            {parts[0]}
            <code className="font-mono text-footnote">./atlas up</code>
            {parts[1]}
          </>
        ) : (
          t('error.body')
        )}
      </p>
      <Button variant="secondary" className="mt-2" onClick={onRetry}>
        {t('error.retry')}
      </Button>
    </section>
  )
}

function Loading() {
  return (
    <div className="grid gap-7" aria-busy="true" data-testid="sch-loading">
      <div className="st-group sch-hero">
        <Skeleton className="h-4 w-[64%]" />
        <Skeleton className="h-4 w-[40%]" />
      </div>
      <div className="st-group">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="sch-row">
            <Skeleton className="size-[30px] rounded-full" />
            <span className="sch-main">
              <Skeleton className="h-3.5 w-[34%]" />
            </span>
            <Skeleton className="h-8 w-[88px] rounded-[8px]" />
          </div>
        ))}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- words */

function useWords(lang: Lang) {
  const { t } = useTranslation(NS)
  const every = (iv: number) => (iv % 60 === 0 ? t('everyHours', { count: iv / 60 }) : t('everyMin', { count: iv }))
  const span = (min: number) => spanWords(min, lang)
  const value = (field: string, v: string | number | boolean | null): string => {
    if (v === null || v === undefined) return '—'
    if (field === 'analysis_interval_minutes') return t('min', { n: iso(String(v)) })
    if (field === 'quiet_hours_enabled') return t(v === true || v === 1 || v === '1' ? 'on' : 'off')
    if (field === 'stock_perps_weekend') return t(v === 'allow' ? 'wk.allow' : 'wk.no')
    if (field === WEEKLY_KEY) {
      const { day, time } = weeklyParts(String(v))
      return `${dayName(day, lang, 'short')} ${iso(time)}`
    }
    return iso(String(v))
  }
  return { t, every, span, value }
}

/* ---------------------------------------------------------------- activity */

function Activity({ entries, loading, lang }: { entries: HistoryEntry[] | undefined; loading: boolean; lang: Lang }) {
  const { t, value } = useWords(lang)
  if (loading && !entries) {
    return (
      <div className="st-group" aria-busy="true">
        {[0, 1].map((i) => (
          <div key={i} className="st-activity-row">
            <Skeleton className="h-3.5 w-[30%]" />
            <Skeleton className="col-span-full h-3.5 w-[55%]" />
          </div>
        ))}
      </div>
    )
  }
  const shown = (entries ?? []).filter((e) => e.version === 1 || Object.keys(e.diff).some((k) => k.startsWith(SCHEDULE_PREFIX))).slice(0, 5)
  if (shown.length === 0) {
    return (
      <div className="st-group">
        <p className="px-4 py-3 text-subheadline text-label-secondary">{t('activityEmpty')}</p>
      </div>
    )
  }
  return (
    <ol className="st-group" data-testid="sch-activity">
      {shown.map((entry, i) => {
        const keys = Object.keys(entry.diff)
        const fields = FIELDS.filter((f) => settingKey(f) in entry.diff)
        const created = entry.version === 1 || (fields.length > 0 && fields.every((f) => entry.diff[settingKey(f)]?.from === null) && keys.length > fields.length)
        const other = keys.some((k) => !k.startsWith(SCHEDULE_PREFIX))
        return (
          <li key={entry.id} className="st-activity-row" data-version={entry.version}>
            <span className="st-activity-head">
              <span>{t('version', { v: formatFixed(entry.version, 0, lang) })}</span>
              {i === 0 && <span className="st-badge-accent">{t('current')}</span>}
            </span>
            <time dateTime={entry.created_at} className="st-activity-time">
              {formatDayTime(entry.created_at, lang, true)}
            </time>
            <span className="st-activity-lines">
              {created && <span>{t('h.created')}</span>}
              {!created &&
                fields.map((f) => {
                  const c = entry.diff[settingKey(f)]
                  if (!c) return null
                  return (
                    <span key={f} data-field={f}>
                      {c.from === null
                        ? t('h.set', { field: t(`f.${f}`), to: value(f, c.to) })
                        : t('h.change', { field: t(`f.${f}`), from: value(f, c.from), to: value(f, c.to) })}
                    </span>
                  )
                })}
              {!created && other && <span>{t('h.other')}</span>}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

/* ---------------------------------------------------------------- the pane */

interface Conflict {
  version: number
  mine: Draft
  fields: Field[]
  saved: Schedule
}

function SchedulePane({ env }: { env: TradingEnv }) {
  const lang = useLang()
  const { t, every, span, value } = useWords(lang)
  const now = useNow(60_000)
  const client = useQueryClient()
  const query = useSettings(env)
  const history = useSettingsHistory(env)
  const save = useSaveSettings(env)
  const zone = useOwnerZone()
  const doc = query.data?.data

  /** `null` = no edits: the page shows the saved schedule. */
  const [edits, setEdits] = useState<Draft | null>(null)
  const [touched, setTouched] = useState<ReadonlySet<string>>(new Set())
  const [serverErrors, setServerErrors] = useState<Partial<Record<Field, string>>>({})
  const [conflict, setConflict] = useState<Conflict | null>(null)
  const pageRef = useRef<HTMLDivElement>(null)
  const focusError = useRef(false)

  useEffect(() => {
    if (!focusError.current) return
    focusError.current = false
    pageRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
  })

  const saved = doc ? scheduleFrom(doc) : null
  const draft = edits ?? (saved ? draftFrom(saved) : null)
  const diff = diffFromTeam(zone, now)
  const city = zoneCity(zone, lang)

  /** Any edit clears the server's messages: order rules span two fields, so fixing either one counts. */
  const update = (next: Draft, _field?: Field) => {
    setEdits(next)
    if (Object.keys(serverErrors).length > 0) setServerErrors({})
  }
  const setValue = <K extends keyof Schedule>(field: K, v: Schedule[K]) => {
    if (!draft) return
    update({ ...draft, values: { ...draft.values, [field]: v } }, field)
  }

  /* time fields: what is shown, typing, leaving, arrow keys */
  const timeOf = (d: Draft, k: TimeField) => (k === WEEKLY_KEY ? weeklyParts(d.values[WEEKLY_KEY]).time : d.values[k])
  const withTime = (d: Draft, k: TimeField, time: string): Draft => {
    const { [k]: _gone, ...raw } = d.raw
    const values = k === WEEKLY_KEY ? { ...d.values, [WEEKLY_KEY]: `${weeklyParts(d.values[WEEKLY_KEY]).day} ${time}` } : { ...d.values, [k]: time }
    return { ...d, values, raw }
  }
  const onTimeText = (k: TimeField, text: string) => {
    if (!draft) return
    const w = western(text)
    const exact = TIME_RE.test(w.trim()) ? w.trim() : null
    update(exact ? withTime(draft, k, exact) : { ...draft, raw: { ...draft.raw, [k]: w } }, k)
  }
  const onTimeCommit = (k: TimeField) => {
    if (!draft) return
    const raw = draft.raw[k]
    if (raw === undefined) return
    const v = parseTime(raw)
    if (v) update(withTime(draft, k, v), k)
    else setTouched((s) => new Set(s).add(k))
  }
  const onNudge = (k: TimeField, by: number) => {
    if (!draft) return
    const cur = parseTime(draft.raw[k] ?? '') ?? timeOf(draft, k)
    const base = hm(cur)
    const next = clock(Math.abs(by) === 60 ? base + by : Math.round((base + by) / 5) * 5)
    update(withTime(draft, k, next), k)
  }
  const shownTime = (k: TimeField) => (draft ? (draft.raw[k] ?? timeOf(draft, k)) : '')

  const onSave = () => {
    if (!doc || !draft || !saved || save.isPending || unsendable(draft) > 0) return
    save.mutate(
      { version: doc.version, changes: changesFor(draft, saved) },
      {
        onSuccess: (next) => {
          setEdits(null)
          setTouched(new Set())
          setServerErrors({})
          setConflict(null)
          toast({ tone: 'success', title: t('saved', { v: formatFixed(next.version, 0, lang) }), body: t('savedBody') })
        },
        onError: async (err) => {
          if (err instanceof ApiError && err.status === 409) {
            // Another window saved first: show its values, keep the owner's edits aside to re-apply.
            const mine = draft
            const fields = changedFields(draft, saved)
            const fresh = await query.refetch()
            void client.invalidateQueries({ queryKey: historyKey(env) })
            const freshDoc = fresh.data?.data
            setEdits(null)
            setTouched(new Set())
            setServerErrors({})
            setConflict({
              version: freshDoc?.version ?? (err.body as { current_version?: number } | null)?.current_version ?? doc.version,
              mine,
              fields,
              saved,
            })
            return
          }
          const byField = scheduleErrors(fieldErrors(err))
          if (Object.keys(byField).length > 0) {
            setServerErrors(byField)
            focusError.current = true
            toast({ tone: 'error', title: t('failed'), body: t('fixFields') })
            return
          }
          toast({ tone: 'error', title: t('failed'), body: t('failedBody') })
        },
      },
    )
  }

  const reapply = () => {
    if (!conflict || !saved) return
    const base = draftFrom(saved)
    const values = { ...base.values } as Record<string, unknown>
    for (const f of conflict.fields) values[f] = conflict.mine.values[f]
    const interval = conflict.fields.includes('analysis_interval_minutes') ? conflict.mine.interval : base.interval
    setEdits({ values: values as unknown as Schedule, interval, raw: {} })
    setConflict(null)
  }

  let body
  if (query.isPending) {
    body = <Loading />
  } else if (!doc || !draft || !saved) {
    body = <LoadError onRetry={() => void query.refetch()} />
  } else {
    const v = draft.values
    const local = clientErrors(draft, touched)
    const errorOf = (f: Field): string | undefined => serverErrors[f] ?? (local[f] ? t(local[f] === 'time' ? 'e.time' : 'e.every') : undefined)
    const changed = changedFields(draft, saved)
    const blocking = unsendable(draft) + Object.keys(serverErrors).length
    const iv = intervalOf(draft)
    const list = iv === null || draft.raw.analysis_first_slot !== undefined || draft.raw.analysis_last_slot !== undefined ? [] : previewSlots({ ...v, analysis_interval_minutes: iv })
    /** The draft as the DayLine draws it (design lineValues): an unusable interval or slot order falls back. */
    const lineIv = iv !== null && previewSlots({ ...SCHEDULE_DEFAULTS, analysis_interval_minutes: iv }).length > 0 ? iv : saved.analysis_interval_minutes
    const lineValues = {
      ...v,
      analysis_interval_minutes: lineIv,
      analysis_last_slot: hm(v.analysis_last_slot) <= hm(v.analysis_first_slot) ? v.analysis_first_slot : v.analysis_last_slot,
    }

    /** "07:30 in London" beside a Baghdad time, or nothing when the zones agree. */
    const altOf = (k: TimeField): string | undefined => {
      if (!diff || draft.raw[k] !== undefined) return undefined
      if (k === WEEKLY_KEY) {
        const { day, time } = weeklyParts(v[WEEKLY_KEY])
        const at = inZone(time, diff)
        return at ? t('altWk', { day: dayName(shiftDay(day, at.shift), lang, 'short'), time: iso(at.time), city }) : undefined
      }
      const at = inZone(v[k], diff)
      if (!at) return undefined
      return t(at.shift === 1 ? 'altNext' : at.shift === -1 ? 'altPrev' : 'alt', { time: iso(at.time), city })
    }

    const timeRow = (k: TimeField, label: string, sub: string, opts: { agent?: string; extra?: ReactNode; control?: ReactNode } = {}) => (
      <div key={k} className={cx('sch-row', opts.agent && 'sch-job', changed.includes(k) && 'is-changed')} data-row={k}>
        {opts.agent && <Medallion slug={opts.agent} size={30} status="idle" />}
        <span className="sch-main">
          <span className="sch-label text-callout" id={`sch-${k}-label`}>
            {label}
            {changed.includes(k) && <span className="sr-only">, {t('changed')}</span>}
          </span>
          <span className="sch-sub text-footnote" data-testid={`sch-${k}-sub`}>
            {sub}
          </span>
          {opts.extra}
          <FieldError id={`sch-${k}-err`} message={errorOf(k)} />
        </span>
        <span className="sch-ctl">
          {opts.control}
          <TimeInput
            id={`sch-${k}`}
            label={label}
            value={shownTime(k)}
            error={errorOf(k)}
            alt={altOf(k)}
            onText={(text) => onTimeText(k, text)}
            onCommit={() => onTimeCommit(k)}
            onNudge={(by) => onNudge(k, by)}
          />
        </span>
      </div>
    )

    const quietOn = v.quiet_hours_enabled
    const quietReady = quietOn && draft.raw.quiet_hours_start === undefined && draft.raw.quiet_hours_end === undefined && v.quiet_hours_start !== v.quiet_hours_end
    const crosses = draft.raw.quiet_hours_start === undefined && draft.raw.quiet_hours_end === undefined && crossesMidnight(v.quiet_hours_start, v.quiet_hours_end)
    const quietLines: string[] = []
    if (quietReady) {
      for (const k of JOB_KEYS) {
        if (draft.raw[k] === undefined && inQuiet(hm(v[k]), v.quiet_hours_start, v.quiet_hours_end)) quietLines.push(t('q.job', { job: t(`jl.${k}`), time: iso(v[k]) }))
      }
      const hit = list.filter((m) => inQuiet(m, v.quiet_hours_start, v.quiet_hours_end)).length
      if (hit) quietLines.push(t('q.cycles', { count: hit, total: iso(list.length) }))
    }
    const beforeWl = draft.raw.analysis_first_slot === undefined && draft.raw.scout_watchlist === undefined && hm(v.analysis_first_slot) < hm(v.scout_watchlist)
    const wk = usWeekend(now)
    const weekly = weeklyParts(v[WEEKLY_KEY])

    const onRadioKey = (e: KeyboardEvent<HTMLDivElement>) => {
      if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight'].includes(e.key)) return
      e.preventDefault()
      const next: Weekend = v.stock_perps_weekend === 'allow' ? 'no_new_trades' : 'allow'
      setValue('stock_perps_weekend', next)
      requestAnimationFrame(() => pageRef.current?.querySelector<HTMLElement>(`[data-weekend="${next}"]`)?.focus())
    }

    body = (
      <>
        {conflict && (
          <Notice
            tone="warning"
            icon={RotateCcw}
            role="alert"
            title={t('conflict.title')}
            data-testid="sch-conflict"
            actions={
              <>
                <Button variant="secondary" size="sm" icon={<Undo2 className="rtl:-scale-x-100" />} onClick={reapply}>
                  {t('conflict.apply')}
                </Button>
                <Button variant="plain" size="sm" onClick={() => setConflict(null)}>
                  {t('conflict.drop')}
                </Button>
              </>
            }
          >
            {t('conflict.body', { v: formatFixed(conflict.version, 0, lang) })}
            <span className="sch-mine" data-testid="sch-mine">
              {conflict.fields.map((f) => (
                <span key={f}>
                  {t('h.change', {
                    field: t(`f.${f}`),
                    from: value(f, conflict.saved[f]),
                    to: value(f, f === 'analysis_interval_minutes' ? (intervalOf(conflict.mine) ?? conflict.mine.interval) : conflict.mine.values[f]),
                  })}
                </span>
              ))}
            </span>
          </Notice>
        )}

        <section className="grid" aria-labelledby="sch-day-title">
          <h2 id="sch-day-title" className="sch-group-title">
            {t('day')}
          </h2>
          <div className="st-group sch-hero">
            <div className="sch-line" data-testid="sch-dayline">
              <DayLine values={lineValues} frame="team" now={now} lang={lang} />
            </div>
            <div className="sch-hero-foot">
              <p className="sch-cap" data-testid="sch-cap" aria-live="polite">
                {list.length === 0 ? t('capNone') : t('cap', { count: list.length, every: every(iv ?? 0), first: iso(v.analysis_first_slot), last: iso(v.analysis_last_slot) })}
              </p>
              {list.length > 0 && (
                <p className="sch-preview" data-testid="sch-preview">
                  {t('preview', { times: '\u0000' })
                    .split('\u0000')
                    .map((piece, i) =>
                      i === 0 ? (
                        <span key={i}>{piece}</span>
                      ) : (
                        <span key={i}>
                          <bdi dir="ltr" className="sch-times" data-testid="sch-preview-times">
                            {previewTimes(list)}
                          </bdi>
                          {piece}
                        </span>
                      ),
                    )}
                </p>
              )}
              {diff !== 0 && (
                <p className="sch-zone" data-testid="sch-zone">
                  <Earth aria-hidden="true" strokeWidth={1.75} />
                  <span>{t(diff < 0 ? 'zoneBehind' : 'zoneAhead', { city, span: span(diff) })}</span>
                </p>
              )}
            </div>
          </div>
        </section>

        <section className="grid" aria-labelledby="sch-jobs-title">
          <h2 id="sch-jobs-title" className="sch-group-title">
            {t('jobs')}
          </h2>
          <div className="st-group sch-group">
            {JOB_KEYS.map((k) => timeRow(k, t(`j.${k}`), t(`j.${k}Sub`), { agent: JOB_AGENT[k] }))}
            {timeRow(WEEKLY_KEY, t(`j.${WEEKLY_KEY}`), t(`j.${WEEKLY_KEY}Sub`), {
              agent: JOB_AGENT[WEEKLY_KEY],
              control: (
                <DaySelect
                  id="sch-wk-day"
                  label={t('dayOf', { job: t(`j.${WEEKLY_KEY}`) })}
                  value={weekly.day}
                  lang={lang}
                  onChange={(day: Weekday) => {
                    const time = TIME_RE.test(weekly.time) ? weekly.time : '18:00'
                    update({ ...draft, values: { ...v, [WEEKLY_KEY]: `${day} ${time}` } }, WEEKLY_KEY)
                  }}
                />
              ),
            })}
          </div>
          <p className="sch-group-note">{t('jobsNote')}</p>
        </section>

        <section className="grid" aria-labelledby="sch-an-title">
          <h2 id="sch-an-title" className="sch-group-title">
            {t('an.title')}
          </h2>
          <div className="st-group sch-group">
            <div className={cx('sch-row', changed.includes('analysis_interval_minutes') && 'is-changed')} data-row="analysis_interval_minutes">
              <span className="sch-main">
                <label htmlFor="sch-analysis_interval_minutes" className="sch-label text-callout">
                  {t('an.every')}
                  {changed.includes('analysis_interval_minutes') && <span className="sr-only">, {t('changed')}</span>}
                </label>
                <span className="sch-sub text-footnote">{t('an.everySub')}</span>
                <FieldError id="sch-analysis_interval_minutes-err" message={errorOf('analysis_interval_minutes')} />
              </span>
              <div className={cx('st-stepper sch-stepper', errorOf('analysis_interval_minutes') && 'is-error')}>
                <div className="st-stepper-field" dir="ltr">
                  <input
                    id="sch-analysis_interval_minutes"
                    className="st-stepper-input"
                    inputMode="numeric"
                    autoComplete="off"
                    spellCheck={false}
                    value={draft.interval}
                    aria-invalid={errorOf('analysis_interval_minutes') ? true : undefined}
                    aria-describedby={errorOf('analysis_interval_minutes') ? 'sch-analysis_interval_minutes-err' : undefined}
                    onChange={(e) => update({ ...draft, interval: western(e.target.value).trim() }, 'analysis_interval_minutes')}
                    onKeyDown={(e) => {
                      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
                      e.preventDefault()
                      update({ ...draft, interval: String(steppedInterval(iv ?? Number.NaN, e.key === 'ArrowUp' ? 1 : -1)) }, 'analysis_interval_minutes')
                    }}
                  />
                  <span aria-hidden="true" className="st-stepper-unit">
                    {t('an.unit')}
                  </span>
                </div>
                <div className="st-stepper-buttons">
                  <button
                    type="button"
                    aria-label={t('an.dec')}
                    disabled={iv !== null && iv <= 15}
                    onClick={() => update({ ...draft, interval: String(steppedInterval(iv ?? Number.NaN, -1)) }, 'analysis_interval_minutes')}
                  >
                    <Minus aria-hidden="true" strokeWidth={2} />
                  </button>
                  <span className="st-stepper-sep" aria-hidden="true" />
                  <button
                    type="button"
                    aria-label={t('an.inc')}
                    disabled={iv !== null && iv >= 240}
                    onClick={() => update({ ...draft, interval: String(steppedInterval(iv ?? Number.NaN, 1)) }, 'analysis_interval_minutes')}
                  >
                    <Plus aria-hidden="true" strokeWidth={2} />
                  </button>
                </div>
              </div>
            </div>
            {timeRow('analysis_first_slot', t('an.first'), t('an.firstSub'), {
              extra: beforeWl ? (
                <span className="sch-hint text-footnote" data-testid="sch-before-wl">
                  <Info aria-hidden="true" strokeWidth={1.75} />
                  <span>{t('an.beforeWl', { time: iso(v.scout_watchlist) })}</span>
                </span>
              ) : null,
            })}
            {timeRow('analysis_last_slot', t('an.last'), t('an.lastSub'))}
          </div>
        </section>

        <section className="grid" aria-labelledby="sch-q-title">
          <h2 id="sch-q-title" className="sch-group-title">
            {t('q.title')}
          </h2>
          <div className="st-group sch-group">
            <div className={cx('sch-row has-icon', changed.includes('quiet_hours_enabled') && 'is-changed')}>
              <span className="sch-moon" aria-hidden="true">
                <MoonStar strokeWidth={1.75} />
              </span>
              <span className="sch-main">
                <span className="sch-label text-callout" id="sch-q-label">
                  {t('q.title')}
                </span>
                <span className="sch-sub text-footnote">{t('q.sub')}</span>
              </span>
              <Toggle
                checked={quietOn}
                aria-labelledby="sch-q-label"
                data-testid="sch-quiet"
                onCheckedChange={(on) => setValue('quiet_hours_enabled', on)}
              />
            </div>
            {quietOn && (
              <div className="sch-q-times">
                {timeRow('quiet_hours_start', t('q.from'), t('q.fromSub'))}
                {timeRow('quiet_hours_end', t('q.to'), crosses ? t('q.toNext', { time: iso(v.quiet_hours_end) }) : t('q.toSub'))}
                {quietLines.length > 0 && (
                  <ul className="sch-qnotes" data-testid="sch-qnotes">
                    {quietLines.map((l) => (
                      <li key={l}>
                        <MoonStar aria-hidden="true" strokeWidth={1.75} />
                        <span>{l}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
          <p className="sch-group-note">{t('q.note')}</p>
        </section>

        <section className="grid" aria-labelledby="sch-wk-title">
          <h2 id="sch-wk-title" className="sch-group-title">
            {t('wk.title')}
          </h2>
          <div className="st-group" role="radiogroup" aria-labelledby="sch-wk-label" onKeyDown={onRadioKey}>
            <p className="sch-wk-label text-callout" id="sch-wk-label">
              {t('wk.label')}
            </p>
            {(['allow', 'no_new_trades'] as const).map((opt) => {
              const checked = v.stock_perps_weekend === opt
              return (
                <div
                  key={opt}
                  role="radio"
                  aria-checked={checked}
                  tabIndex={checked ? 0 : -1}
                  data-weekend={opt}
                  className={cx('sch-wk', changed.includes('stock_perps_weekend') && checked && 'is-changed')}
                  onClick={() => setValue('stock_perps_weekend', opt)}
                  onKeyDown={(e) => {
                    if (e.key === ' ' || e.key === 'Enter') {
                      e.preventDefault()
                      setValue('stock_perps_weekend', opt)
                    }
                  }}
                >
                  <span className="sch-radio" aria-hidden="true" />
                  <span className="sch-main">
                    <span className="sch-label text-callout">{t(opt === 'allow' ? 'wk.allow' : 'wk.no')}</span>
                    <span className="sch-sub text-footnote">
                      {opt === 'allow'
                        ? t('wk.allowSub')
                        : t('wk.noSub', { fri: iso('20:00'), sun: iso('20:00'), from: teamWeekdayTime(wk.from, lang), to: teamWeekdayTime(wk.to, lang) })}
                    </span>
                  </span>
                </div>
              )
            })}
            <FieldError id="sch-stock_perps_weekend-err" message={errorOf('stock_perps_weekend')} />
          </div>
          <p className="sch-group-note">{t('wk.note')}</p>
        </section>

        <section className="grid" aria-labelledby="sch-act-title">
          <h2 id="sch-act-title" className="sch-group-title">
            {t('act')}
          </h2>
          <Activity entries={history.data?.data} loading={history.isPending} lang={lang} />
          <p className="sch-group-note">{t('actNote')}</p>
        </section>

        {(changed.length > 0 || blocking > 0) && (
          <div className="st-save-bar" role="region" aria-label={t('unsaved', { count: Math.max(1, changed.length) })} data-testid="save-bar">
            <span className="st-save-dot" aria-hidden="true" />
            <span className="st-save-text" aria-live="polite">
              {blocking > 0 ? t('fix', { count: blocking }) : t('unsaved', { count: changed.length })}
            </span>
            <Button
              variant="plain"
              size="sm"
              icon={<RotateCcw className="rtl:-scale-x-100" />}
              disabled={save.isPending}
              onClick={() => {
                setEdits(null)
                setTouched(new Set())
                setServerErrors({})
              }}
            >
              {t('revert')}
            </Button>
            <Button variant="primary" size="sm" loading={save.isPending} disabled={blocking > 0 || changed.length === 0} onClick={onSave} data-testid="sch-save">
              {save.isPending ? t('saving') : t('save')}
            </Button>
          </div>
        )}
      </>
    )
  }

  return (
    <div ref={pageRef} className="st-page st-risk sch-page">
      <PaneHeader version={doc && !query.isPending ? doc.version : null} />
      {body}
    </div>
  )
}
