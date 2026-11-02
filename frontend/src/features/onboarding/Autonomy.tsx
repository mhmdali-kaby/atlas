import { CircleAlert, CloudOff, Eye, Hand, Minus, OctagonX, Plus, RotateCcw, Route, Shield, ShieldCheck, CirclePause, SlidersHorizontal } from 'lucide-react'
import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, formatMoney, NO_VALUE, type Lang } from '../../lib/format'
import { Button, Sheet } from '../../ui'
import { cx } from '../../ui/cx'
import { Notice } from './Notice'
import '../settings-risk/risk.css'
import './autonomy.css'

/*
 * Autonomy (ATL-216, decision 254; design js/autonomy.js): the ask line, the "Ask me above"
 * money field and the turn-on sheet. Shared by Settings → Autonomy and the onboarding step.
 * Solid accent = goes to OKX on its own, hatched = waits for you (the Money limits grammar).
 */

/** Stepper step for the ask-above amount (design: step 100, never below 0). */
export const ASK_STEP = 100

function useLang(): Lang {
  const { i18n } = useTranslation()
  return i18n.language === 'ar' ? 'ar' : 'en'
}

/** What the owner typed, as a number; NaN when empty or not a number. Negatives parse (the API judges them). */
export function parseAsk(text: string): number {
  const raw = text.replace(/[‎‏⁦-⁩]/g, '').replace(/[,\s]/g, '').replace('−', '-').replace('٫', '.').trim()
  if (raw === '') return NaN
  return /^-?\d*\.?\d+$/.test(raw) ? Number(raw) : NaN
}

/** The amount as the field shows it: grouped, decimals only when there are cents. */
export function askInputText(value: string | number | null | undefined): string {
  const n = Number(value)
  if (value === null || value === undefined || value === '' || !Number.isFinite(n)) return ''
  return new Intl.NumberFormat('en-US', { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 }).format(n)
}

/** The amount as the API takes it: a decimal string, "250", "-5", "1000.5". */
export function askApiValue(text: string): string {
  const n = parseAsk(text)
  return Number.isNaN(n) ? text.trim() : String(Math.round(n * 100) / 100)
}

/** Splits "{{a}} … {{b}}" on its placeholders and drops nodes into them. */
function fill(template: string, parts: Record<string, ReactNode>): ReactNode {
  return template.split(/(\{\{\w+\}\})/).map((piece, i) => {
    const name = /^\{\{(\w+)\}\}$/.exec(piece)?.[1]
    return <Fragment key={i}>{name ? parts[name] : piece}</Fragment>
  })
}

/** A money figure as an LTR island: "$1,000.00". */
export function MoneyFig({ value, strong }: { value: number | string | null; strong?: boolean }) {
  const lang = useLang()
  const text = value === null || value === '' || Number.isNaN(Number(value)) ? NO_VALUE : formatMoney(value, 'USD', lang)
  const fig = (
    <bdi dir="ltr" className="tabular-nums">
      {text}
    </bdi>
  )
  return strong ? <b>{fig}</b> : fig
}

/** How much of the line goes on its own (0..1), or null for "nothing is sent". */
export function askShare(level: number, ask: number, largest: number | null): number | null {
  if (level <= 1) return null
  if (level === 2) return 0
  if (level >= 4) return 1
  if (!(ask > 0)) return 0
  if (largest === null || !(largest > 0)) return 1
  return Math.max(0, Math.min(1, ask / largest))
}

/** The sentence for a level (design §4.3 table), figures in full ink. */
export function AskSentence({ level, ask, largest }: { level: number; ask: number; largest: number | null }) {
  const { t } = useTranslation('autonomy')
  const a = <MoneyFig value={ask} strong />
  const l = <MoneyFig value={largest} strong />
  if (level <= 1) return <>{t('now.1')}</>
  if (level === 2) return <>{t('now.2')}</>
  if (level >= 4) return <>{fill(t('now.4'), { largest: l })}</>
  if (!(ask > 0)) return <>{t('now.3zero')}</>
  if (largest !== null && ask >= largest) return <>{fill(t('now.3all'), { amount: a, largest: l })}</>
  return <>{fill(t('now.3'), { amount: a, largest: l })}</>
}

/**
 * The ask line (design §4.3): every trade size the limits allow today, $0 to the largest
 * trade, cut where Atlas starts asking. Hidden from assistive tech: the sentence says it.
 */
