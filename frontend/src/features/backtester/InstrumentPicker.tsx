import { Plus, X } from 'lucide-react'
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { TradingEnv } from '../../app/shell/tradingEnv'
import { Spinner } from '../../ui'
import { cx } from '../../ui/cx'
import { useInstrumentSearch } from '../warroom/watchlist'
import { LIMITS } from './model'
import { FieldError, NS } from './parts'

/*
 * Instruments as chips with a search field at their end (design .bt-chips): a combobox over
 * ATL-119's GET /api/instruments?q= (the env's live instruments, debounced 200 ms). Enter or a
 * click adds the active option; ↑/↓ move; Escape closes the list; Backspace in an empty field
 * removes the last chip. 1 to 5, no duplicates.
 */

const DEBOUNCE_MS = 200

interface Props {
  env: TradingEnv
  value: string[]
  error?: string
  onChange: (next: string[]) => void
}

export function InstrumentPicker({ env, value, error, onChange }: Props) {
  const { t } = useTranslation(NS)
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(q.trim()), DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [q])

  const search = useInstrumentSearch(env, debounced)
  const options = (search.data?.data ?? []).map((h) => h.inst_id).filter((i) => !value.includes(i))
  const full = value.length >= LIMITS.maxInsts
  const showList = open && q.trim() !== '' && !full
  const searching = q.trim() !== debounced || search.isFetching
  const current = Math.min(active, Math.max(0, options.length - 1))

  const add = (instId: string | undefined) => {
    if (!instId || full || value.includes(instId)) return
    onChange([...value, instId])
    setQ('')
    setDebounced('')
    setActive(0)
    input.current?.focus()
  }
  const remove = (instId: string) => {
    onChange(value.filter((x) => x !== instId))
    input.current?.focus()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setOpen(true)
      if (options.length) setActive((current + (e.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (!searching) add(options[current])
    } else if (e.key === 'Escape') {
      if (showList) {
        e.preventDefault()
        setOpen(false)
      }
    } else if (e.key === 'Backspace' && q === '' && value.length) {
      remove(value[value.length - 1]!)
    }
  }

  return (
    <div className={cx('bt-group bt-insts', error && 'is-error')} data-field="insts">
      <div className="bt-chips">
        {value.map((instId) => {
          const [base, ...rest] = instId.split('-')
          return (
            <span key={instId} className="bt-chip" data-testid="bt-inst-chip">
              <bdi dir="ltr">
                <b>{base}</b>
                {rest.length ? `-${rest.join('-')}` : ''}
              </bdi>
              <button type="button" className="bt-chip-x" aria-label={t('f.remove', { what: instId })} onClick={() => remove(instId)}>
                <X aria-hidden="true" />
              </button>
            </span>
          )
        })}
        <div className="bt-add-inst">
          <Plus aria-hidden="true" strokeWidth={1.75} />
          <input
            ref={input}
            id="bt-inst"
            className="bt-inst-input"
            type="text"
            dir="ltr"
            role="combobox"
            aria-label={t('f.add')}
            aria-expanded={showList}
            aria-controls={`${id}-list`}
            aria-autocomplete="list"
            aria-activedescendant={showList && options.length ? `${id}-opt-${current}` : undefined}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'bt-err-insts' : undefined}
            placeholder={t('f.addPh')}
            autoComplete="off"
            spellCheck={false}
            disabled={full}
            value={q}
            onChange={(e) => {
              setQ(e.target.value)
              setOpen(true)
              setActive(0)
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => window.setTimeout(() => setOpen(false), 120)}
            onKeyDown={onKeyDown}
          />
        </div>
      </div>
      {showList && (
        <ul id={`${id}-list`} role="listbox" className="bt-inst-list" aria-label={t('f.insts')}>
          {searching && options.length === 0 ? (
            <li className="bt-inst-note" role="presentation">
              <Spinner /> {t('f.searching')}
            </li>
          ) : options.length === 0 ? (
            <li className="bt-inst-note" role="presentation">
              {t('f.noMatch', { q: q.trim() })}
            </li>
          ) : (
            options.map((instId, i) => (
              <li
                key={instId}
                id={`${id}-opt-${i}`}
                role="option"
                aria-selected={i === current}
                className={cx('bt-inst-opt', i === current && 'is-active')}
                onPointerDown={(e) => e.preventDefault()}
                onClick={() => add(instId)}
              >
                <bdi dir="ltr">{instId}</bdi>
              </li>
            ))
          )}
        </ul>
      )}
      {error && <FieldError id="bt-err-insts" className="bt-pad">{error}</FieldError>}
    </div>
  )
}
