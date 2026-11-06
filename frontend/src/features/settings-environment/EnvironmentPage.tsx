import {
  ArrowLeftRight,
  ArrowRight,
  CalendarCheck,
  CalendarClock,
  Check,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CirclePause,
  FlaskConical,
  Hand,
  Info,
  OctagonX,
  Plug,
  Send,
  X,
} from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { useEnvironment, type EnvironmentDoc, type PreflightItem } from '../../app/shell/tradingEnv'
import { Button, Seal, Skeleton, toast } from '../../ui'
import { useConnections, type Connection } from '../onboarding'
import { useLang } from '../portfolio/hooks'
import '../settings-connections/settings.css'
import { fill } from '../settings-money/parts'
import './environment.css'
import { dayText, FIX, timeText, useAttest, type AttestedKey } from './model'
import { SwitchToDemoSheet, SwitchToLiveSheet } from './sheets'

/*
 * Settings → Environment (ATL-239; design v21 js/pages/settings-environment.js, Live readiness §4).
 * The two accounts, the one action the owner can take (Switch to live… only when all eight
 * pre-flight checks pass, or Switch to demo…), then the pre-flight itself in the API's order:
 * five computed rows with a fix link and three the owner ticks, each saving on its own.
 */

const Fig = ({ children }: { children: ReactNode }) => (
  <bdi dir="ltr" className="tabular-nums">
    {children}
  </bdi>
)

function PaneHeader() {
  const { t } = useTranslation('settings-environment')
  return (
    <header className="st-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <ArrowLeftRight strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav')}</h2>
        <p className="max-w-[54ch] text-subheadline text-label-secondary">{t('intro')}</p>
      </div>
    </header>
  )
}

function Section({ id, title, note, children }: { id: string; title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section className="grid" aria-labelledby={id}>
      <h2 id={id} className="px-4 pb-2 text-footnote font-semibold text-label-secondary">
        {title}
      </h2>
      {children}
      {note && <p className="px-4 pt-2 text-footnote text-label-secondary">{note}</p>}
    </section>
  )
}

const readyCount = (items: PreflightItem[]) => items.filter((i) => i.ok).length

function Tile({ kind, doc, connection }: { kind: 'demo' | 'live'; doc: EnvironmentDoc; connection: Connection | null }) {
  const { t } = useTranslation('settings-environment')
  const current = doc.active_env === kind
  const n = readyCount(doc.preflight.items)
  const noKey = kind === 'live' && connection === null
  const name = t(kind === 'demo' ? 'okxDemo' : 'okxLive')
  const last4 = connection?.uid_last4 ?? connection?.api_key_last4 ?? '----'
  const verified = connection !== null && connection.last_verified_at !== null

  let state: ReactNode
  if (current) {
    state = (
      <span className={`env-now is-${kind}`} data-testid={`env-now-${kind}`}>
        <span className="dot" aria-hidden="true" />
        {t('now')}
      </span>
    )
  } else if (kind === 'demo') {
    state = <span className="env-tile-state">{t('backAny')}</span>
  } else if (noKey) {
    state = (
      <span className="env-tile-state is-warn">
        <Plug aria-hidden="true" strokeWidth={1.75} />
        <span>{t('noKey')}</span>
      </span>
    )
  } else if (n === 8) {
    state = (
      <span className="env-tile-state is-ok">
        <CircleCheck aria-hidden="true" strokeWidth={1.75} />
        <span>{t('ready')}</span>
      </span>
    )
  } else {
    state = (
      <span className="env-tile-state is-warn">
        <CircleDashed aria-hidden="true" strokeWidth={1.75} />
        <span>{n === 0 ? t('notReady0') : n === 1 ? t('notReady1') : t('notReady', { n })}</span>
      </span>
    )
  }

  return (
    <div className={`env-tile is-${kind}${current ? ' is-current' : ''}`} data-testid={`env-tile-${kind}`}>
      <span className="env-tile-seal">
        {noKey ? (
          <span className="env-seal-empty" aria-hidden="true">
            <Plug strokeWidth={1.5} />
          </span>
        ) : (
          <span role="img" aria-label={t('sealLabel', { name })} className="relative grid place-items-center">
            <Seal seed={`okx:${kind}:${last4}`} size={72} />
            <span aria-hidden="true" className="st-seal-dot" data-state={verified ? 'ok' : 'stale'} />
          </span>
        )}
      </span>
      <div className="env-tile-text">
        <p className="env-tile-name">{name}</p>
        <p className="env-tile-meta">
          {noKey ? (
            <span>{t('noKeyShort')}</span>
          ) : (
            <bdi dir="ltr" className="tabular-nums">
              •••• {last4}
            </bdi>
          )}
          <span className="env-tile-money">{t(kind === 'demo' ? 'paper' : 'real')}</span>
        </p>
        {state}
      </div>
    </div>
  )
}

