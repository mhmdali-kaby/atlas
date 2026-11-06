import { useMutation } from '@tanstack/react-query'
import {
  ArrowLeftRight,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Plug,
  RadioTower,
  RefreshCw,
  Replace,
  ShieldAlert,
  Trash2,
} from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { api } from '../../lib/api'
import { useOwnerZone } from '../../lib/ownerZone'
import { zoneCity } from '../../lib/zones'
import { Button, Seal, Sheet, SheetClose, Spinner, toast } from '../../ui'
import { cx } from '../../ui/cx'
import {
  AccountRows,
  errorSlug,
  Notice,
  TestFailure,
  WITHDRAW_WARNING,
  type AccountRow,
  type Connection,
  type ConnectionTestResponse,
} from '../onboarding'
import { useLang, useNow } from '../portfolio/hooks'
import { formatAgo, formatClock, formatDayTime } from '../portfolio/time'
import { isLiveVerified, SERVICE_ERRORS, useStoreConnection, type Failure } from './shared'
import { KeysSheet, RemoveSheet } from './sheets'

/*
 * Settings → Connections, Live account (ATL-241, design js/pages/settings.js liveSection). The
 * same pieces as the demo card, with the live rule: a key that can withdraw is refused (red, not
 * verified) where demo only warns (decision 240). Verified means OKX passed the test and listed
 * no withdraw (last_verified_at and withdraw_off_verified_at, ATL-235). Adding a key never
 * switches the environment; the line under a verified key links to Settings → Environment.
 */

/** "Trading now", on the account Atlas trades on once there are two to tell apart (design st-trading). */
export function TradingBadge({ env }: { env: Connection['env'] }) {
  const { t } = useTranslation('settings')
  return (
    <span className="st-trading" data-env={env} data-testid={`trading-${env}`}>
      {t('conn.trading')}
    </span>
  )
}

type LiveState = 'testing' | 'verified' | 'refused' | 'failed' | 'untested'

export function LiveSection({ connection }: { connection: Connection | null }) {
  const { t } = useTranslation('settings')
  const [removed, setRemoved] = useState(false)
  // The connect sheet lives here, not in the empty state: saving the key swaps the empty state
  // for the card, and a refused key must keep the sheet open with its error.
  const [connectOpen, setConnectOpen] = useState(false)
  const [opened, setOpened] = useState(0)
  return (
    <section className="grid" aria-labelledby="st-live-title" data-testid="live-section">
      <h2 id="st-live-title" className="px-4 pb-2 text-footnote font-semibold text-label-secondary">
        {t('lv.title')}
      </h2>
      {connection ? (
        <LiveConnected key={connection.id} connection={connection} onRemoved={() => setRemoved(true)} />
      ) : (
        <LiveEmpty
          focusOnMount={removed}
          onConnect={() => {
            setOpened((n) => n + 1)
            setConnectOpen(true)
          }}
        />
      )}
      <p className="px-4 pt-2 text-footnote text-label-secondary">{t('lv.footnote')}</p>
      <KeysSheet key={`lconnect-${opened}`} open={connectOpen} onOpenChange={setConnectOpen} connection={null} env="live" />
    </section>
  )
}

function LiveEmpty({ focusOnMount, onConnect }: { focusOnMount: boolean; onConnect: () => void }) {
  const { t } = useTranslation('settings')
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (focusOnMount) button.current?.focus()
  }, [focusOnMount])

  return (
    <div className="st-group st-empty" data-testid="live-empty">
      <span className="st-empty-icon is-live" aria-hidden="true">
        <RadioTower strokeWidth={1.5} />
      </span>
      <h3 className="text-title-3">{t('lv.noneTitle')}</h3>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">{t('lv.noneBody')}</p>
      <Button
        ref={button}
        variant="primary"
        icon={<Plug />}
        className="mt-2"
        onClick={onConnect}
      >
        {t('lv.connect')}
      </Button>
    </div>
  )
}

