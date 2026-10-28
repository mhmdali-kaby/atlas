import { useMutation } from '@tanstack/react-query'
import { RefreshCw, Replace, Trash2, TriangleAlert } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../lib/api'
import { Button, Seal, toast } from '../../ui'
import {
  AccountRows,
  canWithdraw,
  errorSlug,
  Notice,
  TestFailure,
  WithdrawWarning,
  type Connection,
  type ConnectionTestResponse,
} from '../onboarding'
import { useLang, useNow } from '../portfolio/hooks'
import { formatClock, formatDayTime } from '../portfolio/time'
import { useStoreConnection, type Failure } from './shared'
import { KeysSheet, RemoveSheet } from './sheets'
import { StatusLine } from './StatusLine'

/**
 * A stored demo connection: the account card (seal, status, Test again / Replace keys / Remove…),
 * the failure, Spot and withdraw notices, and the Account rows with Last verified in Baghdad time.
 */
export function ConnectedView({ connection, onRemoved }: { connection: Connection; onRemoved: () => void }) {
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
        data-testid="last-verified"
      >
        {formatDayTime(connection.last_verified_at, lang, true)}
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
