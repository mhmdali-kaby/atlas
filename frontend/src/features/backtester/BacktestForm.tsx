import { CircleAlert, CircleMinus, Play, Plus, ShieldCheck } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { ApiError } from '../../lib/api'
import { formatFixed } from '../../lib/format'
import { Button, SegmentedControl } from '../../ui'
import { cx } from '../../ui/cx'
import { useLang } from '../portfolio/hooks'
import { useStartRun } from './api'
import { InstrumentPicker } from './InstrumentPicker'
import {
  barCount,
  BARS,
  CLOSE,
  KINDS,
  LIMITS,
  mapServerErrors,
  nextCondition,
  nextIndicator,
  OPS,
  renameIndicator,
  toRequest,
  validate,
  type Bar,
  type ConditionDraft,
  type FieldKey,
  type FormState,
  type Kind,
  type Op,
  type Side,
} from './model'
import { FieldError, NS, Stepper } from './parts'
import { Sentence } from './Sentence'
import { fill, useIndicatorLabel } from './text'

/*
 * The Backtester form (ATL-209; design v18 backtester.js formHtml): the rule as a sentence on top,
 * two columns of grouped lists, and a sticky Run bar. Client checks mirror ATL-208's validation and
 * show live; a 422 puts each server reason beside the field its path names (model.serverFieldKey),
 * with the design's banner above, and moves focus to the first marked control once per refusal.
 */

const NUM = '__num'

type Banner = { kind: 'invalid' | 'throttled' | 'server'; other: string[] }

/** Server errors a change to `key` makes stale: the key itself, or every row of a list that was re-indexed. */
function dropErrors(errors: Record<FieldKey, string>, keys: FieldKey[]): Record<FieldKey, string> {
  const next = { ...errors }
  for (const key of Object.keys(next)) {
    if (keys.some((k) => key === k || key.startsWith(`${k}.`))) delete next[key]
  }
  return next
}