export function AskLine({ level, ask, largest, testId }: { level: number; ask: number; largest: number | null; testId?: string }) {
  const { t } = useTranslation('autonomy')
  const share = askShare(level, Number.isNaN(ask) ? 0 : ask, largest)
  const none = share === null
  const cut = share ?? 0
  const edge = none ? '' : cut < 0.14 ? 'is-at-start' : cut > 0.86 ? 'is-at-end' : ''
  const l3 = level === 3
  return (
    <figure
      aria-hidden="true"
      data-testid={testId}
      data-cut={none ? undefined : cut.toFixed(4)}
      className={cx('au-line', l3 && 'is-l3', none && 'is-none')}
      style={{ '--cut': cut.toFixed(4) } as React.CSSProperties}
    >
      <div className="au-line-cutrow">
        {l3 && (
          <span className="au-line-cutwrap">
            <span className={cx('au-line-cutlabel', edge)}>
              <Hand strokeWidth={1.75} />
              <span>{fill(t('line.cut'), { amount: <MoneyFig value={Number.isNaN(ask) ? null : ask} /> })}</span>
            </span>
          </span>
        )}
      </div>
      <div className="au-line-bar">
        <div className="au-line-track">
          <i className="au-line-auto" />
        </div>
        {l3 && (
          <span className="au-line-cutwrap">
            <i className="au-line-tick" />
          </span>
        )}
      </div>
      <div className="au-line-ends">
        <MoneyFig value={0} />
        <span className="au-line-max">
          <span className="au-line-max-fig">
            <MoneyFig value={largest} />
          </span>
          <span className="au-line-max-words">{t('line.max')}</span>
        </span>
      </div>
      <div className="au-line-keys">
        {none ? (
          <span className="au-key">
            <Eye strokeWidth={1.75} />
            <span>{t('line.none')}</span>
          </span>
        ) : (
          <>
            {cut > 0 && (
              <span className="au-key is-auto">
                <i />
                <span>{t('line.auto')}</span>
              </span>
            )}
            {cut < 1 && (
              <span className="au-key is-asks">
                <i />
                <span>{t('line.asks')}</span>
              </span>
            )}
          </>
        )}
      </div>
    </figure>
  )
}

export interface AskFieldProps {
  id: string
  text: string
  onText: (text: string) => void
  /** The API's 422 message (or a client one); replaces the hint. */
  error?: string | undefined
  disabled?: boolean
  /** Revert / Save beside the stepper (Settings, when the amount differs from the saved one). */
  actions?: ReactNode
  onEnter?: () => void
  inputRef?: React.Ref<HTMLInputElement>
}