function LiveConnected({ connection, onRemoved }: { connection: Connection; onRemoved: () => void }) {
  const { t } = useTranslation('settings')
  const { t: tOn } = useTranslation('onboarding')
  const lang = useLang()
  const now = useNow(30_000)
  const zone = useOwnerZone()
  const store = useStoreConnection()
  const activeEnv = useTradingEnv()
  const [localFailure, setLocalFailure] = useState<Failure | null>(null)
  const [sheet, setSheet] = useState<'replace' | 'remove' | null>(null)
  const [opened, setOpened] = useState(0)
  const openSheet = (which: 'replace' | 'remove') => {
    setOpened((n) => n + 1)
    setSheet(which)
  }

  const test = useMutation({
    mutationFn: () => api.post<ConnectionTestResponse>(`/api/connections/${connection.id}/test`),
    onMutate: () => setLocalFailure(null),
    onSuccess: (result) => {
      store.put(result.connection)
      if (result.ok) {
        toast({
          tone: 'success',
          title: t('lv.okToast'),
          body: t('lv.okToastBody', { time: formatClock(result.verified_at ?? Date.now(), lang, false) }),
        })
      } else {
        const error = result.error ?? 'unknown'
        setLocalFailure({ error, okxCode: result.okx_code })
        toast(
          error === WITHDRAW_WARNING
            ? { tone: 'error', title: t('lv.refusedToast'), body: t('lv.refusedToastBody') }
            : { tone: 'error', title: t('lv.failedToast') },
        )
      }
    },
    onError: (err) => {
      setLocalFailure({ error: errorSlug(err), okxCode: null })
      toast({ tone: 'error', title: t('lv.failedToast') })
    },
  })

  const testing = test.isPending
  const failure: Failure | null =
    localFailure ?? (connection.last_test_error ? { error: connection.last_test_error, okxCode: null } : null)
  const state: LiveState = testing
    ? 'testing'
    : failure?.error === WITHDRAW_WARNING
      ? 'refused'
      : failure
        ? 'failed'
        : isLiveVerified(connection)
          ? 'verified'
          : 'untested'
  const verified = state === 'verified'
  const tradingLive = activeEnv === 'live'
  const last4 = connection.uid_last4 ?? '----'

  const leading: AccountRow[] = [
    [tOn('acct.label'), <bdi key="label">{connection.label}</bdi>],
    [
      tOn('acct.key'),
      <bdi key="key" dir="ltr" className="fig" data-testid="live-key-tail">
        •••• {connection.api_key_last4}
      </bdi>,
    ],
  ]
  const verifiedRow: AccountRow = [
    tOn('acct.verified'),
    connection.last_verified_at ? (
      <time
        key="verified"
        dateTime={connection.last_verified_at}
        title={t('conn.verifiedTip', { city: zoneCity(zone, lang), zone })}
        data-testid="live-last-verified"
      >
        {formatDayTime(connection.last_verified_at, lang, true)}
      </time>
    ) : (
      <span key="verified">{t('conn.never')}</span>
    ),
  ]

  return (
    <>
      <div className="st-group" data-testid="live-card" data-state={state}>
        <div className="flex items-center gap-4 px-5 py-[18px]">
          <span role="img" aria-label={t('lv.sealLabel', { last4 })} className="relative grid shrink-0 place-items-center">
            <Seal seed={`okx:live:${last4}`} size={64} />
            <span aria-hidden="true" className="st-seal-dot" data-state={verified ? 'ok' : testing ? 'pending' : 'stale'} />
          </span>
          <div className="grid min-w-0 gap-1">
            <h3 className="st-account-name text-title-3 font-semibold">
              <span className="truncate">{connection.label}</span>
              {tradingLive && <TradingBadge env="live" />}
            </h3>
            <LiveStatus state={state} failure={failure} lastVerified={connection.last_verified_at} now={now} />
          </div>
        </div>
        <div className="st-actions">
          <Button
            size="sm"
            variant="secondary"
            icon={<RefreshCw />}
            loading={testing}
            onClick={() => !testing && test.mutate()}
          >
            {testing ? t('conn.testing') : t('conn.test')}
          </Button>
          <Button size="sm" variant="secondary" icon={<Replace />} disabled={testing} onClick={() => openSheet('replace')}>
            {t('conn.replace')}
          </Button>
          <span className="flex-1" />
          <Button size="sm" variant="plain-destructive" icon={<Trash2 />} disabled={testing} onClick={() => openSheet('remove')}>
            {t('conn.remove')}
          </Button>
        </div>
      </div>
      <p className="sr-only" aria-live="polite">
        {testing ? t('lv.testingLine') : ''}
      </p>

      {state === 'refused' && (
        <div className="mt-3">
          <Notice
            tone="error"
            role="alert"
            icon={ShieldAlert}
            title={t('lv.withdraw.title')}
            data-testid="live-refused"
            actions={
              <Button size="sm" variant="secondary" icon={<Replace />} onClick={() => openSheet('replace')}>
                {t('conn.replace')}
              </Button>
            }
          >
            {t('lv.withdraw.body')}
          </Notice>
        </div>
      )}
      {state === 'failed' && failure && (
        <div className="mt-3">
          <TestFailure error={failure.error} okxCode={failure.okxCode} env="live" />
        </div>
      )}

      {!testing && (
        <div className="mt-3" data-testid="live-rows">
          <AccountRows
            connection={connection}
            className="st-group st-rows"
            show={verified ? 'all' : state === 'refused' ? 'permissions' : 'none'}
            leading={leading}
            trailing={verified ? [verifiedRow] : []}
          />
        </div>
      )}

      {verified && (
        <Link to="/settings/environment" className="st-group st-envline mt-3" data-testid="live-env-link">
          <span className="st-envline-icon" aria-hidden="true">
            {tradingLive ? <RadioTower strokeWidth={1.75} /> : <ArrowLeftRight strokeWidth={1.75} />}
          </span>
          <span className="grid min-w-0 flex-1 gap-px">
            <span className="text-callout text-label">{t(tradingLive ? 'lv.envLiveTitle' : 'lv.envTitle')}</span>
            <span className="text-footnote text-label-secondary">{t(tradingLive ? 'lv.envLiveSub' : 'lv.envSub')}</span>
          </span>
          <ChevronRight aria-hidden="true" className="st-envline-chev rtl:-scale-x-100" />
        </Link>
      )}

      <KeysSheet
        key={`lreplace-${opened}`}
        open={sheet === 'replace'}
        onOpenChange={(open) => {
          setSheet(open ? 'replace' : null)
          if (!open) setLocalFailure(null)
        }}
        connection={connection}
      />
      {tradingLive ? (
        <RemoveBlockedSheet key={`lremove-${opened}`} open={sheet === 'remove'} onOpenChange={(open) => setSheet(open ? 'remove' : null)} />
      ) : (
        <RemoveSheet
          key={`lremove-${opened}`}
          open={sheet === 'remove'}
          onOpenChange={(open) => setSheet(open ? 'remove' : null)}
          connection={connection}
          onRemoved={onRemoved}
        />
      )}
    </>
  )
}

