import { CircleAlert, Search } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { Spinner } from '../../ui'
import { fillSlots, slot } from './slots'
import { InstrumentId } from './text'
import { baseOf, isDerivative, useInstrumentSearch, type WriteResult } from './watchlist'

/*
 * The Pin instrument popover (design v9 §A7): a combobox over ATL-119's GET /api/instruments?q=
 * (debounced 200 ms, the API's order kept), with the day's not-picked candidates when the field
 * is empty. Rows already on the list or blocked in Settings are shown, disabled and skipped by the
 * arrows. Pinning is not optimistic: the server validates it, so a 422 stays here under the field.
 */

interface Option {
  instId: string
  assetClass: string
  on: boolean
  blocked: boolean
}

export interface PinPopoverProps {
  env: TradingEnv
  /** Not-picked candidates of the day, shown before the owner types. */
  candidates: Array<{ instId: string; assetClass: string }>
  onList: Set<string>
  onPin: (instId: string) => Promise<WriteResult>
  onClose: (returnFocus: boolean) => void
}

const DEBOUNCE_MS = 200

export function PinPopover({ env, candidates, onList, onPin, onClose }: PinPopoverProps) {
  const { t } = useTranslation('warroom')
  const id = useId()
  const root = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [active, setActive] = useState(-1)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [errorInst, setErrorInst] = useState('')

  useEffect(() => input.current?.focus(), [])
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(q.trim()), DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [q])

  // A click outside closes it (focus stays where the owner clicked).
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node) && !(e.target as Element).closest?.('[data-wl-pin-trigger]')) onClose(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [onClose])

  const search = useInstrumentSearch(env, debounced)
  const searching = q.trim() !== '' && (q.trim() !== debounced || search.isFetching)

  const groups = useMemo(() => {
    if (!debounced) {
      const items = candidates.filter((c) => !onList.has(c.instId)).map((c) => ({ instId: c.instId, assetClass: c.assetClass, on: false, blocked: false }))
      return items.length ? [{ label: t('wl.groupNotPicked'), items }] : []
    }
    const items: Option[] = (search.data?.data ?? []).map((h) => ({ instId: h.inst_id, assetClass: h.asset_class, on: onList.has(h.inst_id), blocked: h.blocked }))
    return items.length ? [{ label: t('wl.groupResults'), items }] : []
  }, [debounced, candidates, onList, search.data, t])
  const flat = groups.flatMap((g) => g.items)
  const selectable = (o: Option | undefined) => Boolean(o && !o.on && !o.blocked)

  // The active row stays on a selectable option as the list changes.
  const current = selectable(flat[active]) ? active : flat.findIndex(selectable)

  const pin = async (o: Option | undefined) => {
    if (!o || !selectable(o) || busy) return
    setBusy(o.instId)
    setError(null)
    const r = await onPin(o.instId)
    setBusy(null)
    if (r.ok || r.status === 409) return // the card closes the popover and says what happened
    if (r.status === 422) {
      const msg = (r.field ?? '').toLowerCase()
      const key = msg.includes('blocklist') ? 'blocked' : msg.includes('not live') ? 'notLive' : 'unknown'
      setError(t(`wl.err.${key}`, { inst: slot('inst') }))
    } else {
      setError(t('wl.err.server', { inst: slot('inst') }))
    }
    setErrorInst(o.instId)
    input.current?.focus()
  }

  const move = (dir: 1 | -1) => {
    if (!flat.some(selectable)) return
    let i = current
    for (let n = 0; n < flat.length; n++) {
      i = (i + dir + flat.length) % flat.length
      if (selectable(flat[i])) break
    }
    setActive(i)
    document.getElementById(`${id}-opt-${i}`)?.scrollIntoView({ block: 'nearest' })
  }
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      move(1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      move(-1)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      void pin(flat[current])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onClose(true)
    }
  }

  const clsWord = (o: Option) => {
    const w = t(`wl.cls.${o.assetClass}`, { defaultValue: t('wl.cls.other') })
    return isDerivative(o.instId) ? w : t('wl.spotOf', { cls: w })
  }

  let n = -1
  return (
    <div ref={root} className="wl-pop" role="dialog" aria-label={t('wl.popLabel')} data-testid="wl-pop">
      <div className={`wl-pop-field${error ? ' is-error' : ''}`}>
        <Search aria-hidden="true" strokeWidth={1.5} />
        <input
          ref={input}
          type="text"
          dir="auto"
          role="combobox"
          aria-expanded="true"
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          aria-activedescendant={current >= 0 ? `${id}-opt-${current}` : undefined}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-err` : undefined}
          placeholder={t('wl.search')}
          aria-label={t('wl.search')}
          value={q}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            setQ(e.target.value)
            setError(null)
          }}
          onKeyDown={onKeyDown}
          data-testid="wl-pop-input"
        />
      </div>
      {error && (
        <p className="wl-pop-err" id={`${id}-err`} role="alert" data-testid="wl-pop-err">
          <CircleAlert aria-hidden="true" strokeWidth={1.5} />
          <span>{fillSlots(error, { inst: <InstrumentId id={errorInst} /> })}</span>
        </p>
      )}
      <div className="wl-pop-list" id={`${id}-list`} role="listbox" aria-label={t('wl.search')}>
        {searching ? (
          <p className="wl-pop-msg" role="status">
            <Spinner />
            <span>{t('wl.searching')}</span>
          </p>
        ) : flat.length === 0 ? (
          <p className="wl-pop-msg">{debounced ? t('wl.noMatch', { q: debounced }) : t('wl.typeToSearch')}</p>
        ) : (
          groups.map((g) => (
            <div key={g.label} role="group" aria-label={g.label}>
              <p className="wl-pop-group" role="presentation">
                {g.label}
              </p>
              {g.items.map((o) => {
                n += 1
                const i = n
                const dis = !selectable(o)
                return (
                  <div
                    key={o.instId}
                    id={`${id}-opt-${i}`}
                    role="option"
                    className={`wl-opt${dis ? ' is-dis' : ''}`}
                    aria-selected={i === current}
                    aria-disabled={dis || undefined}
                    data-testid="wl-opt"
                    data-inst={o.instId}
                    onMouseEnter={() => !dis && setActive(i)}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => void pin(o)}
                  >
                    <span className="mono-tile wl-opt-tile" aria-hidden="true">
                      {baseOf(o.instId)}
                    </span>
                    <span className="wl-opt-text">
                      <InstrumentId id={o.instId} />
                      <span className="wl-opt-cls">{clsWord(o)}</span>
                    </span>
                    {busy === o.instId ? <Spinner /> : o.on ? <span className="wl-opt-st">{t('wl.onList')}</span> : o.blocked ? <span className="wl-opt-st">{t('wl.blocked')}</span> : null}
                  </div>
                )
              })}
            </div>
          ))
        )}
      </div>
      {busy && (
        <p className="wl-pop-status" role="status">
          {fillSlots(t('wl.pinningInst', { inst: slot('inst') }), { inst: <InstrumentId id={busy} /> })}
        </p>
      )}
      <p className="wl-pop-hint" aria-hidden="true">
        {fillSlots(t('wl.hint', { ret: slot('ret'), esc: slot('esc') }), { ret: <kbd>{t('wl.keyReturn')}</kbd>, esc: <kbd>{t('wl.keyEsc')}</kbd> })}
      </p>
    </div>
  )
}
