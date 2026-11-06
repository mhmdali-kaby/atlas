import { CalendarClock, Check, ChevronRight, CircleAlert, CloudOff, Laptop, RefreshCw, Settings } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { initials } from '../../app/shell/Prefs'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { useSession } from '../auth/session'
import { DATE_LOCALE, formatMoney, formatSigned, type Lang } from '../../lib/format'
import { useOwnerZone } from '../../lib/ownerZone'
import { saveErrorOf, usePreferences, useSavePreference, type PreferenceField, type Preferences } from '../../lib/preferences'
import { browserZone, diffFromTeam, spanWords, zoneCity, zoneName, zoneOffset } from '../../lib/zones'
import { isKnownZone } from '../../lib/ownerZone'
import { THEME_CHOICES, type ThemeChoice } from '../../theme/theme'
import { Button, SegmentedControl, Skeleton, Spinner } from '../../ui'
import { INPUT_CLASSES } from '../../ui/TextField'
import { cx } from '../../ui/cx'
import { useSettings } from '../onboarding'
import { useLang, useNow } from '../portfolio/hooks'
import { formatAgo } from '../portfolio/time'
import { formatDateTime } from '../warroom/time'
import '../settings-connections/settings.css'
import { clock, hm, scheduleFrom } from './dayModel'
import { DayLine } from './DayLine'
import './general.css'
import { ZonePicker } from './ZonePicker'

/*
 * Settings → General (brief §14.1, decision 255 (1), design v21 owner-setup §7). Who you are to
 * Atlas and how it speaks to you: your name, the language, the time zone every clock time is
 * shown in, and the appearance. Each control saves on its own with PATCH /api/me/preferences
 * (just that field): applied at once, and put back with the server's message beside it when the
 * save is refused (422) or not answered.
 */

const NS = 'settings-general'
const SAVED_MS = 2400

type Status = { state: 'saving' | 'saved' | null; error: string | null }
const IDLE: Status = { state: null, error: null }

function Header() {
  const { t } = useTranslation(NS)
  return (
    <header className="st-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <Settings strokeWidth={1.5} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav')}</h2>
        <p className="max-w-[54ch] text-subheadline text-label-secondary">{t('intro')}</p>
      </div>
    </header>
  )
}

/** "Saving… / Saved" beside a control's label, read once (role=status). */
function SaveMark({ status, field }: { status: Status; field: string }) {
  const { t } = useTranslation(NS)
  return (
    <span className={cx('gn-save', status.state && `is-${status.state}`)} role="status" aria-live="polite" data-testid={`save-${field}`}>
      {status.state === 'saving' && (
        <>
          <Spinner />
          <span>{t('saving')}</span>
        </>
      )}
      {status.state === 'saved' && (
        <>
          <Check aria-hidden="true" strokeWidth={2.25} />
          <span>{t('saved')}</span>
        </>
      )}
    </span>
  )
}

function ErrLine({ id, message }: { id: string; message: string | null }) {
  if (!message) return null
  return (
    <p className="gn-err" id={id} data-testid={id}>
      <CircleAlert aria-hidden="true" strokeWidth={1.5} />
      <span>{message}</span>
    </p>
  )
}

/** A miniature Atlas window in a fixed appearance: sidebar glass, the toolbar's red Stop, an equity line. */
function ThumbWin({ kind }: { kind: 'light' | 'dark' }) {
  return (
    <span className={`gn-win is-${kind}`}>
      <span className="gn-win-side">
        <i className="is-on" />
        <i />
        <i />
        <i />
      </span>
      <span className="gn-win-main">
        <span className="gn-win-bar">
          <i className="gn-win-title" />
          <i className="gn-win-stop" />
        </span>
        <svg className="gn-win-chart" viewBox="0 0 64 28" preserveAspectRatio="none">
          <path className="a" d="M0 24 L8 21 L15 22 L23 16 L31 17 L39 11 L47 12 L55 6 L64 4 L64 28 L0 28 Z" />
          <path className="l" d="M0 24 L8 21 L15 22 L23 16 L31 17 L39 11 L47 12 L55 6 L64 4" />
        </svg>
      </span>
    </span>
  )
}

