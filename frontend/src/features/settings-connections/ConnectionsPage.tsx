import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CircleAlert, CircleCheck, KeyRound, Lock, Plug, RefreshCw, Replace, Trash2, TriangleAlert } from 'lucide-react'
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../lib/api'
import { DATE_LOCALE, type Lang } from '../../lib/format'
import { Button, Seal, Sheet, SheetClose, Spinner, TextField, toast, Toggle } from '../../ui'
import { cx } from '../../ui/cx'
import {
  AccountRows,
  canWithdraw,
  ConnectionForm,
  CONNECTIONS_KEY,
  KeyHelp,
  Notice,
  TestFailure,
  useConnection,
  WithdrawWarning,
  type Connection,
  type ConnectionTestResponse,
  type TestOutcome,
} from '../onboarding'
import { formatAgo, formatClock, OWNER_TZ } from '../portfolio/time'
import { useLang, useNow } from '../portfolio/hooks'
import './settings.css'

/*
 * Settings → Connections (brief §14.2, design js/pages/settings.js `connections`): the OKX demo
 * account card with Test again / Replace keys / Remove…, its account rows, and the disabled
 * OKX live row. Keys are only ever shown as •••• last4.
 */

type Failure = { error: string; okxCode: string | null }
/** Slugs that mean Atlas could not run the test at all, as opposed to OKX refusing the key. */
const SERVICE_ERRORS = new Set(['trading_core_unavailable', 'trading_core_error'])

type ConnectionsList = { data: Connection[] }

function errorSlug(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body as { error?: unknown } | null
    if (body && typeof body.error === 'string') return body.error
    if (err.network) return 'trading_core_unavailable'
    if (err.status >= 500) return 'trading_core_error'
  }
  return 'unknown'
}