export function BacktestForm({ initial }: { initial: FormState }) {
  const { t } = useTranslation(NS)
  const lang = useLang()
  const env = useTradingEnv()
  const navigate = useNavigate()
  const start = useStartRun(env)
  const [form, setForm] = useState<FormState>(initial)
  const [server, setServer] = useState<Record<FieldKey, string>>({})
  const [banner, setBanner] = useState<Banner | null>(null)
  const focusInvalid = useRef(false)
  const pageRef = useRef<HTMLDivElement>(null)
  const label = useIndicatorLabel(form)

  const client = useMemo(() => validate(form), [form])
  const blocked = Object.keys(client).length > 0
  const errorOf = (key: FieldKey): string | undefined => {
    const c = client[key]
    return server[key] ?? (c ? t(`err.${c.key}`, c.values) : undefined)
  }

  const update = (keys: FieldKey[], next: (f: FormState) => FormState) => {
    setForm(next)
    setServer((s) => dropErrors(s, keys))
  }

  // Once per refusal: the first control the server marked is scrolled clear of the Run bar and focused.
  useEffect(() => {
    if (!focusInvalid.current) return
    focusInvalid.current = false
    const bad = pageRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')
    if (!bad) return
    const reduce = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ;(bad.closest('.bt-cond, .bt-ind, .bt-row') ?? bad).scrollIntoView?.({ block: 'center', behavior: reduce ? 'auto' : 'smooth' })
    bad.focus({ preventScroll: true })
  }, [server])

  const submit = async () => {
    if (blocked || start.isPending) return
    setBanner(null)
    try {
      const res = await start.mutateAsync(toRequest(form))
      navigate(`/backtester/run?id=${encodeURIComponent(res.id)}`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const mapped = mapServerErrors(e.fieldErrors, form)
        focusInvalid.current = true
        setServer(mapped.fields)
        setBanner({ kind: 'invalid', other: mapped.other.length || Object.keys(mapped.fields).length ? mapped.other : [e.message] })
      } else if (e instanceof ApiError && e.status === 429) {
        setBanner({ kind: 'throttled', other: [] })
      } else {
        setBanner({ kind: 'server', other: [] })
      }
    }
  }

  const n = barCount(form)
  const periodError = errorOf('period')
  const nameError = errorOf('name')
  const stopError = errorOf('stop')
  const tpError = errorOf('tp')

  const condRows = (list: 'entry' | 'exit') => {
    const names = [CLOSE, ...form.indicators.map((i) => i.name)]
    const listError = errorOf(list)
    const setCond = (i: number, patch: Partial<ConditionDraft>) =>
      update([`${list}.${i}`], (f) => ({ ...f, [list]: f[list].map((c, j) => (j === i ? { ...c, ...patch } : c)) }))
    return (
      <div className={cx('bt-group bt-list', listError && 'is-error')} data-field={list}>
        {form[list].map((c, i) => {
          const key = `${list}.${i}`
          const err = errorOf(key)
          const errId = `bt-err-${list}-${i}`
          const bad = err ? { 'aria-invalid': true as const, 'aria-describedby': errId } : {}
          const what = t(list === 'entry' ? 'f.entryCond' : 'f.exitCond', { n: i + 1 })
          // a condition that names an indicator no longer defined keeps it as an option, so the select shows it
          const leftNames = names.includes(c.left) ? names : [...names, c.left]
          const rightNames = c.num || names.includes(c.right) ? names : [...names, c.right]
          return (
            <div key={i} className={cx('bt-cond', err && 'is-error')} data-testid={`bt-${list}-${i}`}>
              <select className="bt-input bt-sel" aria-label={`${t('f.left')}, ${what}`} value={c.left} onChange={(e) => setCond(i, { left: e.target.value })} {...bad}>
                {leftNames.map((nm) => (
                  <option key={nm} value={nm}>
                    {label(nm)}
                  </option>
                ))}
              </select>
              <select className="bt-input bt-sel" aria-label={`${t('f.op')}, ${what}`} value={c.op} onChange={(e) => setCond(i, { op: e.target.value as Op })} {...bad}>
                {OPS.map((op) => (
                  <option key={op} value={op}>
                    {t(`op.${op}`)}
                  </option>
                ))}
              </select>
              <select
                className="bt-input bt-sel"
                aria-label={`${t('f.right')}, ${what}`}
                value={c.num ? NUM : c.right}
                onChange={(e) => setCond(i, e.target.value === NUM ? { num: true, right: '50' } : { num: false, right: e.target.value })}
                {...bad}
              >
                {rightNames.map((nm) => (
                  <option key={nm} value={nm}>
                    {label(nm)}
                  </option>
                ))}
                <option value={NUM}>{t('f.number')}</option>
              </select>
              {c.num && (
                <input
                  className="bt-input bt-num"
                  inputMode="decimal"
                  dir="ltr"
                  autoComplete="off"
                  aria-label={`${t('f.right')}, ${what}`}
                  value={c.right}
                  onChange={(e) => setCond(i, { right: e.target.value })}
                  {...bad}
                />
              )}
              <button
                type="button"
                className="bt-icon-btn bt-rm"
                aria-label={t('f.remove', { what })}
                disabled={list === 'entry' && form.entry.length === 1}
                onClick={() => update([list], (f) => ({ ...f, [list]: f[list].filter((_, j) => j !== i) }))}
              >
                <CircleMinus aria-hidden="true" strokeWidth={1.5} />
              </button>
              {err && (
                <FieldError id={errId} className="bt-cond-err">
                  <bdi>{err}</bdi>
                </FieldError>
              )}
            </div>
          )
        })}
        <Button
          variant="plain"
          size="sm"
          className="bt-add"
          disabled={form[list].length >= (list === 'entry' ? LIMITS.maxEntry : LIMITS.maxExit)}
          onClick={() => update([list], (f) => ({ ...f, [list]: [...f[list], nextCondition(f)] }))}
          icon={<Plus aria-hidden="true" />}
        >
          {t('f.condAdd')}
        </Button>
        {listError && <FieldError id={`bt-err-${list}`}>{listError}</FieldError>}
        {list === 'exit' && (
          <div className="bt-stops">
            <div className="bt-stop">
              <label className="bt-row-label" htmlFor="bt-stop">
                {t('f.stop')}
              </label>
              <input
                id="bt-stop"
                className="bt-input bt-num"
                inputMode="decimal"
                dir="ltr"
                autoComplete="off"
                placeholder={t('f.optional')}
                value={form.stop}
                aria-invalid={stopError ? true : undefined}
                aria-describedby={stopError ? 'bt-err-stop' : 'bt-stop-sub'}
                onChange={(e) => update(['stop'], (f) => ({ ...f, stop: e.target.value }))}
              />
              <span className="bt-row-sub" id="bt-stop-sub">{t('f.pctOfFill')}</span>
              {stopError && <FieldError id="bt-err-stop">{stopError}</FieldError>}
            </div>
            <div className="bt-stop">
              <label className="bt-row-label" htmlFor="bt-tp">
                {t('f.tp')}
              </label>
              <input
                id="bt-tp"
                className="bt-input bt-num"
                inputMode="decimal"
                dir="ltr"
                autoComplete="off"
                placeholder={t('f.optional')}
                value={form.tp}
                aria-invalid={tpError ? true : undefined}
                aria-describedby={tpError ? 'bt-err-tp' : 'bt-tp-sub'}
                onChange={(e) => update(['tp'], (f) => ({ ...f, tp: e.target.value }))}
              />
              <span className="bt-row-sub" id="bt-tp-sub">{t('f.pctOfFill')}</span>
              {tpError && <FieldError id="bt-err-tp">{tpError}</FieldError>}
            </div>
          </div>
        )}
      </div>
    )
  }

  const indError = errorOf('indicators')
  const indicators = (
    <div className={cx('bt-group bt-list', indError && 'is-error')} data-field="indicators">
      {form.indicators.map((x, i) => {
        const err = errorOf(`ind.${i}`)
        const errId = `bt-err-ind-${i}`
        const bad = err ? { 'aria-invalid': true as const, 'aria-describedby': errId } : {}
        const w = Number(x.window)
        const setWindow = (next: string) => update([`ind.${i}`], (f) => ({ ...f, indicators: f.indicators.map((y, j) => (j === i ? { ...y, window: next } : y)) }))
        return (
          <div key={i} className={cx('bt-ind', err && 'is-error')} data-testid={`bt-ind-${i}`}>
            <input
              className="bt-input bt-ind-name"
              dir="ltr"
              spellCheck={false}
              autoComplete="off"
              aria-label={`${t('f.indName')} ${i + 1}`}
              value={x.name}
              onChange={(e) => {
                const next = e.target.value.trim()
                setForm((f) => renameIndicator(f, i, next))
                setServer((s) => dropErrors(s, [`ind.${i}`, 'entry', 'exit']))
              }}
              {...bad}
            />
            <select
              className="bt-input bt-sel"
              aria-label={`${t('f.kind')}, ${x.name}`}
              value={x.kind}
              onChange={(e) => update([`ind.${i}`], (f) => ({ ...f, indicators: f.indicators.map((y, j) => (j === i ? { ...y, kind: e.target.value as Kind } : y)) }))}
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k.toUpperCase()}
                </option>
              ))}
            </select>
            <Stepper
              label={`${t('f.window')}, ${x.name}`}
              value={null}
              canDec={Number.isFinite(w) && w > LIMITS.minWindow}
              canInc={Number.isFinite(w) && w < LIMITS.maxWindow}
              onStep={(d) => setWindow(String(Math.min(LIMITS.maxWindow, Math.max(LIMITS.minWindow, (Number.isFinite(w) ? Math.round(w) : 0) + d))))}
            >
              <input
                className="bt-step-input"
                inputMode="numeric"
                dir="ltr"
                autoComplete="off"
                aria-label={`${t('f.window')}, ${x.name}`}
                value={x.window}
                onChange={(e) => setWindow(e.target.value.trim())}
                {...bad}
              />
            </Stepper>
            <button
              type="button"
              className="bt-icon-btn bt-rm"
              aria-label={t('f.remove', { what: x.name || t('f.indName') })}
              onClick={() => update(['indicators', 'ind'], (f) => ({ ...f, indicators: f.indicators.filter((_, j) => j !== i) }))}
            >
              <CircleMinus aria-hidden="true" strokeWidth={1.5} />
            </button>
            {err && (
              <FieldError id={errId} className="bt-cond-err">
                <bdi>{err}</bdi>
              </FieldError>
            )}
          </div>
        )
      })}
      <Button
        variant="plain"
        size="sm"
        className="bt-add"
        disabled={form.indicators.length >= LIMITS.maxIndicators}
        onClick={() => update(['indicators'], (f) => ({ ...f, indicators: [...f.indicators, nextIndicator(f)] }))}
        icon={<Plus aria-hidden="true" />}
      >
        {t('f.indAdd')}
      </Button>
      {indError && <FieldError id="bt-err-indicators">{indError}</FieldError>}
    </div>
  )

  const simpleError = (key: FieldKey) => {
    const err = errorOf(key)
    return err ? <FieldError id={`bt-err-${key}`} className="bt-pad">{err}</FieldError> : null
  }

  return (
    <div className="bt-page" ref={pageRef} data-testid="bt-form">
      <header className="bt-head">
        <div className="bt-head-text">
          <h2 className="text-title-1">{t('new')}</h2>
          <p className="text-subheadline text-label-secondary">{t('intro')}</p>
        </div>
      </header>
      <Sentence form={form} />
      {banner && <FormBanner banner={banner} />}

      <form
        className="bt-form"
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
        id="bt-form"
      >
        <div className="bt-col">
          <h3 className="bt-group-title">{t('f.rule')}</h3>
          <div className={cx('bt-group', (nameError || errorOf('side')) && 'is-error')}>
            <div className="bt-row">
              <label className="bt-row-label" htmlFor="bt-name">
                {t('f.name')}
              </label>
              <input
                id="bt-name"
                className="bt-input bt-name"
                dir="ltr"
                spellCheck={false}
                autoComplete="off"
                maxLength={LIMITS.maxName + 20}
                value={form.name}
                aria-invalid={nameError ? true : undefined}
                aria-describedby={nameError ? 'bt-err-name' : undefined}
                onChange={(e) => update(['name'], (f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            {simpleError('name')}
            <div className="bt-row">
              <span className="bt-row-label">{t('f.side')}</span>
              <span className="bt-end">
                <SegmentedControl<Side>
                  aria-label={t('f.side')}
                  value={form.side}
                  onValueChange={(v) => update(['side'], (f) => ({ ...f, side: v }))}
                  options={[
                    { value: 'long', label: t('sideLong') },
                    { value: 'short', label: t('sideShort') },
                  ]}
                />
              </span>
            </div>
            {simpleError('side')}
          </div>

          <h3 className="bt-group-title">
            {t('f.insts')}{' '}
            <span className="bt-count">{fill(t('f.instsSub'), { n: <bdi dir="ltr" className="tabular-nums">{formatFixed(form.insts.length, 0, lang)}</bdi> })}</span>
          </h3>
          <InstrumentPicker env={env} value={form.insts} error={errorOf('insts')} onChange={(insts) => update(['insts'], (f) => ({ ...f, insts }))} />

          <h3 className="bt-group-title">{t('f.data')}</h3>
          <div className={cx('bt-group', (periodError || errorOf('bar')) && 'is-error')}>
            <div className="bt-row">
              <span className="bt-row-label">{t('f.bar')}</span>
              <span className="bt-end">
                <SegmentedControl<Bar>
                  aria-label={t('f.bar')}
                  value={form.bar}
                  onValueChange={(v) => update(['bar', 'period'], (f) => ({ ...f, bar: v }))}
                  options={BARS.map((b) => ({ value: b, label: <bdi dir="ltr">{b}</bdi> }))}
                />
              </span>
            </div>
            {simpleError('bar')}
            <div className={cx('bt-row bt-period', periodError && 'is-error')} data-field="period">
              <label className="bt-row-label" htmlFor="bt-from">
                {t('f.from')}
              </label>
              <input
                id="bt-from"
                className="bt-input bt-date"
                type="date"
                dir="ltr"
                value={form.from}
                aria-invalid={periodError ? true : undefined}
                aria-describedby="bt-period-note"
                onChange={(e) => update(['period'], (f) => ({ ...f, from: e.target.value }))}
              />
              <label className="bt-row-label" htmlFor="bt-to">
                {t('f.to')}
              </label>
              <input
                id="bt-to"
                className="bt-input bt-date"
                type="date"
                dir="ltr"
                value={form.to}
                aria-describedby="bt-period-note"
                onChange={(e) => update(['period'], (f) => ({ ...f, to: e.target.value }))}
              />
            </div>
            {periodError ? (
              <FieldError id="bt-period-note" className="bt-pad">
                <bdi>{periodError}</bdi>
              </FieldError>
            ) : (
              <p className={cx('bt-pad', n > LIMITS.maxBars ? 'bt-field-warn' : 'bt-field-hint')} id="bt-period-note" data-testid="bt-bars">
                {fill(t(n > LIMITS.maxBars ? 'f.barsOver' : 'f.bars'), {
                  n: <bdi dir="ltr" className="tabular-nums">{formatFixed(n, 0, lang)}</bdi>,
                  max: <bdi dir="ltr" className="tabular-nums">{formatFixed(LIMITS.maxBars, 0, lang)}</bdi>,
                })}
              </p>
            )}
          </div>

          <h3 className="bt-group-title">{t('f.test')}</h3>
          <div className={cx('bt-group', (errorOf('splits') || errorOf('slip')) && 'is-error')}>
            <div className="bt-row">
              <div className="bt-row-main">
                <span className="bt-row-label" id="bt-splits-l">
                  {t('f.splits')}
                </span>
                <span className="bt-row-sub">{t('f.splitsSub')}</span>
              </div>
              <Stepper
                label={t('f.splits')}
                value={<bdi dir="ltr" className="tabular-nums" data-testid="bt-splits">{formatFixed(form.splits, 0, lang)}</bdi>}
                canDec={form.splits > LIMITS.minSplits}
                canInc={form.splits < LIMITS.maxSplits}
                onStep={(d) => update(['splits'], (f) => ({ ...f, splits: Math.min(LIMITS.maxSplits, Math.max(LIMITS.minSplits, f.splits + d)) }))}
              />
            </div>
            {simpleError('splits')}
            <div className="bt-row">
              <div className="bt-row-main">
                <span className="bt-row-label">{t('f.slip')}</span>
                <span className="bt-row-sub">{t('f.slipSub')}</span>
              </div>
              <Stepper
                label={t('f.slip')}
                value={fill(t('bps'), { n: <bdi dir="ltr" className="tabular-nums" data-testid="bt-slip">{formatFixed(form.slip, 0, lang)}</bdi> })}
                canDec={form.slip > LIMITS.minSlip}
                canInc={form.slip < LIMITS.maxSlip}
                onStep={(d) => update(['slip'], (f) => ({ ...f, slip: Math.min(LIMITS.maxSlip, Math.max(LIMITS.minSlip, f.slip + d)) }))}
              />
            </div>
            {simpleError('slip')}
          </div>
        </div>

        <div className="bt-col">
          <h3 className="bt-group-title">{t('f.ind')}</h3>
          {indicators}
          <h3 className="bt-group-title">{t('f.entry')}</h3>
          {condRows('entry')}
          <h3 className="bt-group-title">{t('f.exit')}</h3>
          {condRows('exit')}
        </div>
      </form>

      <div className="bt-runbar">
        <p className="bt-run-note">
          <ShieldCheck aria-hidden="true" strokeWidth={1.5} />
          <span>{t('runNote')}</span>
        </p>
        <Button
          type="submit"
          form="bt-form"
          variant="primary"
          size="lg"
          disabled={blocked}
          loading={start.isPending}
          icon={<Play aria-hidden="true" className="rtl:-scale-x-100" />}
          data-testid="bt-run"
        >
          {start.isPending ? t('starting') : t('run')}
        </Button>
      </div>
    </div>
  )
}

function FormBanner({ banner }: { banner: Banner }) {
  const { t } = useTranslation(NS)
  const title = banner.kind === 'invalid' ? t('e.invalid') : banner.kind === 'throttled' ? t('e.throttled') : t('e.server')
  const body = banner.kind === 'invalid' ? t('e.fix.invalid') : banner.kind === 'throttled' ? t('e.throttledFix') : t('e.serverFix')
  const extra: ReactNode = banner.other.map((reason, i) => (
    <p key={i} className="bt-notice-body">
      <bdi>{reason}</bdi>
    </p>
  ))
  return (
    <div className="bt-notice is-error bt-422" role="alert" data-testid="bt-banner">
      <CircleAlert aria-hidden="true" strokeWidth={1.5} />
      <div>
        <p className="bt-notice-title">{title}</p>
        <p className="bt-notice-body">{body}</p>
        {extra}
      </div>
    </div>
  )
}