function Hero({
  doc,
  demo,
  live,
  onToLive,
  onToDemo,
}: {
  doc: EnvironmentDoc
  demo: Connection | null
  live: Connection | null
  onToLive: () => void
  onToDemo: () => void
}) {
  const { t } = useTranslation('settings-environment')
  const lang = useLang()
  const isLive = doc.active_env === 'live'
  const n = readyCount(doc.preflight.items)
  const allPass = doc.preflight.ok && n === 8

  let foot: ReactNode
  if (isLive) {
    const ends = doc.first_month_ends_at
    const started = doc.switched_at ?? doc.live_started_at
    foot = (
      <>
        <div className="env-foot-text">
          {ends && (
            <p className="env-foot-main" data-testid="env-month">
              {doc.in_first_live_month ? (
                <CalendarClock aria-hidden="true" strokeWidth={1.75} />
              ) : (
                <CalendarCheck aria-hidden="true" strokeWidth={1.75} />
              )}
              <span>{t(doc.in_first_live_month ? 'month' : 'monthDone', { date: dayText(ends, lang, true) })}</span>
            </p>
          )}
          {started && (
            <p className="env-foot-sub">
              {fill(t('switched', { date: '{{date}}', time: '{{time}}' }), {
                date: dayText(started, lang),
                time: <Fig>{timeText(started, lang)}</Fig>,
              })}
            </p>
          )}
        </div>
        <Button variant="secondary" icon={<FlaskConical strokeWidth={1.5} />} onClick={onToDemo}>
          {t('toDemo')}
        </Button>
      </>
    )
  } else {
    const why = allPass ? t('allReady') : n === 0 ? t('why0') : n === 1 ? t('why1') : t('why', { n })
    foot = (
      <>
        <div className="env-foot-text">
          <p className={`env-foot-main${allPass ? ' is-ok' : ''}`} id="env-why">
            {allPass ? <CircleCheck aria-hidden="true" strokeWidth={1.75} /> : <Info aria-hidden="true" strokeWidth={1.75} />}
            <span>{why}</span>
          </p>
        </div>
        <Button
          variant="secondary"
          className="env-btn-live"
          disabled={!allPass}
          aria-describedby="env-why"
          onClick={onToLive}
        >
          {t('toLive')}
        </Button>
      </>
    )
  }

  return (
    <Section id="env-acc-title" title={t('accounts')}>
      <div className="st-group env-hero">
        <div className="env-pair">
          <Tile kind="demo" doc={doc} connection={demo} />
          <span className={`env-arrow${isLive ? ' is-back' : ''}`} aria-hidden="true">
            <ArrowRight />
          </span>
          <Tile kind="live" doc={doc} connection={live} />
        </div>
        <div className="env-foot">{foot}</div>
      </div>
    </Section>
  )
}

function Word({ ok }: { ok: boolean }) {
  const { t } = useTranslation('settings-environment')
  return ok ? (
    <span className="env-word is-ok">
      <Check aria-hidden="true" />
      <span>{t('wOk')}</span>
    </span>
  ) : (
    <span className="env-word is-no">{t('wNo')}</span>
  )
}

function Confirmed({ at }: { at: string }) {
  const { t } = useTranslation('settings-environment')
  const lang = useLang()
  return (
    <>
      {fill(t('confirmed', { date: '{{date}}', time: '{{time}}' }), {
        date: dayText(at, lang),
        time: <Fig>{timeText(at, lang)}</Fig>,
      })}
    </>
  )
}