/** "28 Oct 2026, 17:36" in Baghdad time, Western digits in both languages. */
function formatVerified(iso: string, lang: Lang): string {
  return new Intl.DateTimeFormat(DATE_LOCALE[lang], {
    timeZone: OWNER_TZ,
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(iso))
}

function useStoreConnection() {
  const client = useQueryClient()
  return {
    put: (connection: Connection) =>
      client.setQueryData<ConnectionsList>(CONNECTIONS_KEY, (old) => ({
        data: [...(old?.data ?? []).filter((c) => c.id !== connection.id), connection],
      })),
    drop: (id: string) =>
      client.setQueryData<ConnectionsList>(CONNECTIONS_KEY, (old) => ({ data: (old?.data ?? []).filter((c) => c.id !== id) })),
  }
}

function PaneHeader() {
  const { t } = useTranslation('settings')
  return (
    <header className="st-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <Plug strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav.connections')}</h2>
        <p className="max-w-[54ch] text-subheadline text-label-secondary">{t('conn.intro')}</p>
      </div>
    </header>
  )
}

function LiveGroup() {
  const { t } = useTranslation('settings')
  const labelId = useId()
  return (
    <section className="grid" aria-labelledby={`${labelId}-title`}>
      <h2 id={`${labelId}-title`} className="px-4 pb-2 text-footnote font-semibold text-label-secondary">
        {t('conn.liveTitle')}
      </h2>
      <div role="list" className="st-group">
        <div role="listitem" aria-disabled="true" data-testid="live-row" className="flex min-h-12 items-center gap-3 px-4 py-2.5">
          <span
            aria-hidden="true"
            className="grid size-7 shrink-0 place-items-center rounded-[7px] bg-fill-3 text-label-secondary [&_svg]:size-4"
          >
            <Lock strokeWidth={1.5} />
          </span>
          <span className="grid min-w-0 flex-1 gap-px">
            <span id={labelId} className="text-callout text-label-secondary">
              {t('conn.live')}
            </span>
            <span className="text-footnote text-label-secondary">{t('conn.liveNote')}</span>
          </span>
          <Toggle checked={false} disabled aria-labelledby={labelId} />
        </div>
      </div>
    </section>
  )
}

function KeysSheet({
  open,
  onOpenChange,
  connection,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** null: connect a new demo key; otherwise replace this connection's keys. */
  connection: Connection | null
}) {
  const { t } = useTranslation('settings')
  const [failure, setFailure] = useState<Failure | null>(null)
  const replace = connection !== null

  function onResult(outcome: TestOutcome) {
    if (outcome.status === 'failed') {
      setFailure({ error: outcome.error, okxCode: outcome.okxCode })
      return
    }
    onOpenChange(false)
    toast({
      tone: 'success',
      title: replace ? t('rk.toast') : t('ck.toast'),
      body: t('rk.toastBody', { last4: outcome.connection.api_key_last4 }),
    })
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      icon={<KeyRound strokeWidth={1.5} />}
      title={replace ? t('rk.title') : t('ck.title')}
      description={replace ? t('rk.text') : t('ck.text')}
    >
      {!replace && <KeyHelp />}
      {failure && <TestFailure error={failure.error} okxCode={failure.okxCode} />}
      <div className="st-sheet-fields">
        <ConnectionForm
          connection={connection}
          idPrefix={replace ? 'rk' : 'ck'}
          submitLabel={t('rk.save')}
          cancel={
            <SheetClose>
              <Button variant="secondary">{t('cancel')}</Button>
            </SheetClose>
          }
          onTestStart={() => setFailure(null)}
          onResult={onResult}
        />
      </div>
    </Sheet>
  )
}

function RemoveSheet({
  open,
  onOpenChange,
  connection,
  onRemoved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  connection: Connection
  onRemoved: () => void
}) {
  const { t } = useTranslation('settings')
  const store = useStoreConnection()
  const [typed, setTyped] = useState('')
  const formId = useId()
  const remove = useMutation({
    mutationFn: () => api.delete<null>(`/api/connections/${connection.id}`),
    onSuccess: () => {
      store.drop(connection.id)
      onOpenChange(false)
      toast({ tone: 'success', title: t('rm.toast'), body: t('rm.toastBody') })
      onRemoved()
    },
  })
  const matches = typed.trim() === connection.label

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (matches && !remove.isPending) remove.mutate()
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => !remove.isPending && onOpenChange(next)}
      tone="destructive"
      icon={<Trash2 strokeWidth={1.5} />}
      title={t('rm.title')}
      description={t('rm.text')}
      actions={
        <>
          <SheetClose>
            <Button variant="secondary" disabled={remove.isPending}>
              {t('cancel')}
            </Button>
          </SheetClose>
          <Button type="submit" form={formId} variant="destructive" disabled={!matches} loading={remove.isPending}>
            {remove.isPending ? t('rm.removing') : t('rm.do')}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit}>
        <TextField
          id="rm-confirm"
          label={t('rm.confirm', { label: connection.label })}
          placeholder={connection.label}
          dir="auto"
          autoComplete="off"
          spellCheck={false}
          value={typed}
          onChange={(e) => setTyped(e.currentTarget.value)}
          disabled={remove.isPending}
          error={remove.isError ? t('rm.failed') : undefined}
        />
      </form>
    </Sheet>
  )
}

function StatusLine({
  connection,
  testing,
  failure,
  now,
  lang,
}: {
  connection: Connection
  testing: boolean
  failure: Failure | null
  now: number
  lang: Lang
}) {
  const { t } = useTranslation('settings')
  const verified = connection.last_verified_at
  const ms = verified ? Math.max(0, now - Date.parse(verified)) : 0
  const ago = verified ? (ms < 60_000 ? t('conn.justNow') : formatAgo(ms, lang)) : null
  const base = 'inline-flex items-center gap-1.5 text-subheadline text-label-secondary [&_svg]:size-4 [&_svg]:shrink-0'

  if (testing) {
    return (
      <p className={base} data-testid="conn-status" data-state="testing">
        <Spinner />
        <span>{t('conn.testingLine')}</span>
      </p>
    )
  }
  if (failure) {
    const service = SERVICE_ERRORS.has(failure.error)
    const text = service
      ? ago
        ? t('conn.cantTest', { ago })
        : t('conn.cantTestNever')
      : ago
        ? t('conn.failedLineAgo', { ago })
        : t('conn.failedLine')
    return (
      <p className={cx(base, 'text-loss-ink')} data-testid="conn-status" data-state="failed">
        <CircleAlert aria-hidden="true" strokeWidth={2} />
        <span>{text}</span>
      </p>
    )
  }
  if (ago) {
    return (
      <p className={base} data-testid="conn-status" data-state="ok">
        <CircleCheck aria-hidden="true" strokeWidth={2} className="text-profit-ink" />
        <span>{t('conn.connected', { ago })}</span>
      </p>
    )
  }
  return (
    <p className={base} data-testid="conn-status" data-state="untested">
      <span>{t('conn.untested')}</span>
    </p>
  )
}