function ThemeTiles({ value, onChoose, describedBy }: { value: ThemeChoice; onChoose: (v: ThemeChoice) => void; describedBy?: string }) {
  const { t } = useTranslation(NS)
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return
    e.preventDefault()
    const rtl = document.documentElement.dir === 'rtl'
    const fwd = e.key === 'ArrowDown' || (e.key === 'ArrowRight') !== rtl
    const next = THEME_CHOICES[(i + (fwd ? 1 : -1) + THEME_CHOICES.length) % THEME_CHOICES.length] as ThemeChoice
    onChoose(next)
    refs.current[next]?.focus()
  }
  return (
    <div className="gn-tiles" role="radiogroup" aria-labelledby="gn-look-title" aria-describedby={describedBy} data-testid="theme-tiles">
      {THEME_CHOICES.map((v, i) => (
        <button
          key={v}
          ref={(el) => {
            refs.current[v] = el
          }}
          type="button"
          role="radio"
          className="gn-tile"
          aria-checked={value === v}
          tabIndex={value === v ? 0 : -1}
          data-theme-pick={v}
          onClick={() => v !== value && onChoose(v)}
          onKeyDown={(e) => onKey(e, i)}
        >
          <span className={`gn-thumb is-${v}`} aria-hidden="true">
            {v === 'system' ? (
              <>
                <ThumbWin kind="light" />
                <ThumbWin kind="dark" />
              </>
            ) : (
              <ThumbWin kind={v} />
            )}
          </span>
          <span className="gn-tile-label">{t(`theme.${v}`)}</span>
        </button>
      ))}
    </div>
  )
}

function Preview({ lang, zone, now }: { lang: Lang; zone: string; now: number }) {
  const { t } = useTranslation(NS)
  const long = new Intl.DateTimeFormat(lang === 'ar' ? DATE_LOCALE.ar : 'en-GB', {
    timeZone: zone,
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(now)
  const fig = (s: string) => (
    <bdi dir="ltr" className="fig">
      {s}
    </bdi>
  )
  return (
    <dl className="gn-pv" data-testid="gn-preview">
      <div className="gn-pv-item is-long">
        <dt>{t('pv.long')}</dt>
        <dd>
          <span data-testid="gn-preview-long">{long}</span> <span className="gn-pv-zone">{zoneName(zone, lang)}</span>
        </dd>
      </div>
      <div className="gn-pv-item">
        <dt>{t('pv.short')}</dt>
        <dd>
          <span className="fig">{formatDateTime(now, lang)}</span> <span className="gn-pv-zone">{formatAgo(5 * 60_000, lang)}</span>
        </dd>
      </div>
      <div className="gn-pv-item">
        <dt>{t('pv.amount')}</dt>
        <dd>{fig(formatMoney('1234567.89', 'USD', lang))}</dd>
      </div>
      <div className="gn-pv-item">
        <dt>{t('pv.change')}</dt>
        <dd>
          <span className="fig text-profit-ink">{fig(formatSigned('2450', 'USD', lang))}</span>
          <span className="fig text-loss-ink">{fig(formatSigned('-1.25', 'percent', lang))}</span>
          {fig('+1.40R')}
        </dd>
      </div>
    </dl>
  )
}

function Loading() {
  return (
    <div className="st-page gn-page" aria-busy="true" data-testid="gn-loading">
      <Header />
      <div className="st-group flex items-center gap-4 p-5">
        <Skeleton className="size-14 rounded-full" />
        <div className="grid flex-1 gap-2">
          <Skeleton className="h-3.5 w-[30%]" />
          <Skeleton className="h-8 w-full max-w-[420px] rounded-[8px]" />
        </div>
      </div>
      <div className="st-group flex flex-wrap gap-5 p-5">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-20 w-[124px] rounded-[10px]" />
        ))}
      </div>
      <div className="st-group">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="gn-row">
            <Skeleton className="h-3.5 w-[24%]" />
            <Skeleton className="ms-auto h-7 w-[180px] rounded-[8px]" />
          </div>
        ))}
      </div>
    </div>
  )
}