function Row({
  item,
  readOnly = false,
  busy = false,
  onTick,
}: {
  item: PreflightItem
  readOnly?: boolean
  busy?: boolean
  onTick?: (key: AttestedKey, on: boolean) => void
}) {
  const { t } = useTranslation('settings-environment')
  const uid = useId()
  const label = t(`i.${item.key}`)
  const attested = item.kind === 'attested'

  if (attested && !readOnly) {
    const key = item.key as AttestedKey
    const cb = `env-cb-${key}-${uid}`
    const sub = `env-d-${key}-${uid}`
    return (
      <div className={`env-row is-att ${item.ok ? 'is-ok' : 'is-no'}`} data-item={key} data-testid={`env-item-${key}`}>
        <span className="env-lead">
          <input
            type="checkbox"
            className="env-cb"
            id={cb}
            checked={item.ok}
            disabled={busy}
            aria-describedby={sub}
            onChange={(e) => onTick?.(key, e.target.checked)}
          />
        </span>
        <div className="env-row-main">
          <label className="env-row-label" htmlFor={cb}>
            {label}
          </label>
          <p className="env-row-sub" id={sub}>
            {key === 'demo_reviewed'
              ? fill(t('a.demo_reviewed', { perf: '{{perf}}', journal: '{{journal}}' }), {
                  perf: <Link to="/portfolio/performance">{t('perf')}</Link>,
                  journal: <Link to="/journal">{t('journal')}</Link>,
                })
              : t(`a.${key}`)}
          </p>
          {item.ok && item.confirmed_at && (
            <p className="env-when">
              <Confirmed at={item.confirmed_at} />
            </p>
          )}
        </div>
        <Word ok={item.ok} />
      </div>
    )
  }

  const fix = !item.ok && !readOnly ? FIX[item.key] : undefined
  return (
    <div className={`env-row ${item.ok ? 'is-ok' : 'is-no'}`} data-item={item.key} data-testid={`env-item-${item.key}`}>
      <span className="env-lead">
        <span className={`env-disc ${item.ok ? 'is-ok' : 'is-no'}`} aria-hidden="true">
          {item.ok ? <Check /> : <X />}
        </span>
      </span>
      <div className="env-row-main">
        <p className="env-row-label">{label}</p>
        <p className="env-row-sub">{attested && item.ok && item.confirmed_at ? <Confirmed at={item.confirmed_at} /> : item.detail}</p>
        {fix && (
          <Button asChild variant="secondary" size="sm" className="env-fix">
            <Link to={fix}>
              <span>{t('open', { page: t(`page.${item.key}`) })}</span>
              <ChevronRight aria-hidden="true" className="rtl:-scale-x-100" />
            </Link>
          </Button>
        )}
      </div>
      <Word ok={item.ok} />
    </div>
  )
}

function Preflight({ items, onTick, busyKey }: { items: PreflightItem[]; onTick: (key: AttestedKey, on: boolean) => void; busyKey: AttestedKey | null }) {
  const { t } = useTranslation('settings-environment')
  const n = readyCount(items)
  return (
    <Section id="env-pre-title" title={t('pre')} note={t('preNote')}>
      <div className="st-group env-pre" data-testid="env-preflight">
        <div className="env-pre-head">
          <p className="env-count">
            <b>
              <Fig>{n}</Fig>
            </b>
            <span>{t('ofReady')}</span>
          </p>
          <span className="env-meter" role="img" aria-label={t('meter', { n })}>
            {items.map((i) => (
              <i key={i.key} className={i.ok ? 'is-ok' : 'is-no'} />
            ))}
          </span>
        </div>
        {items.map((i) => (
          <Row key={i.key} item={i} onTick={onTick} busy={busyKey === i.key} />
        ))}
      </div>
    </Section>
  )
}

