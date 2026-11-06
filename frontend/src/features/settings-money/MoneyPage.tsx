import { useQueryClient } from '@tanstack/react-query'
import { Banknote, CircleAlert, Info, RotateCcw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { ApiError } from '../../lib/api'
import { formatFixed, formatMoney } from '../../lib/format'
import { Button, Skeleton, toast } from '../../ui'
import { Notice, useSettings } from '../onboarding'
import { useConnections } from '../portfolio/api'
import { useLang, useNow } from '../portfolio/hooks'
import '../settings-connections/settings.css'
import '../settings-risk/risk.css'
import { dayText } from '../settings-environment/model'
import { fieldErrors, historyKey, useSaveSettings, useSettingsHistory } from '../settings-risk/model'
import { EnvScopeControl, EnvScopeNotice, useEnvScope, type EnvScope } from '../settings-risk/EnvScope'
import { MoneyActivity } from './Activity'
import { FieldRow } from './FieldRow'
import {
  changedFields,
  changesFor,
  clean,
  clientErrors,
  cutRows,
  FIELD_OF_LIMIT,
  fieldError,
  groupText,
  MONEY_FIELDS,
  previewParams,
  stepped,
  toCents,
  useDebounced,
  useRiskPreview,
  type MoneyField,
  type MoneyTexts,
  textsFrom,
} from './model'
import './money.css'
import { Fig, fill, pctText, usd } from './parts'
import { Preview } from './Preview'

/*
 * Settings → Money limits (brief §14.4, ATL-130; design js/pages/settings-money.js).
 * The preview calls GET /api/settings/risk-preview (ATL-129) with the unsaved draft 300 ms
 * after the last valid edit; Save sends the changed keys as one versioned PATCH /api/settings.
 */

const DEBOUNCE_MS = 300

function PaneHeader({ version, scope }: { version: number | null; scope: EnvScope }) {
  const { t } = useTranslation('settings-money')
  const lang = useLang()
  return (
    <header className="st-pane st-risk-pane">
      <span className="st-pane-icon" aria-hidden="true">
        <Banknote strokeWidth={1.75} />
      </span>
      <div className="grid gap-0.5">
        <h2 className="text-title-2">{t('nav')}</h2>
        <p className="max-w-[60ch] text-subheadline text-label-secondary">{t(`intro.${scope.env}`)}</p>
      </div>
      <div className="lv-aside">
        {version !== null && (
          <span className="st-version" data-testid="settings-version">
            {t('version', { v: formatFixed(version, 0, lang) })}
          </span>
        )}
        <EnvScopeControl scope={scope} />
      </div>
    </header>
  )
}

function LoadError({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('settings-money')
  const parts = t('error.body').split('./atlas up')
  return (
    <section role="alert" className="st-group st-empty" data-testid="ml-error">
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
    <div className="grid gap-7" aria-busy="true" data-testid="ml-loading">
      <div className="st-group ml-preview">
        <Skeleton className="h-3 w-[58%]" />
        <Skeleton className="h-5 w-[92%]" />
        <Skeleton className="h-5 w-[64%]" />
      </div>
      <div className="st-group">
        {MONEY_FIELDS.map((f) => (
          <div key={f} className="ml-row">
            <Skeleton className="h-3.5 w-[40%]" />
            <Skeleton className="h-[30px] w-[226px] rounded-[8px]" />
          </div>
        ))}
      </div>
    </div>
  )
}

export function MoneyPage() {
  const scope = useEnvScope()
  // One row per env: switching Demo | Live starts from that row's saved values, no draft carried over.
  return <MoneyPane key={scope.env} scope={scope} />
}

function MoneyPane({ scope }: { scope: EnvScope }) {
  const ENV = scope.env
  const { t } = useTranslation('settings-money')
  const { t: tOb } = useTranslation('onboarding')
  const lang = useLang()
  const now = useNow(30_000)
  const client = useQueryClient()
  const query = useSettings(ENV)
  const history = useSettingsHistory(ENV)
  const save = useSaveSettings(ENV)
  const connections = useConnections()
  const doc = query.data?.data

  /** `null` = no edits: the form shows the saved settings. */
  const [edits, setEdits] = useState<MoneyTexts | null>(null)
  const [serverErrors, setServerErrors] = useState<Partial<Record<string, string>>>({})
  const [conflictVersion, setConflictVersion] = useState<number | null>(null)
  const pageRef = useRef<HTMLDivElement>(null)
  const focusError = useRef(false)

  const texts = edits ?? (doc ? textsFrom(doc) : null)
  const errors = texts ? clientErrors(texts) : {}
  // First live month (ATL-238): the live cap is required until the end date, so an empty cap can't be saved.
  const capRequiredUntil = doc?.first_live_month_ends_at ? dayText(doc.first_live_month_ends_at, lang, true) : null
  const missingCap = capRequiredUntil !== null && texts !== null && clean(texts.bot_capital_cap_usdt) === ''
  const invalid = Object.keys(errors).length > 0 || missingCap
  const liveQs = doc && texts && !invalid ? previewParams(texts, doc, ENV) : null
  // While a field is invalid the preview keeps its last good answer (design §5.5).
  const debouncedQs = useDebounced(liveQs, DEBOUNCE_MS)
  const preview = useRiskPreview(debouncedQs, ENV)
  const baseQs = new URLSearchParams({ env: ENV }).toString()
  const dirty = doc && texts ? changedFields(texts, doc).length > 0 : false
  const savedPreview = useRiskPreview(dirty && doc ? baseQs : null, ENV)
  const p = preview.data?.data
  const pending = (liveQs !== null && liveQs !== debouncedQs) || preview.isFetching

  useEffect(() => {
    if (!focusError.current) return
    focusError.current = false
    pageRef.current?.querySelector<HTMLInputElement>('input[aria-invalid="true"]')?.focus()
  })

  // One polite announcement per preview answer, not per keystroke (design §7).
  const announce =
    p && !p.reason && p.max_risk_usd && p.max_notional_usd && p.notional_binding_limit
      ? t('say', { risk: formatMoney(p.max_risk_usd, 'USD', lang), use: formatMoney(p.max_notional_usd, 'USD', lang), binding: t(`c.${p.notional_binding_limit}`) })
      : ''

  const setField = (field: MoneyField, text: string) => {
    if (!texts) return
    setEdits({ ...texts, [field]: text })
    if (serverErrors[field]) setServerErrors(({ [field]: _gone, ...rest }) => rest)
  }

  const onSave = () => {
    if (!doc || !texts || save.isPending || invalid) return
    save.mutate(
      { version: doc.version, changes: changesFor(texts, doc) },
      {
        onSuccess: (saved) => {
          setEdits(null)
          setServerErrors({})
          setConflictVersion(null)
          void client.invalidateQueries({ queryKey: ['settings', ENV, 'risk-preview'] })
          toast({ tone: 'success', title: t('saved', { v: formatFixed(saved.version, 0, lang) }), body: t('savedBody') })
        },
        onError: async (err) => {
          if (err instanceof ApiError && err.status === 409) {
            // Keep the owner's edits on top of the version another window saved.
            const fresh = await query.refetch()
            void client.invalidateQueries({ queryKey: historyKey(ENV) })
            setConflictVersion(fresh.data?.data.version ?? (err.body as { current_version?: number } | null)?.current_version ?? null)
            return
          }
          const byField = fieldErrors(err)
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

  let body
  if (query.isPending) {
    body = <Loading />
  } else if (!doc || !texts) {
    body = <LoadError onRetry={() => void query.refetch()} />
  } else {
    const changed = changedFields(texts, doc)
    const blocking = MONEY_FIELDS.filter((f) => errors[f] || serverErrors[f]).length
    const showFigs = p && !p.reason && !invalid
    const rows = showFigs ? cutRows(p, texts, doc) : null
    const binding = new Set<MoneyField>()
    let profileBinds = false
    if (showFigs && rows) {
      for (const r of rows) {
        if (!r.binding) continue
        const f = FIELD_OF_LIMIT[r.key]
        if (f) binding.add(f)
        else if ((toCents(p.max_notional_usd) ?? 0n) > 0n) profileBinds = true
      }
      const max = fieldError('max_trades_per_day', texts.max_trades_per_day) ? Infinity : Number(clean(texts.max_trades_per_day))
      if (p.trades_today >= max) binding.add('max_trades_per_day')
    }
    const usedToday = p ? toCents(p.used_exposure_today_usd) : null
    const position = rows?.find((r) => r.key === 'max_position_pct')

    body = (
      <>
        {conflictVersion !== null && (
          <Notice tone="warning" icon={RotateCcw} role="status" title={t('conflict.title')} data-testid="conflict-notice">
            {t('conflict.body', { v: formatFixed(conflictVersion, 0, lang) })}
          </Notice>
        )}

        <Preview
          doc={doc}
          texts={texts}
          preview={p}
          failed={preview.isError && !p}
          pending={pending && !invalid}
          invalid={invalid}
          dirty={dirty}
          saved={savedPreview.data?.data}
          seed={connections.data?.data.find((c) => c.env === ENV)?.uid_last4 ? `okx-demo-${connections.data.data.find((c) => c.env === ENV)?.uid_last4}` : 'okx-demo'}
          now={now}
          lang={lang}
          env={ENV}
          onRetry={() => void preview.refetch()}
        />

        <section className="grid" aria-labelledby="ml-limits-title">
          <h2 id="ml-limits-title" className="ml-group-title">
            {t('limits')}
          </h2>
          <div className="st-group" data-testid="ml-form">
            {MONEY_FIELDS.map((f) => (
              <FieldRow
                key={f}
                field={f}
                texts={texts}
                changed={changed.includes(f)}
                binds={binding.has(f)}
                serverError={serverErrors[f]}
                usedToday={usedToday}
                tradesToday={p ? p.trades_today : null}
                lang={lang}
                onChange={(text) => setField(f, text)}
                onStep={(dir) => setField(f, stepped(f, texts[f], dir))}
                onBlur={() => {
                  const tidy = groupText(f, texts[f])
                  if (tidy !== texts[f] && !serverErrors[f]) setEdits({ ...texts, [f]: tidy })
                }}
                capRequiredUntil={capRequiredUntil}
                onCap={(set) => {
                  setField('bot_capital_cap_usdt', set ? '100,000' : '')
                  pageRef.current?.querySelector<HTMLInputElement>('#ml-bot_capital_cap_usdt')?.focus()
                }}
              />
            ))}
          </div>
          <p className="ml-group-note">{t('note')}</p>
          {profileBinds && position && (
            <p className="ml-group-note ml-note-profile" data-testid="ml-note-profile">
              <Info aria-hidden="true" />
              <span>
                {fill(t('noteProfile'), {
                  profile: tOb(`rp.${doc.settings.risk_profile}`),
                  pct: <Fig>{pctText(doc.settings.max_position_pct, lang)}</Fig>,
                  value: <Fig>{usd(position.cents, lang)}</Fig>,
                })}{' '}
                <Link to={ENV === scope.active ? '/settings/risk' : `/settings/risk?env=${ENV}`}>{t('noteProfileLink')}</Link>
              </span>
            </p>
          )}
        </section>

        <section className="grid" aria-labelledby="ml-act-title">
          <h2 id="ml-act-title" className="ml-group-title">
            {t('activity')}
          </h2>
          <MoneyActivity entries={history.data?.data} loading={history.isPending} lang={lang} />
          <p className="ml-group-note">{t('activityNote')}</p>
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
                setServerErrors({})
              }}
            >
              {t('revert')}
            </Button>
            <Button variant="primary" size="sm" loading={save.isPending} disabled={invalid || blocking > 0 || changed.length === 0} onClick={onSave}>
              {save.isPending ? t('saving') : t('save')}
            </Button>
          </div>
        )}
        <div className="sr-only" aria-live="polite" data-testid="ml-live">
          {announce}
        </div>
      </>
    )
  }

  return (
    <div ref={pageRef} className="st-page st-risk ml-page">
      <PaneHeader scope={scope} version={doc && !query.isPending ? doc.version : null} />
      <EnvScopeNotice scope={scope} />
      {body}
    </div>
  )
}
