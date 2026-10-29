import { CircleCheck, CircleMinus, CirclePause, Clock, KeyRound, RotateCcw, TriangleAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button, Spinner, Toggle } from '../../ui'
import {
  disabledUntil,
  isFeed,
  lastItem,
  lastSuccess,
  monogram,
  PROVIDER_SITES,
  providerName,
  sum24,
  type ApiSource,
  type RowStatus,
} from './model'
import { cssKey, Fig, NS, Tip, Tr, useSourceName, useTimes } from './parts'

const ICON = {
  working: CircleCheck,
  standby: CirclePause,
  retrying: RotateCcw,
  failing: TriangleAlert,
  needs_key: KeyRound,
  off: CircleMinus,
  never_run: Clock,
} as const

function StatusIcon({ st, stale }: { st: RowStatus; stale: boolean }) {
  if (st === 'starting') return <Spinner className="src-spin" />
  // Stalled collector (TL, design v6 review): no green check on a row whose "working" is out of date.
  const Icon = st === 'working' && stale ? Clock : ICON[st]
  return <Icon aria-hidden="true" strokeWidth={2} className={stale && st === 'working' ? 'is-stale' : undefined} />
}

function Detail({ s, st, now }: { s: ApiSource; st: RowStatus; now: number }) {
  const { t } = useTranslation(NS)
  const time = useTimes(now)
  const success = lastSuccess(s)
  if (st === 'working' && success !== null) return <>{t('d.checked', { ago: time.ago(success) })}</>
  if (st === 'standby') return <Tr k="d.standby" values={{ provider: providerName('finnhub') }} />
  if (st === 'retrying') return <Tr k="d.failed" count={s.consecutive_failures} />
  if (st === 'failing') return <>{t('d.next', { within: time.within(s.poll_seconds) })}</>
  if (st === 'starting' || st === 'never_run') return <>{t('d.first', { within: time.within(s.poll_seconds) })}</>
  if (st === 'needs_key' && PROVIDER_SITES[s.provider]) return <Tr k="d.free" values={{ site: PROVIDER_SITES[s.provider] }} />
  return null
}

function Strip({ s, st, now }: { s: ApiSource; st: RowStatus; now: number }) {
  const { t } = useTranslation(NS)
  const time = useTimes(now)
  const hourly = s.hourly_24h ?? []
  const n = sum24(s)
  const max = Math.max(4, ...hourly)
  const newest = lastItem(s)
  const label = n
    ? t('strip.label', { headlines: t('headlines', { count: n }), time: time.when(newest ?? now) })
    : t('strip.none')
  const muted = st === 'off' || st === 'needs_key'
  return (
    <>
      <Tip label={label}>
        <span className={muted ? 'src-strip is-muted' : 'src-strip'} role="img" aria-label={label} data-testid="src-strip">
          {hourly.map((v, i) => (
            <i
              key={i}
              className={[v ? '' : 'is-zero', i === hourly.length - 1 ? 'is-now' : ''].join(' ').trim() || undefined}
              style={{ ['--h' as string]: (v / max).toFixed(3) }}
            />
          ))}
        </span>
      </Tip>
      <span className="src-count" aria-hidden="true">
        <Fig>{n.toLocaleString('en-US')}</Fig>
      </span>
    </>
  )
}

export function SourceRow({
  s,
  st,
  now,
  stale,
  onToggle,
  onKey,
}: {
  s: ApiSource
  st: RowStatus
  now: number
  /** The summary says the collector has stopped. */
  stale: boolean
  onToggle: (s: ApiSource, enabled: boolean) => void
  /** Opens the provider's key sheet (ATL-69): add on a Needs a key row, replace from the error line. */
  onKey: (provider: string, replace: boolean) => void
}) {
  const { t } = useTranslation(NS)
  const nameOf = useSourceName()
  const time = useTimes(now)
  const name = nameOf(s)
  const mono = monogram(s)
  const subId = `src-sub-${cssKey(s.key)}`
  const locked = st === 'needs_key'
  const until = disabledUntil(s)
  const showStrip = isFeed(s) && !(['off', 'never_run', 'starting'].includes(st) && sum24(s) === 0) && st !== 'needs_key'
  const showError = (st === 'retrying' || st === 'failing') && Boolean(s.last_error)
  const swLabel = t(locked ? 'sw.needsKey' : 'sw.label', { name })

  return (
    <div className={`src-row is-${st}`} id={`src-${cssKey(s.key)}`} data-testid="src-row" data-key={s.key} data-status={st}>
      <span className={mono.length > 2 ? 'src-mono is-long' : 'src-mono'} aria-hidden="true">
        {mono}
      </span>
      <span className="src-main">
        <span className="src-name">
          <bdi>{name}</bdi>
        </span>
        <span className="src-sub" id={subId}>
          <span className={`src-status is-${st}`} data-testid="src-status">
            <StatusIcon st={st} stale={stale} />
            <span>{st === 'retrying' && until !== null ? <Tr k="st.retrying" values={{ time: time.when(until) }} /> : t(`st.${st}`)}</span>
          </span>
          {st !== 'off' && (
            <span className="src-detail">
              {t('sep')}
              <Detail s={s} st={st} now={now} />
            </span>
          )}
        </span>
        {showError && (
          <span className="src-error" data-testid="src-error">
            <span className="src-error-lead">{t('error')}</span>
            <Tip label={s.last_error!}>
              <bdi dir="ltr" className="src-error-text" tabIndex={0}>
                {s.last_error}
              </bdi>
            </Tip>
            {s.requires_key && s.has_key && (
              <Button size="sm" variant="plain" className="src-error-key" data-testid="src-replace-key" onClick={() => onKey(s.provider, true)}>
                {t('keys.replaceKey')}
              </Button>
            )}
          </span>
        )}
      </span>
      <span className="src-yield">
        {showStrip && <Strip s={s} st={st} now={now} />}
        {locked && (
          <Button size="sm" variant="secondary" icon={<KeyRound strokeWidth={1.5} />} data-testid="src-add-key" onClick={() => onKey(s.provider, false)}>
            {t('keys.add')}
          </Button>
        )}
      </span>
      <Toggle
        className="src-switch"
        checked={s.enabled && !locked}
        disabled={locked}
        aria-label={swLabel}
        aria-describedby={subId}
        data-testid="src-switch"
        onCheckedChange={(v) => onToggle(s, v)}
      />
    </div>
  )
}