export function GeneralPage() {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const zoneOnScreen = useOwnerZone()
  const now = useNow(30_000)
  const session = useSession()
  const prefs = usePreferences()
  const save = useSavePreference()
  const env = useTradingEnv()
  const settings = useSettings(env, true, false)
  const schedule = scheduleFrom(settings.data?.data.settings as Record<string, unknown> | undefined)

  const [status, setStatus] = useState<Record<string, Status>>({})
  const timers = useRef<Record<string, number>>({})
  useEffect(() => () => Object.values(timers.current).forEach((h) => window.clearTimeout(h)), [])
  const statusOf = (f: PreferenceField) => status[f] ?? IDLE
  const setField = (f: PreferenceField, s: Status) => setStatus((old) => ({ ...old, [f]: s }))

  const data = prefs.data
  const [draft, setDraft] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)

  if (prefs.isPending) return <Loading />
  if (prefs.isError || !data) {
    return (
      <div className="st-page gn-page">
        <Header />
        <section role="alert" className="st-group st-empty" data-testid="gn-error">
          <span className="st-empty-icon is-error" aria-hidden="true">
            <CloudOff strokeWidth={1.5} />
          </span>
          <h2 className="text-title-3">{t('err.title')}</h2>
          <p className="max-w-[46ch] text-subheadline text-label-secondary">{t('err.body')}</p>
          <Button variant="secondary" className="mt-2" icon={<RefreshCw strokeWidth={1.5} />} onClick={() => void prefs.refetch()}>
            {t('err.retry')}
          </Button>
        </section>
      </div>
    )
  }

  const label = (field: PreferenceField, v: string): string =>
    field === 'locale' ? (v === 'ar' ? 'العربية' : 'English') : field === 'theme' ? t(`theme.${v}`) : field === 'timezone' ? zoneCity(v, lang) : v

  function send<K extends PreferenceField>(field: K, value: Preferences[K]) {
    const previous = data![field]
    if (value === previous) return
    window.clearTimeout(timers.current[field])
    setField(field, { state: 'saving', error: null })
    save.mutate(
      { field, value, previous },
      {
        onSuccess: () => {
          setField(field, { state: 'saved', error: null })
          timers.current[field] = window.setTimeout(() => setField(field, IDLE), SAVED_MS)
        },
        onError: (err) => {
          const e = saveErrorOf(err, field)
          setField(field, {
            state: null,
            error: e.kind === 'server' ? e.message : t('e.offline', { was: label(field, String(previous)) }),
          })
          if (field === 'name') setDraft(null)
        },
      },
    )
  }

  const zone = isKnownZone(data.timezone) ? data.timezone : zoneOnScreen
  const mac = browserZone()
  const nameValue = draft ?? data.name
  const nameErr = nameError ?? statusOf('name').error
  const commitName = () => {
    if (draft === null) return
    const v = draft.trim().replace(/\s+/g, ' ')
    if (v === data.name) {
      setDraft(null)
      return
    }
    if (!v) {
      setNameError(t('e.empty'))
      return
    }
    setNameError(null)
    setDraft(null)
    send('name', v)
  }

  const diff = diffFromTeam(zone, now)
  const watchlistAt = clock(hm(schedule.scout_watchlist) + diff)
  const tzErr = statusOf('timezone').error
  const langErr = statusOf('locale').error
  const themeErr = statusOf('theme').error

  return (
    <div className="st-page gn-page" data-testid="gn-page">
      <Header />

      <section className="gn-section" aria-labelledby="gn-you-title">
        <h2 className="gn-group-title" id="gn-you-title">
          {t('you')}
        </h2>
        <div className="st-group">
          <div className="gn-you-top">
            <span className="gn-mono" aria-hidden="true" data-testid="gn-mono">
              {initials(nameValue) || initials(data.name)}
            </span>
            <div className="gn-name">
              <div className="gn-label-line">
                <label className="gn-row-label" htmlFor="gn-name">
                  {t('name')}
                </label>
                <SaveMark status={statusOf('name')} field="name" />
              </div>
              <div className="gn-name-field">
                <input
                  id="gn-name"
                  name="name"
                  className={INPUT_CLASSES}
                  value={nameValue}
                  maxLength={80}
                  autoComplete="name"
                  spellCheck={false}
                  dir="auto"
                  aria-invalid={nameErr ? true : undefined}
                  aria-describedby={nameErr ? 'gn-err-name' : undefined}
                  onChange={(e) => {
                    setDraft(e.target.value)
                    if (nameError && e.target.value.trim()) setNameError(null)
                  }}
                  onBlur={commitName}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      commitName()
                    } else if (e.key === 'Escape') {
                      e.preventDefault()
                      setDraft(null)
                      setNameError(null)
                    }
                  }}
                />
              </div>
              <ErrLine id="gn-err-name" message={nameErr} />
            </div>
          </div>
          <div className="gn-row gn-email">
            <span className="gn-row-main">
              <span className="gn-row-label">{t('email')}</span>
              <span className="gn-row-sub">{t('emailSub')}</span>
            </span>
            <span className="gn-row-value">
              <bdi dir="ltr">{session.data?.email ?? ''}</bdi>
            </span>
          </div>
        </div>
        <p className="gn-group-note">{t('youNote')}</p>
      </section>

      <section className="gn-section" aria-labelledby="gn-look-title">
        <h2 className="gn-group-title" id="gn-look-title">
          {t('look')}
        </h2>
        <div className="st-group gn-look">
          <ThemeTiles value={data.theme} onChoose={(v) => send('theme', v)} describedBy={themeErr ? 'gn-err-theme' : undefined} />
          <div className="gn-look-foot">
            {statusOf('theme').state && <SaveMark status={statusOf('theme')} field="theme" />}
            <ErrLine id="gn-err-theme" message={themeErr} />
          </div>
        </div>
        <p className="gn-group-note">{t('lookNote')}</p>
      </section>

      <section className="gn-section" aria-labelledby="gn-region-title">
        <h2 className="gn-group-title" id="gn-region-title">
          {t('region')}
        </h2>
        <div className="st-group gn-region">
          <div className="gn-row">
            <span className="gn-row-main">
              <span className="gn-label-line">
                <span className="gn-row-label" id="gn-lang-label">
                  {t('lang')}
                </span>
                <SaveMark status={statusOf('locale')} field="locale" />
              </span>
              <span className="gn-row-sub">{t('langSub')}</span>
              <ErrLine id="gn-err-locale" message={langErr} />
            </span>
            <span data-testid="gn-lang">
              {/* Keyed by language: the thumb is measured once per choice, so a direction flip must remount it. */}
              <SegmentedControl
                key={lang}
                className="gn-lang"
                aria-label={t('lang')}
                value={data.locale}
                onValueChange={(v) => send('locale', v)}
                options={[
                  { value: 'en', label: <span lang="en">English</span> },
                  { value: 'ar', label: <span lang="ar">العربية</span> },
                ]}
              />
            </span>
          </div>
          <div className="gn-row">
            <span className="gn-row-main">
              <span className="gn-label-line">
                <span className="gn-row-label">{t('tz')}</span>
                <SaveMark status={statusOf('timezone')} field="timezone" />
              </span>
              <span className="gn-row-sub">{t('tzSub')}</span>
              {mac !== zone && isKnownZone(mac) && (
                <span className="gn-mac" data-testid="gn-mac">
                  <Laptop aria-hidden="true" strokeWidth={1.5} />
                  <span>{t('mac', { city: zoneCity(mac, lang), offset: zoneOffset(mac, lang, now) })}</span>
                  <Button size="sm" variant="secondary" onClick={() => send('timezone', mac)}>
                    {t('macUse', { city: zoneCity(mac, lang) })}
                  </Button>
                </span>
              )}
              <ErrLine id="gn-err-timezone" message={tzErr} />
            </span>
            <ZonePicker value={zone} macZone={mac} lang={lang} now={now} onChoose={(z) => send('timezone', z)} />
          </div>
          <div className="gn-row">
            <span className="gn-row-main">
              <span className="gn-row-label">{t('currency')}</span>
              <span className="gn-row-sub">{t('currencySub')}</span>
            </span>
            <span className="gn-row-value">{t('currencyValue')}</span>
          </div>
          <div className="gn-row gn-pv-row">
            <span className="gn-row-main">
              <span className="gn-row-label">{t('preview')}</span>
              <span className="gn-row-sub">{t('previewSub')}</span>
            </span>
            <Preview lang={lang} zone={zone} now={now} />
          </div>
        </div>
      </section>

      <section className="gn-section" aria-labelledby="gn-team-title">
        <h2 className="gn-group-title" id="gn-team-title">
          {t('team')}
        </h2>
        <div className="st-group gn-team">
          <DayLine values={schedule} frame="owner" now={now} lang={lang} />
          <div className="gn-team-foot">
            <p className="gn-team-cap" data-testid="gn-team-cap">
              {diff === 0
                ? t('teamSame')
                : t(diff < 0 ? 'teamBehind' : 'teamAhead', {
                    city: zoneCity(zone, lang),
                    span: spanWords(diff, lang),
                    time: `⁦${watchlistAt}⁩`,
                  })}
            </p>
            <Link className="gn-team-edit" to="/settings/schedule">
              <CalendarClock aria-hidden="true" strokeWidth={1.5} />
              <span>{t('teamEdit')}</span>
              <ChevronRight aria-hidden="true" strokeWidth={1.5} className="rtl:-scale-x-100" />
            </Link>
          </div>
        </div>
      </section>
    </div>
  )
}