function ConnectedView({ connection, onRemoved }: { connection: Connection; onRemoved: () => void }) {
  const { t } = useTranslation('settings')
  const { t: tOn } = useTranslation('onboarding')
  const lang = useLang()
  const now = useNow(30_000)
  const store = useStoreConnection()
  const [localFailure, setLocalFailure] = useState<Failure | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [sheet, setSheet] = useState<'replace' | 'remove' | null>(null)
  // Each opening is a fresh sheet (empty fields, no old error).
  const [opened, setOpened] = useState(0)
  const openSheet = (which: 'replace' | 'remove') => {
    setOpened((n) => n + 1)
    setSheet(which)
  }
  const testButton = useRef<HTMLButtonElement>(null)

  const test = useMutation({
    mutationFn: () => api.post<ConnectionTestResponse>(`/api/connections/${connection.id}/test`),
    onMutate: () => setLocalFailure(null),
    onSuccess: (result) => {
      store.put(result.connection)
      if (result.ok) {
        setWarnings(result.warnings ?? [])
        toast({
          tone: 'success',
          title: t('conn.okToast'),
          body: t('conn.okToastBody', { time: formatClock(result.verified_at ?? Date.now(), lang, false) }),
        })
      } else {
        setLocalFailure({ error: result.error ?? 'unknown', okxCode: result.okx_code })
        toast({ tone: 'error', title: t('conn.failedToast') })
      }
    },
    onError: (err) => {
      setLocalFailure({ error: errorSlug(err), okxCode: null })
      toast({ tone: 'error', title: t('conn.failedToast') })
    },
  })

  // The failure this page saw, else the one stored by the last test (survives a reload).
  const failure: Failure | null =
    localFailure ?? (connection.last_test_error ? { error: connection.last_test_error, okxCode: null } : null)
  const testing = test.isPending
  const spot = connection.account_mode === 'spot'
  const last4 = connection.uid_last4 ?? '----'
  const verifiedRow: [string, ReactNode] = [
    tOn('acct.verified'),
    connection.last_verified_at ? (
      <time
        key="verified"
        dateTime={connection.last_verified_at}
        title={t('conn.verifiedTip')}
        className="fig"
        data-testid="last-verified"
      >
        {formatVerified(connection.last_verified_at, lang)}
      </time>
    ) : (
      <span key="verified" data-testid="last-verified">
        {t('conn.never')}
      </span>
    ),
  ]

  return (
    <>
      <section className="grid" aria-labelledby="st-acc-name">
        <div className="st-group" data-testid="account-card">
          <div className="flex items-center gap-4 px-5 py-[18px]">
            <span role="img" aria-label={t('conn.sealLabel', { last4 })} className="relative grid shrink-0 place-items-center">
              <Seal seed={`okx:${connection.env}:${last4}`} size={64} />
              <span aria-hidden="true" className="st-seal-dot" data-state={failure ? 'stale' : 'ok'} />
            </span>
            <div className="grid min-w-0 gap-1">
              <h2 id="st-acc-name" className="truncate text-title-3 font-semibold">
                {connection.label}
              </h2>
              <StatusLine connection={connection} testing={testing} failure={failure} now={now} lang={lang} />
            </div>
          </div>
          <div className="st-actions">
            <Button
              ref={testButton}
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
          {testing ? t('conn.testingLine') : ''}
        </p>
        {(failure || spot || canWithdraw(connection, warnings)) && (
          <div className="mt-3 grid gap-3">
            {failure && <TestFailure error={failure.error} okxCode={failure.okxCode} />}
            {spot && !failure && (
              <Notice tone="warning" icon={TriangleAlert} title={tOn('tr.spot.title')}>
                {tOn('tr.spot.body')}
              </Notice>
            )}
            {canWithdraw(connection, warnings) && <WithdrawWarning />}
          </div>
        )}
        <p className="px-4 pt-2 text-footnote text-label-secondary">{t('conn.footnote')}</p>
      </section>

      <section className="grid" aria-labelledby="st-acc-title">
        <h2 id="st-acc-title" className="px-4 pb-2 text-footnote font-semibold text-label-secondary">
          {t('conn.accountTitle')}
        </h2>
        <AccountRows
          connection={connection}
          className="st-group st-rows"
          leading={[
            [tOn('acct.label'), <bdi key="label">{connection.label}</bdi>],
            [
              tOn('acct.key'),
              <bdi key="key" dir="ltr" className="fig" data-testid="key-tail">
                •••• {connection.api_key_last4}
              </bdi>,
            ],
          ]}
          trailing={[verifiedRow]}
        />
        <p className="px-4 pt-2 text-footnote text-label-secondary">{t('conn.accountNote')}</p>
      </section>

      <KeysSheet
        key={`replace-${opened}`}
        open={sheet === 'replace'}
        onOpenChange={(open) => {
          setSheet(open ? 'replace' : null)
          if (!open) setLocalFailure(null)
        }}
        connection={connection}
      />
      <RemoveSheet
        key={`remove-${opened}`}
        open={sheet === 'remove'}
        onOpenChange={(open) => setSheet(open ? 'remove' : null)}
        connection={connection}
        onRemoved={onRemoved}
      />
    </>
  )
}

