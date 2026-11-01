import { CircleAlert, CircleCheck, CircleX, MessageSquareText } from 'lucide-react'
import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../../lib/api'
import { formatFixed, formatMoney, type Lang } from '../../../lib/format'
import { Button, Sheet, SheetClose } from '../../../ui'
import { INPUT_CLASSES } from '../../../ui/TextField'
import { fillSlots, slot } from '../slots'
import { formatTime } from '../time'
import { cmp, dec, decStr, div, fromInt, mul } from './dec'
import { Ladder } from './Ladder'
import { directionOf, proposalApi, sizePctOf, sizeUsdOf, type ProposalDetail, type Stake } from './proposal'
import { rText } from './rText'

/*
 * The proposal card's sheets (design v11 wr-proposal): Reject (a required reason, with common
 * reasons that fill the field), Ask the team (a required question) and Edit (size, stop and
 * targets, previewed on the ladder). Stops only tighten: the Edit sheet refuses a wider stop
 * before anything is sent, as PATCH /api/proposals does on the server.
 */

function Fig({ children }: { children: ReactNode }) {
  return (
    <bdi dir="ltr" className="num fig">
      {children}
    </bdi>
  )
}

// ---- Reject / Ask the team ---------------------------------------------------------------------

export function ReasonSheet({
  kind,
  open,
  onOpenChange,
  onSubmit,
}: {
  kind: 'reject' | 'ask'
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (text: string) => Promise<void>
}) {
  const { t } = useTranslation('proposal')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const id = useId()
  const area = useRef<HTMLTextAreaElement>(null)
  const ns = kind === 'reject' ? 'rj' : 'ak'
  const empty = text.trim() === ''

  const submit = async (e?: FormEvent) => {
    e?.preventDefault()
    if (empty || busy) return
    setBusy(true)
    try {
      await onSubmit(text.trim())
      setText('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!busy) onOpenChange(o)
      }}
      title={t(`${ns}.title`)}
      description={t(`${ns}.text`)}
      icon={kind === 'reject' ? <CircleX /> : <MessageSquareText />}
      tone={kind === 'reject' ? 'destructive' : 'default'}
      onOpenAutoFocus={(e) => {
        e.preventDefault()
        area.current?.focus()
      }}
      actions={
        <>
          <SheetClose>
            <Button disabled={busy}>{t('cancel')}</Button>
          </SheetClose>
          <Button variant={kind === 'reject' ? 'destructive' : 'primary'} disabled={empty} loading={busy} onClick={() => void submit()} data-testid={`pr-${kind}-submit`}>
            {busy ? t(`${ns}.busy`) : t(`${ns}.submit`)}
          </Button>
        </>
      }
    >
      <form className="pr-sheet grid gap-3" onSubmit={(e) => void submit(e)} noValidate>
        <div className="grid gap-1.5">
          <label htmlFor={`${id}-text`} className="text-subheadline font-medium text-label">
            {t(kind === 'reject' ? 'rj.reason' : 'ak.question')}
          </label>
          <textarea
            ref={area}
            id={`${id}-text`}
            className={`${INPUT_CLASSES} pr-textarea`}
            rows={3}
            maxLength={1000}
            dir="auto"
            required
            aria-describedby={`${id}-hint`}
            value={text}
            disabled={busy}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void submit(e)
            }}
            data-testid={`pr-${kind}-text`}
          />
          <p id={`${id}-hint`} className="text-footnote text-label-secondary">
            {t(`${ns}.hint`)}
          </p>
        </div>
        {kind === 'reject' && (
          <div className="pr-quick" role="group" aria-label={t('rj.quick')}>
            <span className="pr-quick-k">{t('rj.quick')}</span>
            {(['q1', 'q2', 'q3'] as const).map((q) => (
              <button
                key={q}
                type="button"
                className="pr-qchip"
                disabled={busy}
                onClick={() => {
                  setText(t(`rj.${q}`))
                  area.current?.focus()
                }}
              >
                {t(`rj.${q}`)}
              </button>
            ))}
          </div>
        )}
      </form>
    </Sheet>
  )
}

// ---- Edit --------------------------------------------------------------------------------------

