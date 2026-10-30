import { CircleAlert, Plus, X } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed } from '../../lib/format'
import { Button } from '../../ui'
import { cx } from '../../ui/cx'
import { useDir } from '../../ui/useDir'
import { useLang } from '../portfolio/hooks'
import { displayHost, isIdn, normaliseHost, type ListWhich } from './model'

/*
 * One site list as a token field (design v9 settings-agents.js listRow): chips with a remove
 * button, then a text input and Add. Keyboard: Enter adds, Escape clears, Backspace in an empty
 * input moves to the last chip; on a chip the arrows (mirrored in RTL), Home and End move, and
 * Delete or Backspace removes it, focusing the next chip or the input. One chip is in the Tab
 * order at a time (roving tabindex).
 */
export function DomainList({
  which,
  list,
  saved,
  bad,
  error,
  changed,
  onChange,
  announce,
}: {
  which: ListWhich
  list: readonly string[]
  saved: readonly string[]
  bad: ReadonlySet<string>
  error: string | null
  changed: boolean
  onChange: (next: string[]) => void
  announce: (text: string) => void
}) {
  const { t } = useTranslation('settings-agents')
  const lang = useLang()
  const dir = useDir()
  const [text, setText] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const chipRefs = useRef<(HTMLButtonElement | null)[]>([])
  /** Where focus goes after the list re-renders: a chip index, or the input. */
  const pendingFocus = useRef<number | 'input' | null>(null)
  const name = t(which)
  const id = `sa-in-${which}`

  useEffect(() => {
    const f = pendingFocus.current
    if (f === null) return
    pendingFocus.current = null
    if (f === 'input') inputRef.current?.focus()
    else chipRefs.current[f]?.focus()
  })

  const roving = Math.min(active, list.length - 1)

  const add = () => {
    const host = normaliseHost(text)
    if (!host) {
      inputRef.current?.focus()
      return
    }
    if (list.includes(host)) {
      announce(t('dup', { host: displayHost(host) }))
      inputRef.current?.select()
      return
    }
    setText('')
    setActive(list.length)
    pendingFocus.current = 'input'
    onChange([...list, host])
    announce(t('added', { host: displayHost(host), list: name }))
  }

  const remove = (i: number, byKey: boolean) => {
    const host = list[i]
    if (host === undefined) return
    const next = list.filter((_, j) => j !== i)
    const to = Math.min(i, next.length - 1)
    setActive(Math.max(0, to))
    if (byKey) pendingFocus.current = next.length ? to : 'input'
    onChange(next)
    announce(t('removed', { host: displayHost(host), list: name }))
  }

  const focusChip = (i: number) => {
    setActive(i)
    chipRefs.current[i]?.focus()
  }

  const onInputKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      add()
    } else if (e.key === 'Escape' && text) {
      e.preventDefault()
      e.stopPropagation()
      setText('')
    } else if (e.key === 'Backspace' && !text && list.length) {
      e.preventDefault()
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

  return (
    <div className={cx('sa-row sa-list', changed && 'is-changed')} data-list={which} data-testid={`list-${which}`}>
      <div className="sa-list-main">
        <div className="sa-list-head">
          <label className="text-callout" htmlFor={id}>
            {name}
            {changed && <span className="sr-only">, {t('changed')}</span>}
          </label>
          <span className="sa-list-count">
            {list.length ? t('count', { count: list.length, n: formatFixed(list.length, 0, lang) }) : t(which === 'allow' ? 'anySite' : 'noSites')}
          </span>
        </div>
        <div className={cx('sa-tokens', error && 'is-error')}>
          {list.length > 0 && (
            <ul className="sa-chips" aria-label={name}>
              {list.map((host, i) => {
                const shown = displayHost(host)
                const invalid = bad.has(host)
                return (
                  <li key={host} className={cx('sa-chip', invalid && 'is-invalid', !saved.includes(host) && 'is-new')} data-invalid={invalid || undefined}>
                    {invalid && <CircleAlert aria-hidden="true" className="sa-chip-bad" strokeWidth={2} />}
                    <bdi dir="ltr" className="sa-chip-host" title={isIdn(host) ? t('idnTip', { puny: host }) : undefined}>
                      {shown}
                    </bdi>
                    <button
                      ref={(el) => {
                        chipRefs.current[i] = el
                      }}
                      type="button"
                      className="sa-chip-x"
                      tabIndex={i === roving ? 0 : -1}
                      aria-label={t('rm', { host: shown })}
                      aria-invalid={invalid || undefined}
                      onFocus={() => setActive(i)}
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
            className="sa-token-input"
            value={text}
            placeholder={t('place')}
            dir="auto"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            aria-invalid={error ? true : undefined}
            aria-describedby={`sa-${which}-note${error ? ` sa-${which}-err` : ''}`}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onInputKey}
          />
          <Button variant="secondary" size="sm" className="sa-add" icon={<Plus />} aria-label={t('addTo', { list: name })} onClick={add}>
            {t('add')}
          </Button>
        </div>
        {error && (
          <p className="st-field-error text-footnote" id={`sa-${which}-err`} data-testid={`error-${which}`}>
            <CircleAlert aria-hidden="true" strokeWidth={1.75} />
            <span>{error}</span>
          </p>
        )}
        <p className="text-footnote text-label-secondary" id={`sa-${which}-note`}>
          {t(which === 'allow' ? 'allowNote' : 'denyNote')}
        </p>
      </div>
    </div>
  )
}