/** The "Ask me above" money field (design AU.field): input with USDT inside, − and + (step 100). */
export function AskField({ id, text, onText, error, disabled, actions, onEnter, inputRef }: AskFieldProps) {
  const { t } = useTranslation('autonomy')
  const value = parseAsk(text)
  const step = (dir: 1 | -1) => {
    const base = value >= 0 ? value : 0
    onText(askInputText(Math.max(0, Math.round((base + dir * ASK_STEP) / ASK_STEP) * ASK_STEP)))
  }
  return (
    <div className={cx('au-field', error && 'is-error')} data-testid="ask-field">
      <label className="au-field-label" htmlFor={id}>
        {t('askAbove')}
      </label>
      <div className={cx('st-stepper au-stepper', error && 'is-error')}>
        <div className="st-stepper-field" dir="ltr">
          <input
            ref={inputRef}
            id={id}
            className="st-stepper-input au-input"
            inputMode="decimal"
            autoComplete="off"
            spellCheck={false}
            value={text}
            disabled={disabled}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${id}-err` : `${id}-hint`}
            onChange={(e) => onText(e.target.value)}
            onBlur={() => {
              const n = parseAsk(text)
              if (n >= 0) onText(askInputText(n))
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault()
                step(e.key === 'ArrowUp' ? 1 : -1)
              } else if (e.key === 'Enter' && onEnter) {
                e.preventDefault()
                onEnter()
              }
            }}
          />
          <span aria-hidden="true" className="st-stepper-unit">
            {t('unit')}
          </span>
        </div>
        <div className="st-stepper-buttons">
          <button type="button" aria-label={t('decrease')} disabled={disabled || !(value > 0)} onClick={() => step(-1)}>
            <Minus aria-hidden="true" strokeWidth={2} />
          </button>
          <span className="st-stepper-sep" aria-hidden="true" />
          <button type="button" aria-label={t('increase')} disabled={disabled} onClick={() => step(1)}>
            <Plus aria-hidden="true" strokeWidth={2} />
          </button>
        </div>
        {actions && <span className="au-amount-actions">{actions}</span>}
      </div>
      {error ? (
        <p id={`${id}-err`} className="st-field-error text-footnote" data-testid="ask-error">
          <CircleAlert aria-hidden="true" strokeWidth={1.75} />
          <span>{error}</span>
        </p>
      ) : (
        <p id={`${id}-hint`} className="au-hint text-footnote text-label-secondary">
          {t('askHint')}
        </p>
      )}
    </div>
  )
}

export type SheetNotice = { kind: 'server' } | { kind: 'conflict'; version: number } | null

export interface TurnOnSheetProps {
  open: boolean
  /** The level being turned on: 3 or 4. */
  level: number
  /** At level 3 already, raising the amount ("Send larger trades without asking?"). */
  raise?: boolean
  mode: 'settings' | 'onboarding'
  /** The amount the field opens with (level 3). */
  initialAsk: string
  largest: number | null
  pending?: boolean
  /** The API's 422 message for autonomy_ask_above_usd. */
  fieldError?: string | undefined
  notice?: SheetNotice
  onAskEdited?: () => void
  onCancel: () => void
  /** Confirm: the amount as typed (level 3) — the parent saves (settings) or keeps it (onboarding). */
  onConfirm: (askText: string) => void
}

/** The turn-on sheet (design §4.4): says in plain words what changes, then saves on confirm. Mount it when it opens: the field starts from `initialAsk`. */
export function TurnOnSheet({ open, level, raise, mode, initialAsk, largest, pending, fieldError, notice, onAskEdited, onCancel, onConfirm }: TurnOnSheetProps) {
  const { t } = useTranslation('autonomy')
  const lang = useLang()
  const [text, setText] = useState(initialAsk)
  const [clientError, setClientError] = useState<string | undefined>()
  const inputRef = useRef<HTMLInputElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)

  // A 422 puts focus back in the field; a server notice puts it on the primary button.
  useEffect(() => {
    if (fieldError) inputRef.current?.focus()
  }, [fieldError])
  useEffect(() => {
    if (notice) confirmRef.current?.focus()
  }, [notice])

  const l3 = level === 3
  const title = raise ? t('sh.titleRaise') : t(`sh.title${level}`)
  const text_ = raise ? t('sh.textRaise') : t(`sh.text${level}`)
  const confirm = mode === 'onboarding' ? t(`sh.use${level}`) : raise ? t('sh.confirmRaise') : t(`sh.confirm${level}`)
  const ask = parseAsk(text)
  const error = fieldError ?? clientError

  const submit = () => {
    if (pending) return
    // Only an empty or unreadable amount is stopped here; a negative goes to the API, which judges it.
    if (l3 && Number.isNaN(ask)) {
      setClientError(t('err.empty'))
      inputRef.current?.focus()
      return
    }
    onConfirm(text)
  }

  const keep: [typeof ShieldCheck, string][] = [
    [ShieldCheck, 'sh.k1'],
    [Shield, 'sh.k2'],
    [OctagonX, 'sh.k3'],
    [CirclePause, 'sh.k4'],
  ]

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next && !pending) onCancel()
      }}
      title={title}
      description={text_}
      icon={<Route strokeWidth={1.75} />}
      className="au-sheet"
      onOpenAutoFocus={(e) => {
        e.preventDefault()
        if (l3 && inputRef.current) {
          inputRef.current.focus()
          inputRef.current.select()
        } else confirmRef.current?.focus()
      }}
      actions={
        <>
          <Button variant="secondary" disabled={pending} onClick={onCancel} data-testid="au-sheet-cancel">
            {t('sh.cancel')}
          </Button>
          <Button ref={confirmRef} variant="primary" loading={pending} onClick={submit} data-testid="au-sheet-confirm">
            {pending ? t('sh.saving') : confirm}
          </Button>
        </>
      }
    >
      <form
        className="grid gap-4"
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        {notice?.kind === 'conflict' && (
          <Notice tone="warning" icon={RotateCcw} role="alert" title={t('err.conflict.title')}>
            {t('err.conflict.body', { v: formatFixed(notice.version, 0, lang) })}
          </Notice>
        )}
        {notice?.kind === 'server' && (
          <Notice tone="error" icon={CloudOff} role="alert" title={t('err.server.title')}>
            {t('err.server.body')}
          </Notice>
        )}
        <div className="au-sh-box">
          {l3 && (
            <AskField
              id="au-sh-ask"
              inputRef={inputRef}
              text={text}
              error={error}
              disabled={pending}
              onText={(next) => {
                setText(next)
                setClientError(undefined)
                onAskEdited?.()
              }}
              onEnter={submit}
            />
          )}
          <p className="au-sh-sentence" data-testid="au-sheet-sentence">
            <AskSentence level={level} ask={l3 ? ask : 0} largest={largest} />
          </p>
          <AskLine level={level} ask={l3 ? ask : 0} largest={largest} />
        </div>
        <div className="au-keep">
          <h3 className="au-keep-title">{t('sh.keep')}</h3>
          <ul role="list">
            {keep.map(([Icon, key]) => (
              <li key={key}>
                <Icon aria-hidden="true" strokeWidth={1.75} />
                <span>{t(key)}</span>
              </li>
            ))}
          </ul>
        </div>
        {mode === 'onboarding' && (
          <p className="au-sh-later">
            <SlidersHorizontal aria-hidden="true" strokeWidth={1.75} />
            <span>{t('sh.onboarding')}</span>
          </p>
        )}
      </form>
    </Sheet>
  )
}