function LiveStatus({
  state,
  failure,
  lastVerified,
  now,
}: {
  state: LiveState
  failure: Failure | null
  lastVerified: string | null
  now: number
}) {
  const { t } = useTranslation('settings')
  const lang = useLang()
  const base = 'inline-flex items-center gap-1.5 text-subheadline text-label-secondary [&_svg]:size-4 [&_svg]:shrink-0'
  let content: ReactNode
  let tone = ''
  if (state === 'testing') {
    content = (
      <>
        <Spinner />
        <span>{t('lv.testingLine')}</span>
      </>
    )
  } else if (state === 'verified' && lastVerified) {
    const ms = Math.max(0, now - Date.parse(lastVerified))
    const ago = ms < 60_000 ? t('conn.justNow') : formatAgo(ms, lang)
    content = (
      <>
        <CircleCheck aria-hidden="true" strokeWidth={2} className="text-profit-ink" />
        <span>{t('lv.verified', { ago })}</span>
      </>
    )
  } else if (state === 'refused' || state === 'failed') {
    tone = 'text-loss-ink'
    const text =
      state === 'refused' ? t('lv.notVerified') : failure && SERVICE_ERRORS.has(failure.error) ? t('lv.cantTest') : t('lv.failedLine')
    content = (
      <>
        {state === 'refused' ? <ShieldAlert aria-hidden="true" strokeWidth={2} /> : <CircleAlert aria-hidden="true" strokeWidth={2} />}
        <span>{text}</span>
      </>
    )
  } else {
    content = <span>{t('lv.untested')}</span>
  }
  return (
    <p className={cx(base, tone)} data-testid="live-status" data-state={state}>
      {content}
    </p>
  )
}

/** While Atlas trades live, the live key can't be removed: the owner switches to demo first. */
function RemoveBlockedSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation('settings')
  const navigate = useNavigate()
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      tone="live"
      icon={<RadioTower strokeWidth={1.5} />}
      title={t('lv.rm.title')}
      description={t('lv.rm.blocked')}
      actions={
        <>
          <SheetClose>
            <Button variant="secondary">{t('cancel')}</Button>
          </SheetClose>
          <Button
            variant="primary"
            onClick={() => {
              onOpenChange(false)
              void navigate('/settings/environment')
            }}
          >
            {t('lv.openEnv')}
          </Button>
        </>
      }
    />
  )
}