/** What the owner typed, as a plain decimal: Arabic-Indic digits and separators normalised. */
export function clean(v: string): string {
  return v
    .replace(/[‎‏⁦-⁩]/g, '')
    .replace(/[٠-٩]/g, (c) => String('٠١٢٣٤٥٦٧٨٩'.indexOf(c)))
    .replace(/٫/g, '.')
    .replace(/[,\s٬]/g, '')
}

const NUM = /^\d+(\.\d+)?$/
const SHARE = /^\d+$/

export interface EditForm {
  size: string
  stop: string
  tps: Array<{ price: string; pct: string }>
}

/**
 * The Edit sheet's errors, keyed `size`, `stop`, `tp<i>`, `sh<i>`, `sum`. `widest` is the stop the
 * proposal has now: a buy's stop may only rise from it and a sell's only fall (stops only tighten).
 */
export function editErrors(
  f: EditForm,
  ctx: { side: 'buy' | 'sell'; zone: [string, string]; widest: string; cap: string | null; withSize: boolean },
  t: (key: string, opts?: Record<string, unknown>) => string,
  fmt: (v: string) => string,
): Record<string, string> {
  const e: Record<string, string> = {}
  const long = ctx.side === 'buy'
  const lo = dec(ctx.zone[0])!
  const hi = dec(ctx.zone[1])!
  if (ctx.withSize) {
    const size = clean(f.size)
    if (size === '') e.size = t('err.sizeEmpty')
    else if (!NUM.test(size)) e.size = t('err.num')
    else if (cmp(dec(size)!, 0n) <= 0) e.size = t('err.sizeMin')
    else if (ctx.cap !== null && dec(ctx.cap) !== null && cmp(dec(size)!, dec(ctx.cap)!) > 0) e.size = t('err.sizeMax', { cap: formatMoney(ctx.cap, 'USD') })
  }
  const stopS = clean(f.stop)
  const widest = dec(ctx.widest)!
  if (!NUM.test(stopS)) e.stop = t('err.num')
  else {
    const stop = dec(stopS)!
    if (long ? cmp(stop, widest) < 0 : cmp(stop, widest) > 0) e.stop = t('err.stopWiden', { stop: fmt(ctx.widest) })
    else if (long && cmp(stop, lo) >= 0) e.stop = t('err.stopSide', { lo: fmt(ctx.zone[0]) })
    else if (!long && cmp(stop, hi) <= 0) e.stop = t('err.stopSideShort', { hi: fmt(ctx.zone[1]) })
  }
  let prev: bigint | null = null
  let shareError = false
  f.tps.forEach((x, i) => {
    const ps = clean(x.price)
    if (!NUM.test(ps)) e[`tp${i}`] = t('err.num')
    else {
      const p = dec(ps)!
      if (long ? cmp(p, hi) <= 0 : cmp(p, lo) >= 0) e[`tp${i}`] = long ? t('err.tpSide', { hi: fmt(ctx.zone[1]) }) : t('err.tpSideShort', { lo: fmt(ctx.zone[0]) })
      else if (prev !== null && (long ? cmp(p, prev) <= 0 : cmp(p, prev) >= 0)) e[`tp${i}`] = t('err.tpOrder')
      prev = p
    }
    const s = clean(x.pct)
    if (!SHARE.test(s) || Number(s) < 1 || Number(s) > 100) {
      e[`sh${i}`] = t('err.share')
      shareError = true
    }
  })
  const sum = f.tps.reduce((n, x) => n + (Number(clean(x.pct)) || 0), 0)
  if (!shareError && sum !== 100) e.sum = t('err.sum', { n: `${sum}%` })
  return e
}

/** Σ share × R over the targets, against the mid entry and the stop. */
function expectedR(zone: [string, string], stop: string, tps: EditForm['tps']): string | null {
  const mid = (dec(zone[0])! + dec(zone[1])!) / 2n
  const s = dec(stop)
  if (s === null || s === mid) return null
  let sum = 0n
  for (const x of tps) {
    const p = dec(clean(x.price))
    const pct = dec(clean(x.pct))
    if (p === null || pct === null) return null
    sum += mul(div(p - mid, mid - s), div(pct, fromInt(100)))
  }
  return decStr(sum, 2)
}

