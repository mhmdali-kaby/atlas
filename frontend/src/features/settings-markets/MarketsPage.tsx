import { useQueryClient } from '@tanstack/react-query'
import { Bitcoin, CalendarClock, Coins, Landmark, Pickaxe, RotateCcw, Sigma, type LucideIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../lib/api'
import { formatFixed, type Lang } from '../../lib/format'
import { Button, Toggle, toast } from '../../ui'
import { cx } from '../../ui/cx'
import { Notice, useSettings, type SettingsDoc } from '../onboarding'
import { useLang, useNow } from '../portfolio/hooks'
import { formatClock } from '../portfolio/time'
import '../settings-agents/settings-agents.css'
import '../settings-connections/settings.css'
import { useDebounced } from '../settings-money/model'
import { EnvScopeNotice, useEnvScope, type EnvScope } from '../settings-risk/EnvScope'
import '../settings-money/money.css'
import { Fig, fill } from '../settings-money/parts'
import { historyKey, useSaveSettings, useSettingsHistory } from '../settings-risk/model'
import '../settings-risk/risk.css'
import { MarketsActivity } from './Activity'
import { InstrumentList } from './InstrumentList'
import { failText } from './labels'
import './markets.css'
import {
  changedKeys,
  changesFor,
  clientErrors,
  draftFrom,
  ELIGIBILITY_MAX,
  eligibilityParams,
  grouped,
  kindOf,
  LATER_KINDS,
  LIVE_KINDS,
  numError,
  serverErrors,
  stepped,
  toggled,
  useEligibility,
  usSession,
  VOLUME_MARKETS,
  volKey,
  type Draft,
  type FieldKey,
  type Kind,
  type NumKey,
} from './model'
import { LoadError, Loading, NumRow, PaneHeader, Section } from './parts'

/*
 * Settings → Markets (brief §14.5, ATL-131; design v14 js/pages/settings-markets.js): asset-class
 * switches, a minimum 24h volume per class, the max spread, the US-hours rule for stock perps, the
 * watchlist size and the allowed/blocked instrument lists. Changes are batched into one versioned
 * PATCH /api/settings of ATL-124's flat keys; a 409 keeps the owner's edits on top of the newer
 * version. Each allowed instrument shows the first filter it fails today, from ATL-155's
 * eligibility API with the unsaved draft (decision 250).
 */

const DEBOUNCE_MS = 300
const ICONS: Record<Kind, LucideIcon> = { spot: Coins, crypto_perp: Bitcoin, stock_perp: Landmark, commodity_perp: Pickaxe, futures: CalendarClock, options: Sigma }

export function MarketsPage() {
  const scope = useEnvScope()
  // One row per env: switching Demo | Live starts from that row's saved values, no draft carried over.
  return <MarketsPane key={scope.env} scope={scope} />
}

function MarketsPane({ scope }: { scope: EnvScope }) {
  const ENV: SettingsDoc['env'] = scope.env
  const { t } = useTranslation('settings-markets')
  const tt = t as unknown as (k: string, o?: Record<string, unknown>) => string
  const lang: Lang = useLang()
  const now = useNow(60_000)
  const client = useQueryClient()
  const query = useSettings(ENV)
  const history = useSettingsHistory(ENV)
  const save = useSaveSettings(ENV)
  const doc = query.data?.data

  /** `null` = no edits: the form shows the saved settings. */
  const [edits, setEdits] = useState<Draft | null>(null)
  const [srvErrors, setSrvErrors] = useState<Partial<Record<FieldKey, string>>>({})
  const [conflictVersion, setConflictVersion] = useState<number | null>(null)
  const [said, setSaid] = useState('')
  const pageRef = useRef<HTMLDivElement>(null)
  const focusError = useRef(false)

  const saved = doc ? draftFrom(doc) : null
  const d = edits ?? saved
  const errors = d ? clientErrors(d) : {}
  const liveQs = d && saved ? eligibilityParams(d, saved, ENV) : null
  const qs = useDebounced(liveQs ?? '', DEBOUNCE_MS)
  const eligibility = useEligibility(liveQs && qs ? qs : null)

  useEffect(() => {
    if (!focusError.current) return
    focusError.current = false
    pageRef.current?.querySelector<HTMLInputElement>('input[aria-invalid="true"]')?.focus()
  })

  const announce = (text: string) => {
    setSaid('')
    window.setTimeout(() => setSaid(text), 30)
  }

  const update = (patch: Partial<Draft>, fields: FieldKey[]) => {
    if (!d) return
    setEdits({ ...d, ...patch })
    if (fields.some((f) => srvErrors[f])) setSrvErrors((prev) => Object.fromEntries(Object.entries(prev).filter(([k]) => !fields.includes(k as FieldKey))))
  }

  const onSave = () => {
    if (!doc || !d || !saved || save.isPending || Object.keys(errors).length) return
    save.mutate(
      { version: doc.version, changes: changesFor(d, saved) },
      {
        onSuccess: (next) => {
          setEdits(null)
          setSrvErrors({})
          setConflictVersion(null)
          void client.invalidateQueries({ queryKey: ['settings', 'markets-eligibility'] })
          toast({ tone: 'success', title: t('saved', { v: formatFixed(next.version, 0, lang) }), body: t('savedBody') })
        },
        onError: async (err) => {
          if (err instanceof ApiError && err.status === 409) {
            // Keep the owner's edits on top of the version another window saved.
            const fresh = await query.refetch()
            void client.invalidateQueries({ queryKey: historyKey(ENV) })
            setConflictVersion(fresh.data?.data.version ?? (err.body as { current_version?: number } | null)?.current_version ?? null)
            return
          }
          const byField = serverErrors(err)
          if (Object.keys(byField).length > 0) {
            setSrvErrors(byField)
            focusError.current = true
            toast({ tone: 'error', title: t('failed'), body: t('fixFields') })
            return
          }
          toast({ tone: 'error', title: t('failed'), body: t('failedBody') })
        },
      },
    )
  }

  let body
  if (query.isPending) {
    body = <Loading />
  } else if (!doc || !d || !saved) {
    body = <LoadError onRetry={() => void query.refetch()} />
  } else {
    const changed = changedKeys(d, saved)
    const isCh = (f: FieldKey) => changed.includes(f)
    const blocking = (Object.keys(errors) as NumKey[]).length + (Object.keys(srvErrors) as FieldKey[]).filter((k) => !errors[k as NumKey]).length
    const errorOf = (f: NumKey) => (errors[f] ? tt(`err.${errors[f]}`, { max: '100,000,000,000' }) : srvErrors[f])
    const numProps = (f: NumKey) => ({
      field: f,
      text: d[f],
      error: errorOf(f),
      changed: isCh(f),
      onChange: (text: string) => update({ [f]: text } as Partial<Draft>, [f]),
      onStep: (dir: 1 | -1) => update({ [f]: stepped(f, d[f], dir, saved[f]) } as Partial<Draft>, [f]),
      onBlur: () => {
        if (kindOf(f) === 'usd' && !numError(f, d[f]) && grouped(d[f]) !== d[f]) setEdits({ ...d, [f]: grouped(d[f]) })
      },
    })

    // The whitelist's labels: only an answer for the current draft (or the one still debouncing) is used.
    const answer = eligibility.data?.data
    const warnings: Record<string, string> = {}
    if (d.whitelist.length && answer) {
      for (const e of answer.instruments) {
        if (!d.whitelist.includes(e.inst_id)) continue
        const w = failText(e, tt)
        if (w) warnings[e.inst_id] = w
      }
    }

    const s = usSession(now)
    const range = `${formatClock(s.open, lang, false)}–${formatClock(s.close, lang, false)}`

    body = (
      <>
        {conflictVersion !== null && (
          <Notice tone="warning" icon={RotateCcw} role="status" title={t('conflict.title')} data-testid="conflict-notice">
            {t('conflict.body', { v: formatFixed(conflictVersion, 0, lang) })}
          </Notice>
        )}

        <Section id="mk-classes-title" title={t('classes')} note={t('classNote')}>
          <div className="st-group" data-testid="mk-classes">
            {LIVE_KINDS.map((m) => {
              const Icon = ICONS[m]
              const on = d.enabled_markets.includes(m)
              const ch = on !== saved.enabled_markets.includes(m)
              return (
                <div key={m} className={cx('sa-row mk-class', ch && 'is-changed')} data-market={m}>
                  <span className="mk-row-icon" aria-hidden="true">
                    <Icon strokeWidth={1.75} />
                  </span>
                  <span className="sa-row-main">
                    <span className="sa-row-label" id={`mk-c-${m}`}>
                      {t(`m.${m}`)}
                      {ch && <span className="sr-only">, {t('changed')}</span>}
                    </span>
                  </span>
                  <Toggle
                    checked={on}
                    aria-label={t('use', { market: t(`m.${m}`) })}
                    onCheckedChange={(checked) => update({ enabled_markets: toggled(d.enabled_markets, m, checked) }, ['enabled_markets'])}
                  />
                </div>
              )
            })}
            {LATER_KINDS.map((m) => {
              const Icon = ICONS[m]
              return (
                <div key={m} className="sa-row mk-class is-later" data-market={m}>
                  <span className="mk-row-icon" aria-hidden="true">
                    <Icon strokeWidth={1.75} />
                  </span>
                  <span className="sa-row-main">
                    <span className="sa-row-label" id={`mk-c-${m}`}>
                      {t(`m.${m}`)}
                    </span>
                    <span className="sa-row-sub" id={`mk-c-${m}-d`} data-testid={`mk-later-${m}`}>
                      {t('later')}
                    </span>
                  </span>
                  <Toggle checked={false} disabled aria-labelledby={`mk-c-${m}`} aria-describedby={`mk-c-${m}-d`} />
                </div>
              )
            })}
          </div>
        </Section>

        <Section id="mk-vol-title" title={t('vol')} note={t('volNote')}>
          <div className="st-group" data-testid="mk-volume">
            {VOLUME_MARKETS.map((m) => {
              const off = !d.enabled_markets.includes(m)
              return (
                <NumRow
                  key={m}
                  id={`mk-vol-${m}`}
                  label={t(`m.${m}`)}
                  ariaLabel={t('volOf', { market: t(`m.${m}`) })}
                  sub={off ? t(`off.${m}`) : undefined}
                  dim={off}
                  {...numProps(volKey(m))}
                />
              )
            })}
          </div>
        </Section>

        <Section id="mk-spread-title" title={t('spread')} note={t('spreadNote')}>
          <div className="st-group">
            <NumRow id="mk-spread" label={t('maxSpread')} {...numProps('max_spread_bps')} />
          </div>
        </Section>

        <Section id="mk-hours-title" title={t('hours')} note={t('hoursNote')}>
          <div className="st-group">
            <div className={cx('sa-row mk-hours', isCh('avoid_stock_perps_outside_us_hours') && 'is-changed')}>
              <span className="sa-row-main">
                <span className="sa-row-label" id="mk-hours-label">
                  {t('avoid')}
                  {isCh('avoid_stock_perps_outside_us_hours') && <span className="sr-only">, {t('changed')}</span>}
                </span>
                <span className="sa-row-sub" id="mk-hours-sub" data-testid="mk-hours-sub">
                  {fill(t('hoursSub'), { range: <Fig>{range}</Fig>, state: t(s.isOpen ? 'openNow' : 'closedNow') })}
                </span>
              </span>
              <Toggle
                checked={d.avoid_stock_perps_outside_us_hours}
                aria-labelledby="mk-hours-label"
                aria-describedby="mk-hours-sub"
                onCheckedChange={(checked) => update({ avoid_stock_perps_outside_us_hours: checked }, ['avoid_stock_perps_outside_us_hours'])}
              />
            </div>
          </div>
        </Section>

        <Section id="mk-wl-title" title={t('wl')}>
          <div className="st-group">
            <NumRow id="mk-wl" label={t('wlMax')} sub={t('wlSub')} {...numProps('max_instruments_per_day')} />
          </div>
        </Section>

        <Section id="mk-lists-title" title={t('lists')}>
          <div className="st-group mk-lists">
            <InstrumentList
              which="whitelist"
              env={ENV}
              list={d.whitelist}
              saved={saved.whitelist}
              other={d.blocklist}
              warnings={warnings}
              serverError={srvErrors.whitelist}
              changed={isCh('whitelist')}
              note={
                d.whitelist.length
                  ? `${t('allowOnly')}${d.whitelist.length > ELIGIBILITY_MAX ? ` ${t('failFirst', { n: ELIGIBILITY_MAX })}` : ''}`
                  : t('allowNote')
              }
              onChange={(next) => update({ whitelist: next }, ['whitelist'])}
              announce={announce}
            />
            <InstrumentList
              which="blocklist"
              env={ENV}
              list={d.blocklist}
              saved={saved.blocklist}
              other={d.whitelist}
              serverError={srvErrors.blocklist}
              changed={isCh('blocklist')}
              note={t('denyNote')}
              onChange={(next) => update({ blocklist: next }, ['blocklist'])}
              announce={announce}
            />
          </div>
        </Section>

        <Section id="mk-act-title" title={t('activity')} note={t('activityNote')}>
          <MarketsActivity entries={history.data?.data} loading={history.isPending} lang={lang} />
        </Section>

        {(changed.length > 0 || blocking > 0) && (
          <div className={cx('st-save-bar sa-bar', blocking > 0 && 'is-blocked')} role="region" aria-label={t('unsaved', { count: Math.max(1, changed.length) })} data-testid="save-bar">
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
                setSrvErrors({})
              }}
            >
              {t('revert')}
            </Button>
            <Button variant="primary" size="sm" loading={save.isPending} disabled={blocking > 0 || changed.length === 0} onClick={onSave}>
              {save.isPending ? t('saving') : t('save')}
            </Button>
          </div>
        )}
      </>
    )
  }

  return (
    <div ref={pageRef} className="st-page st-risk ml-page mk-page">
      <PaneHeader scope={scope} version={doc && !query.isPending ? doc.version : null} />
      <EnvScopeNotice scope={scope} />
      {body}
      <div className="sr-only" aria-live="polite" data-testid="mk-live">
        {said}
      </div>
    </div>
  )
}