function NotConnected({ focusOnMount }: { focusOnMount: boolean }) {
  const { t } = useTranslation('settings')
  const [open, setOpen] = useState(false)
  const [opened, setOpened] = useState(0)
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (focusOnMount) button.current?.focus()
  }, [focusOnMount])

  return (
    <section className="st-group st-empty" aria-labelledby="st-none-title" data-testid="not-connected">
      <span className="st-empty-icon" aria-hidden="true">
        <Plug strokeWidth={1.5} />
      </span>
      <h2 id="st-none-title" className="text-title-3">
        {t('conn.none.title')}
      </h2>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">{t('conn.none.body')}</p>
      <Button ref={button} variant="primary" icon={<Plug />} className="mt-2" onClick={() => {
          setOpened((n) => n + 1)
          setOpen(true)
        }}
      >
        {t('conn.connect')}
      </Button>
      <KeysSheet key={opened} open={open} onOpenChange={setOpen} connection={null} />
    </section>
  )
}

function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('settings')
  const parts = t('conn.loadError.body').split('./atlas up')
  return (
    <section role="alert" className="st-group st-empty" data-testid="connections-error">
      <span className="st-empty-icon is-error" aria-hidden="true">
        <CircleAlert strokeWidth={1.5} />
      </span>
      <h2 className="text-title-3">{t('conn.loadError.title')}</h2>
      <p className="max-w-[46ch] text-subheadline text-label-secondary">
        {parts.length === 2 ? (
          <>
            {parts[0]}
            <code dir="ltr" className="rounded-[6px] bg-fill-3 px-1.5 text-label [unicode-bidi:isolate]">
              ./atlas up
            </code>
            {parts[1]}
          </>
        ) : (
          t('conn.loadError.body')
        )}
      </p>
      <Button variant="secondary" icon={<RefreshCw />} className="mt-2" onClick={onRetry}>
        {t('conn.loadError.retry')}
      </Button>
    </section>
  )
}

export function ConnectionsPage() {
  const query = useConnection('demo')
  const [removed, setRemoved] = useState(false)

  let body
  if (query.isPending) {
    body = (
      <div className="grid gap-7" aria-busy="true" data-testid="connections-loading">
        <span className="st-skeleton h-[112px] rounded-card" />
        <span className="st-skeleton h-[240px] rounded-card" />
      </div>
    )
  } else if (query.isError && !query.data) {
    body = <LoadError onRetry={() => void query.refetch()} />
  } else if (query.connection) {
    body = <ConnectedView key={query.connection.id} connection={query.connection} onRemoved={() => setRemoved(true)} />
  } else {
    body = <NotConnected focusOnMount={removed} />
  }

  return (
    <div className="st-page">
      <PaneHeader />
      {body}
      <LiveGroup />
    </div>
  )
}