function Field({
  id,
  label,
  value,
  onChange,
  note,
  error,
  unit,
  share,
  disabled,
}: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  note?: ReactNode
  error?: string
  unit?: string
  share?: boolean
  disabled: boolean
}) {
  return (
    <div className={`pr-ef${share ? ' is-share' : ''}${error ? ' is-error' : ''}`}>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="pr-input-wrap">
        <input
          id={id}
          className={`${INPUT_CLASSES} input num`}
          dir="ltr"
          inputMode="decimal"
          autoComplete="off"
          spellCheck={false}
          value={value}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={`${id}-msg`}
          onChange={(e) => onChange(e.target.value)}
          data-testid={id}
        />
        {unit && <span className="pr-unit">{unit}</span>}
      </div>
      {error ? (
        <p id={`${id}-msg`} className="pr-err" data-testid={`${id}-error`}>
          <CircleAlert aria-hidden="true" strokeWidth={1.5} />
          <span>{error}</span>
        </p>
      ) : note ? (
        <p id={`${id}-msg`} className="field-hint">
          {note}
        </p>
      ) : null}
    </div>
  )
}

export function EditSheet({
  proposal: p,
  zone,
  stake,
  decimals,
  lang,
  onClose,
  onSaved,
  onConflict,
}: {
  proposal: ProposalDetail
  zone: [string, string]
  stake: Stake
  decimals: number
  lang: Lang
  onClose: () => void
  onSaved: (next: ProposalDetail) => void
  onConflict: (err: ApiError) => void
}) {
  const { t } = useTranslation('proposal')
  const id = useId()
  const fmt = (v: string) => formatFixed(v, decimals, lang)
  const bare = (v: string | null) => (v === null ? '' : formatFixed(v, decimals, 'en').replace(/,/g, ''))
  const sizeStart = sizeUsdOf(p, stake)
  const withSize = sizeStart !== null && stake.sizingEquity !== null
  const start: EditForm = {
    size: sizeStart ?? '',
    stop: bare(p.stop),
    tps: p.take_profits.map((x) => ({ price: bare(x.price), pct: x.pct ? decStr(dec(x.pct)!, 0) : '' })),
  }
  const [f, setF] = useState<EditForm>(start)
  const [busy, setBusy] = useState(false)
  const [server, setServer] = useState<Record<string, string>>({})
  const sizeRef = useRef<HTMLDivElement>(null)

  const errors = { ...editErrors(f, { side: p.side, zone, widest: p.stop!, cap: stake.perTradeCap, withSize }, t, fmt), ...server }
  const same =
    clean(f.size) === clean(start.size) &&
    clean(f.stop) === clean(start.stop) &&
    f.tps.every((x, i) => clean(x.price) === clean(start.tps[i]!.price) && clean(x.pct) === clean(start.tps[i]!.pct))
  const bad = Object.keys(errors).length > 0
  const set = (patch: Partial<EditForm>) => {
    setServer({})
    setF((cur) => ({ ...cur, ...patch }))
  }
  const setTp = (i: number, patch: Partial<EditForm['tps'][number]>) => set({ tps: f.tps.map((x, j) => (j === i ? { ...x, ...patch } : x)) })

  const drawable = !errors.stop && f.tps.every((x, i) => !errors[`tp${i}`] && NUM.test(clean(x.price)))
  const exp = drawable && !errors.sum ? expectedR(zone, clean(f.stop), f.tps) : null
  const dir = directionOf(p.side)

  const submit = async (e?: FormEvent) => {
    e?.preventDefault()
    if (bad || same || busy) return
    const body: { size?: string; stop?: string; targets?: Array<{ price: string; pct: string }> } = {}
    if (withSize && clean(f.size) !== clean(start.size)) {
      const pct = sizePctOf(clean(f.size), stake.sizingEquity)
      if (pct) body.size = pct
    }
    if (clean(f.stop) !== clean(start.stop)) body.stop = clean(f.stop)
    if (f.tps.some((x, i) => clean(x.price) !== clean(start.tps[i]!.price) || clean(x.pct) !== clean(start.tps[i]!.pct))) {
      body.targets = f.tps.map((x) => ({ price: clean(x.price), pct: clean(x.pct) }))
    }
    setBusy(true)
    try {
      const res = await proposalApi.edit(p.id, body)
      onSaved(res.proposal)
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        const fe = err.fieldErrors
        const next: Record<string, string> = {}
        if (fe.stop?.[0]) next.stop = fe.stop[0]
        if (fe.size?.[0]) next.size = fe.size[0]
        const tk = Object.keys(fe).find((k) => k.startsWith('targets'))
        if (tk) next.sum = fe[tk]![0]!
        setServer(next)
      } else if (err instanceof ApiError) onConflict(err)
      else onConflict(new ApiError(0, 'network'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet
      open
      onOpenChange={(o) => {
        if (!o && !busy) onClose()
      }}
      title={t('ed.title')}
      description={fillSlots(t('ed.text', { time: slot('time') }), { time: <Fig>{p.approval_expires_at ? formatTime(p.approval_expires_at, lang) : '—'}</Fig> })}
      className="pr-edit-sheet"
      onOpenAutoFocus={(e) => {
        e.preventDefault()
        sizeRef.current?.querySelector('input')?.focus()
      }}
      actions={
        <>
          <span className="pr-ed-hint" data-testid="pr-ed-hint">
            {!bad && same ? t('ed.noChange') : ''}
          </span>
          <SheetClose>
            <Button disabled={busy}>{t('cancel')}</Button>
          </SheetClose>
          <Button variant="primary" disabled={bad || same} loading={busy} onClick={() => void submit()} data-testid="pr-edit-save">
            {busy ? t('ed.busy') : t('ed.save')}
          </Button>
        </>
      }
    >
      <form className="pr-sheet pr-edit grid gap-3.5" onSubmit={(e) => void submit(e)} noValidate>
        <div className={`pr-ed-preview${drawable ? '' : ' is-stale'}`} aria-live="polite">
          <span className="pr-k">{t('ed.preview')}</span>
          {drawable && (
            <div className="pr-ed-ladder">
              <Ladder side={dir} entry={zone} stop={clean(f.stop)} targets={f.tps.map((x) => ({ price: clean(x.price), pct: clean(x.pct) || null }))} decimals={decimals} lang={lang} compact />
            </div>
          )}
          <p className="pr-ed-exp">{exp ? fillSlots(t('ed.exp', { r: slot('r') }), { r: <Fig>{rText(exp, 2).replace(/^\+/, '')}</Fig> }) : ''}</p>
        </div>
        <div className="pr-ed-grid" ref={sizeRef}>
          {withSize && (
            <Field
              id={`${id}-size`}
              label={t('ed.size')}
              value={f.size}
              onChange={(v) => set({ size: v })}
              unit="USDT"
              note={stake.perTradeCap ? fillSlots(t('ed.sizeNote', { cap: slot('cap') }), { cap: <Fig>{formatMoney(stake.perTradeCap, 'USD', lang)}</Fig> }) : undefined}
              error={errors.size}
              disabled={busy}
            />
          )}
          <Field
            id={`${id}-stop`}
            label={t('ed.stop')}
            value={f.stop}
            onChange={(v) => set({ stop: v })}
            note={fillSlots(t('ed.stopNote', { stop: slot('stop') }), { stop: <Fig>{fmt(p.stop!)}</Fig> })}
            error={errors.stop}
            disabled={busy}
          />
        </div>
        {f.tps.length > 0 && (
          <fieldset className="pr-ed-tps">
            <legend className="field-label">{t('ed.targets')}</legend>
            {f.tps.map((x, i) => (
              <div key={i} className="pr-tp">
                <Field id={`${id}-tp${i}`} label={t('ed.tp', { n: i + 1 })} value={x.price} onChange={(v) => setTp(i, { price: v })} error={errors[`tp${i}`]} disabled={busy} />
                <Field id={`${id}-sh${i}`} label={t('ed.share', { n: i + 1 })} value={x.pct} onChange={(v) => setTp(i, { pct: v })} unit="%" share error={errors[`sh${i}`]} disabled={busy} />
              </div>
            ))}
            <p className={`pr-ed-sum${errors.sum ? ' is-error' : ''}`} data-testid="pr-ed-sum">
              {errors.sum ? <CircleAlert className="icon" aria-hidden="true" /> : <CircleCheck className="icon" aria-hidden="true" />}
              <span>{errors.sum ?? fillSlots(t('ed.sum', { n: slot('n') }), { n: <Fig>100%</Fig> })}</span>
            </p>
          </fieldset>
        )}
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </Sheet>
  )
}
