import { useQueryClient } from '@tanstack/react-query'
import { CircleAlert, Plus, TriangleAlert, X } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../../lib/api'
import { Button } from '../../ui'
import { cx } from '../../ui/cx'
import { useDir } from '../../ui/useDir'
import { marketOf, searchKey, searchUrl, useInstrumentSearch, type Env, type Instrument, type ListKey } from './model'

/*
 * One instrument list (design v14 settings-markets.js listRow): chips with a remove button, then a
 * combobox backed by GET /api/instruments (ATL-119) and Add. Only an instId the active env's
 * universe returns can be added. Keyboard: ↑/↓ move through the matches, Enter adds the highlighted
 * match (or an exact instId), Escape closes the matches then clears, Backspace in an empty field
 * moves to the last chip; on a chip the arrows (mirrored in RTL), Home and End move, and Delete or
 * Backspace removes it. One chip is in the Tab order at a time.
 */

const WHICH = { whitelist: 'allow', blocklist: 'deny' } as const

export function InstrumentList({
  which,
  env,
  list,
  saved,
  other,
  warnings,
  serverError,
  changed,
  note,
  onChange,
  announce,
}: {
  which: ListKey
  env: Env
  list: readonly string[]
  saved: readonly string[]
  /** The other list, to mark matches that are already on it. */
  other: readonly string[]
  /** Allowed list only: the reason each instrument will still be left out. */
  warnings?: Readonly<Record<string, string>>
  serverError?: string
  changed: boolean
  note: string
  onChange: (next: string[]) => void
  announce: (text: string) => void
}) {
  const { t } = useTranslation('settings-markets')
  const dir = useDir()
  const client = useQueryClient()
  const short = WHICH[which]
  const name = t(short)
  const id = `mk-in-${short}`
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [chip, setChip] = useState(0)
  const [place, setPlace] = useState<CSSProperties | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const fieldRef = useRef<HTMLDivElement>(null)
  const chipRefs = useRef<(HTMLButtonElement | null)[]>([])
  const pendingFocus = useRef<number | 'input' | null>(null)

  const search = useInstrumentSearch(env, text)
  const items = text.trim() ? (search.data?.data ?? []) : []
  const stateOf = (instId: string): 'allowed' | 'blocked' | null => {
    const allow = which === 'whitelist' ? list : other
    const deny = which === 'blocklist' ? list : other
    if (allow.includes(instId)) return 'allowed'
    if (deny.includes(instId)) return 'blocked'
    return null
  }
  // On this list already: not addable. On the other list: addable (decision #250, the blocklist wins).
  const addable = (i: Instrument) => !list.includes(i.inst_id)
  const firstAddable = items.findIndex(addable)
  const current = active >= 0 && active < items.length && items[active] && addable(items[active]) ? active : firstAddable
  const showPop = open && text.trim() !== ''

  useEffect(() => {
    const f = pendingFocus.current
    if (f === null) return
    pendingFocus.current = null
    if (f === 'input') inputRef.current?.focus()
    else chipRefs.current[f]?.focus()
  })

  useLayoutEffect(() => {
    if (!showPop) return
    const measure = () => {
      const r = fieldRef.current?.getBoundingClientRect()
      if (!r) return
      const below = window.innerHeight - r.bottom
      const style: CSSProperties = { position: 'fixed', width: Math.min(r.width, 460) }
      if (dir === 'rtl') style.right = window.innerWidth - r.right
      else style.left = r.left
      if (below < 220 && r.top > below) style.bottom = window.innerHeight - r.top + 6
      else style.top = r.bottom + 6
      setPlace(style)
    }
    measure()
    window.addEventListener('scroll', measure, true)
    window.addEventListener('resize', measure)
    return () => {
      window.removeEventListener('scroll', measure, true)
      window.removeEventListener('resize', measure)
    }
  }, [showPop, dir])

  useEffect(() => {
    if (!showPop || search.isFetching) return
    announce(search.isError ? t('searchFailed') : t('matches', { count: items.length }))
    // Announce once per answer, not per render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showPop, search.isFetching, search.dataUpdatedAt])

  const add = (instId: string) => {
    if (list.includes(instId)) {
      const msg = t(which === 'whitelist' ? 'dupAllow' : 'dupDeny', { id: instId })
      setRefusal(msg)
      announce(msg)
      return
    }
    setText('')
    setOpen(false)
    setActive(-1)
    setRefusal(null)
    setChip(list.length)
    pendingFocus.current = 'input'
    onChange([...list, instId])
    announce(t('added', { id: instId, list: name }))
  }

  /** Enter or Add: the highlighted match, else an exact instId from the universe, else say why not. */
  const commit = async () => {
    const typed = text.trim()
    if (showPop && current >= 0 && items[current]) {
      add(items[current].inst_id)
      return
    }
    if (!typed) {
      inputRef.current?.focus()
      return
    }
    const want = typed.toUpperCase()
    let hits = items
    try {
      hits = (await client.fetchQuery<{ data: Instrument[] }>({ queryKey: searchKey(env, typed), queryFn: () => api.get(searchUrl(env, typed)), staleTime: 60_000 })).data
    } catch {
      // A failed search refuses below, like an unknown id.
    }
    const exact = hits.find((i) => i.inst_id.toUpperCase() === want)
    if (exact) {
      add(exact.inst_id)
      return
    }
    const msg = t('unknownInst', { text: typed })
    setRefusal(msg)
    announce(msg)
  }

  const remove = (i: number, byKey: boolean) => {
    const instId = list[i]
    if (instId === undefined) return
    const next = list.filter((_, j) => j !== i)
    const to = Math.min(i, next.length - 1)
    setChip(Math.max(0, to))
    if (byKey) pendingFocus.current = next.length ? to : 'input'
    onChange(next)
    announce(t('removed', { id: instId, list: name }))
  }

  const focusChip = (i: number) => {
    setChip(i)
    chipRefs.current[i]?.focus()
  }

  const move = (step: 1 | -1) => {
    if (!items.length) return
    const n = items.length
    let i = current
    for (let k = 0; k < n; k++) {
      i = i < 0 ? (step > 0 ? 0 : n - 1) : (i + step + n) % n
      const hit = items[i]
      if (hit && addable(hit)) {
        setActive(i)
        return
      }
    }
  }

  const onInputKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) setOpen(true)
      move(e.key === 'ArrowDown' ? 1 : -1)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      void commit()
    } else if (e.key === 'Escape') {
      if (showPop) {
        e.preventDefault()
        e.stopPropagation()
        setOpen(false)
      } else if (text) {
        e.preventDefault()
        e.stopPropagation()
        setText('')
        setRefusal(null)
      }
    } else if (e.key === 'Backspace' && !text && list.length) {
      e.preventDefault()
      setOpen(false)
      focusChip(list.length - 1)
    }
  }

  const onChipKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const forward = dir === 'rtl' ? 'ArrowLeft' : 'ArrowRight'
    const back = dir === 'rtl' ? 'ArrowRight' : 'ArrowLeft'
    if (e.key === forward) {
      e.preventDefault()
      if (i < list.length - 1) focusChip(i + 1)
      else inputRef.current?.focus()
    } else if (e.key === back) {
      e.preventDefault()
      if (i > 0) focusChip(i - 1)
    } else if (e.key === 'Home') {
      e.preventDefault()
      focusChip(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      focusChip(list.length - 1)
    } else if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault()
      remove(i, true)
    }
  }

  const error = refusal ?? serverError ?? null
  const roving = Math.min(chip, list.length - 1)
  const listboxId = `mk-lb-${short}`
  const optId = (n: number) => `mk-opt-${short}-${n}`

  return (
    <div className={cx('sa-row sa-list mk-list', changed && 'is-changed')} data-list={short} data-testid={`mk-list-${short}`}>
      <div className="sa-list-main">
        <div className="sa-list-head">
          <label className="sa-row-label" htmlFor={id}>
            {name}
            {changed && <span className="sr-only">, {t('changed')}</span>}
          </label>
          <span className="sa-list-count">{list.length ? t('listCount', { count: list.length }) : t(which === 'whitelist' ? 'anyInst' : 'none')}</span>
        </div>
        <div ref={fieldRef} className={cx('sa-tokens mk-tokens', error && 'is-error')}>
          {list.length > 0 && (
            <ul className="sa-chips" aria-label={name}>
              {list.map((instId, i) => {
                const warn = warnings?.[instId]
                return (
                  <li key={instId} className={cx('sa-chip mk-chip', !saved.includes(instId) && 'is-new', warn && 'is-warn')} data-inst={instId}>
                    {warn && (
                      <span className="mk-chip-warn" title={warn} aria-hidden="true">
                        <TriangleAlert strokeWidth={2} />
                      </span>
                    )}
                    <bdi className="sa-chip-host" dir="ltr">
                      {instId}
                    </bdi>
                    {warn && (
                      <span className="sr-only" data-testid={`mk-warn-${instId}`}>
                        , {warn}
                      </span>
                    )}
                    <button
                      type="button"
                      ref={(el) => {
                        chipRefs.current[i] = el
                      }}
                      className="sa-chip-x"
                      tabIndex={i === roving ? 0 : -1}
                      aria-label={t('rm', { id: instId })}
                      onFocus={() => setChip(i)}
                      onKeyDown={(e) => onChipKey(e, i)}
                      onClick={(e) => remove(i, e.detail === 0)}
                    >
                      <X aria-hidden="true" strokeWidth={2.25} />
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          <input
            ref={inputRef}
            id={id}
            className="sa-token-input mk-combo"
            role="combobox"
            aria-autocomplete="list"
            aria-expanded={showPop && items.length > 0}
            aria-controls={listboxId}
            aria-activedescendant={showPop && current >= 0 ? optId(current) : undefined}
            value={text}
            placeholder={t(list.length ? 'placeShort' : 'place')}
            dir="auto"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            aria-invalid={error ? true : undefined}
            aria-describedby={`mk-${short}-note${error ? ` mk-${short}-err` : ''}`}
            onChange={(e) => {
              setText(e.target.value)
              setOpen(true)
              setActive(-1)
              if (refusal) setRefusal(null)
            }}
            onKeyDown={onInputKey}
            onBlur={() => setOpen(false)}
          />
          <Button variant="secondary" size="sm" className="sa-add" icon={<Plus />} aria-label={t('addTo', { list: name })} onMouseDown={(e) => e.preventDefault()} onClick={() => void commit()}>
            {t('add')}
          </Button>
        </div>
        {error && (
          <p className="st-field-error text-footnote" id={`mk-${short}-err`} data-testid={`error-${which}`}>
            <CircleAlert aria-hidden="true" strokeWidth={1.75} />
            <span>{error}</span>
          </p>
        )}
        <p className="text-footnote text-label-secondary" id={`mk-${short}-note`} data-testid={`mk-${short}-note`}>
          {note}
        </p>
      </div>
      {showPop &&
        place &&
        createPortal(
          <div className="mk-pop" style={place} dir={dir} onMouseDown={(e) => e.preventDefault()}>
            {items.length > 0 ? (
              <ul className="mk-lb" role="listbox" id={listboxId} aria-label={name}>
                {items.map((i, n) => {
                  const st = stateOf(i.inst_id)
                  const market = marketOf(i)
                  const disabled = !addable(i)
                  return (
                    <li
                      key={i.inst_id}
                      id={optId(n)}
                      role="option"
                      className="mk-opt"
                      aria-selected={n === current}
                      aria-disabled={disabled || undefined}
                      onMouseEnter={() => !disabled && setActive(n)}
                      onClick={() => !disabled && add(i.inst_id)}
                    >
                      <bdi dir="ltr" className="mk-opt-id">
                        {i.inst_id}
                      </bdi>
                      {market && <span className="mk-opt-market">{t(`mShort.${market}`)}</span>}
                      {st && <span className="mk-opt-state">{t(`opt.${st}`)}</span>}
                    </li>
                  )
                })}
              </ul>
            ) : (
              <div className="mk-pop-empty" role="status">
                {search.isError ? t('searchFailed') : search.isFetching ? t('searching') : t('noMatch', { q: text.trim() })}
              </div>
            )}
          </div>,
          document.body,
        )}
    </div>
  )
}