function PassedSummary({ items }: { items: PreflightItem[] }) {
  const { t } = useTranslation('settings-environment')
  const [open, setOpen] = useState(false)
  return (
    <Section id="env-pre-title" title={t('pre')}>
      <div className="st-group env-pre">
        <div className="env-row is-ok env-sum">
          <span className="env-lead">
            <span className="env-disc is-ok" aria-hidden="true">
              <Check />
            </span>
          </span>
          <div className="env-row-main">
            <p className="env-row-label">{t('sumTitle')}</p>
            <p className="env-row-sub">{t('sumBody')}</p>
          </div>
          <button
            type="button"
            className="env-disclosure"
            aria-expanded={open}
            aria-controls="env-checks"
            onClick={() => setOpen((v) => !v)}
          >
            <ChevronRight aria-hidden="true" />
            <span>{t(open ? 'hideChecks' : 'showChecks')}</span>
          </button>
        </div>
        <div id="env-checks" className="env-checks" hidden={!open}>
          {items.map((i) => (
            <Row key={i.key} item={i} readOnly />
          ))}
        </div>
      </div>
    </Section>
  )
}

const CHANGES = [
  [Hand, 'approve'],
  [CalendarClock, 'month'],
  [Send, 'telegram'],
  [CirclePause, 'pause'],
  [OctagonX, 'stop'],
] as const

function Changes({ live }: { live: boolean }) {
  const { t } = useTranslation('settings-environment')
  return (
    <Section id="env-ch-title" title={t(live ? 'changesLive' : 'changes')}>
      <div className="st-group">
        {CHANGES.map(([Icon, k]) => (
          <div key={k} className="env-change">
            <Icon aria-hidden="true" />
            <span>{t(`c.${k}`)}</span>
          </div>
        ))}
      </div>
    </Section>
  )
}

function LoadingBody() {
  return (
    <div className="grid gap-7" aria-busy="true" data-testid="env-loading">
      <Skeleton className="h-[176px] rounded-card" />
      <div className="grid gap-2">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-[52px] rounded-card" />
        ))}
      </div>
    </div>
  )
}

function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('settings-environment')
  return (
    <section role="alert" className="st-group st-empty" data-testid="env-error">
      <span className="st-empty-icon is-error" aria-hidden="true">
        <CircleAlert strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{t('err.title')}</h2>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">{t('err.body')}</p>
      <Button variant="secondary" className="mt-2" onClick={onRetry}>
        {t('err.retry')}
      </Button>
    </section>
  )
}

export function EnvironmentPage() {
  const { t } = useTranslation('settings-environment')
  const query = useEnvironment()
  const connections = useConnections()
  const [sheet, setSheet] = useState<'live' | 'demo' | null>(null)
  const [busyKey, setBusyKey] = useState<AttestedKey | null>(null)
  const attest = useAttest((key) =>
    toast({ tone: 'error', title: t('at.fail'), body: t('at.failBody', { item: t(`i.${key}`) }) }),
  )
  const doc = query.data?.data
  const okx = (env: 'demo' | 'live') => connections.data?.data.find((c) => c.exchange === 'okx' && c.env === env) ?? null

  const tick = (key: AttestedKey, on: boolean) => {
    setBusyKey(key)
    attest.mutate({ key, on }, { onSettled: () => setBusyKey(null) })
  }

  let body: ReactNode
  if (query.isPending) body = <LoadingBody />
  else if (!doc) body = <LoadError onRetry={() => void query.refetch()} />
  else {
    const items = doc.preflight.items
    const isLive = doc.active_env === 'live'
    body = (
      <>
        <Hero doc={doc} demo={okx('demo')} live={okx('live')} onToLive={() => setSheet('live')} onToDemo={() => setSheet('demo')} />
        {isLive ? <PassedSummary items={items} /> : <Preflight items={items} onTick={tick} busyKey={busyKey} />}
        <Changes live={isLive} />
        {sheet === 'live' && (
          <SwitchToLiveSheet
            confirmationText={doc.confirmation_text}
            uidLast4={okx('live')?.uid_last4 ?? null}
            onClose={() => setSheet(null)}
          />
        )}
        {sheet === 'demo' && <SwitchToDemoSheet onClose={() => setSheet(null)} />}
      </>
    )
  }

  return (
    <div className="st-page env-page" data-testid="environment-page">
      <PaneHeader />
      {body}
    </div>
  )
}

