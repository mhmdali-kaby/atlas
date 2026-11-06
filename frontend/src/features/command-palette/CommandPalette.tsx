import { Command } from 'cmdk'
import { CornerDownLeft, Search, SearchX } from 'lucide-react'
import { Dialog } from 'radix-ui'
import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import { cx } from '../../ui/cx'
import { entryHooks, GROUP_ORDER, matchRank, sortEntries, staticEntries } from './registry'
import { focusBeforeOpen, paletteKeys, skipFocusReturn, usePaletteStore } from './store'
import type { PaletteEntry, PaletteGroup } from './types'
import './palette.css'

const NS = 'command-palette'

/**
 * The ⌘K command palette (brief §15, decision 265 (2), design v22 §6). Mounted once in the
 * signed-in shell: ⌘K / Ctrl+K opens it (not while a sheet is asking something) and closes it
 * again; Esc and the scrim close it and focus goes back where it was. Entries come from every
 * feature's palette.ts; labels match in English and Arabic whichever language is showing.
 */
export function CommandPalette() {
  const open = usePaletteStore((s) => s.open)
  const setOpen = usePaletteStore((s) => s.setOpen)
  const { t } = useTranslation(NS)

  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey || e.key.toLowerCase() !== 'k') return
      const state = usePaletteStore.getState()
      if (!state.open && document.querySelector('[data-sheet]')) return // a sheet is asking: answer it first
      e.preventDefault()
      e.stopPropagation()
      state.setOpen(!state.open)
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="pal-scrim" />
        <Dialog.Content
          className="pal"
          aria-describedby={undefined}
          data-testid="command-palette"
          onCloseAutoFocus={(e) => {
            e.preventDefault()
            const el = focusBeforeOpen()
            if (el && document.contains(el)) el.focus({ preventScroll: true })
          }}
        >
          <Dialog.Title className="sr-only">{t('label')}</Dialog.Title>
          <PaletteBody
            onChoose={(entry, go) => {
              skipFocusReturn()
              setOpen(false)
              go(entry)
            }}
          />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

interface Ranked {
  entry: PaletteEntry
  names: string[]
}

function PaletteBody({ onChoose }: { onChoose: (entry: PaletteEntry, go: (entry: PaletteEntry) => void) => void }) {
  const { t, i18n } = useTranslation([NS, 'shell'])
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  // The hooks list is fixed when the module loads, so every render calls the same hooks in order.
  const dynamic = entryHooks.flatMap((provide) => provide(query))

  const en = i18n.getFixedT('en')
  const ar = i18n.getFixedT('ar')
  const all = useMemo(() => sortEntries([...staticEntries, ...dynamic]), [dynamic])

  const results: Ranked[] = useMemo(() => {
    const ranked = all.map((entry) => {
      const names = [en(entry.labelKey), ar(entry.labelKey)]
      let rank = matchRank(names, entry.keywords, query)
      // The area's name in both languages lists its pages in order ("المحفظة" finds Overview first),
      // just after pages whose own label matches as well.
      if (entry.area) {
        const area = matchRank([en(`shell:area.${entry.area}`), ar(`shell:area.${entry.area}`)], undefined, query)
        if (area >= 0 && area < 3) rank = rank < 0 ? area + 0.5 : Math.min(rank, area + 0.5)
      }
      return { entry, names, rank }
    })
    return ranked
      .filter((r) => r.rank >= 0)
      .sort((a, b) => GROUP_ORDER.indexOf(a.entry.group) - GROUP_ORDER.indexOf(b.entry.group) || a.rank - b.rank)
      .map(({ entry, names }) => ({ entry, names }))
  }, [all, query, en, ar])

  const choosable = results.filter((r) => !r.entry.disabled)
  const firstId = choosable[0]?.entry.id ?? ''
  const [selected, setSelected] = useState(firstId)
  // A new query selects its first result, as Spotlight does.
  const [lastQuery, setLastQuery] = useState(query)
  if (lastQuery !== query) {
    setLastQuery(query)
    setSelected(firstId)
  } else if (selected && !choosable.some((r) => r.entry.id === selected)) {
    setSelected(firstId)
  }

  function go(entry: PaletteEntry) {
    if (entry.to) void navigate(entry.to)
    else void entry.run?.()
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== 'Tab' || choosable.length === 0) return
    // Tab moves like ↓ (Shift+Tab like ↑), wrapping; focus stays in the search line.
    e.preventDefault()
    const i = choosable.findIndex((r) => r.entry.id === selected)
    const next = (i + (e.shiftKey ? -1 : 1) + choosable.length) % choosable.length
    setSelected(choosable[next]!.entry.id)
  }

  const keys = paletteKeys()
  const groups = GROUP_ORDER.map((g) => ({ g, items: results.filter((r) => r.entry.group === g) })).filter((x) => x.items.length > 0)

  return (
    <Command
      shouldFilter={false}
      loop
      value={selected}
      onValueChange={setSelected}
      label={t('label')}
      onKeyDown={onKeyDown}
      className="pal-cmd"
    >
      <div className="pal-search">
        <Search aria-hidden="true" className="pal-search-icon" />
        <Command.Input
          value={query}
          onValueChange={setQuery}
          className="pal-input"
          placeholder={t('placeholder')}
          dir="auto"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          aria-describedby="pal-count"
          data-testid="palette-input"
        />
        <Dialog.Close className="pal-esc" dir="ltr" aria-label={t('k.close')}>
          esc
        </Dialog.Close>
      </div>
      <Command.List className="pal-list" aria-label={t('results')}>
        {groups.map(({ g, items }) => (
          <Command.Group key={g} heading={t(`g.${g as PaletteGroup}`)} className="pal-group">
            {items.map((r) => (
              <Row key={r.entry.id} ranked={r} query={query} onSelect={() => onChoose(r.entry, go)} />
            ))}
          </Command.Group>
        ))}
        {results.length === 0 && (
          <div className="pal-empty" role="status">
            <SearchX aria-hidden="true" className="pal-empty-icon" />
            <p className="pal-empty-title">{t('none', { q: query.trim() })}</p>
            <p className="pal-empty-body">{t('noneBody')}</p>
          </div>
        )}
      </Command.List>
      <div className="pal-foot" aria-hidden="true">
        <span className="pal-keys">
          <Kbd>↑</Kbd>
          <Kbd>↓</Kbd>
          <span>{t('k.move')}</span>
        </span>
        <span className="pal-keys">
          <Kbd>↵</Kbd>
          <span>{t('k.open')}</span>
        </span>
        <span className="pal-keys">
          <Kbd>esc</Kbd>
          <span>{t('k.close')}</span>
        </span>
        <span className="pal-keys pal-keys-end">
          <Kbd>{keys}</Kbd>
          <span>{t('k.anywhere')}</span>
        </span>
      </div>
      <p className="sr-only" id="pal-count" aria-live="polite">
        {t('count', { count: choosable.length })}
      </p>
    </Command>
  )
}

function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="pal-kbd" dir="ltr">
      {children}
    </kbd>
  )
}

/** Bolds the matched part of the label, the way Spotlight does. */
function Marked({ label, query }: { label: string; query: string }) {
  const q = query.trim().toLowerCase()
  const i = q ? label.toLowerCase().indexOf(q) : -1
  if (i < 0) return <>{label}</>
  return (
    <>
      {label.slice(0, i)}
      <b>{label.slice(i, i + q.length)}</b>
      {label.slice(i + q.length)}
    </>
  )
}

function Row({ ranked, query, onSelect }: { ranked: Ranked; query: string; onSelect: () => void }) {
  const { t } = useTranslation([NS, 'shell'])
  const { entry } = ranked
  const Icon = entry.icon
  const label = t(entry.labelKey)
  const end = entry.group === 'pages' && entry.area ? t(`shell:area.${entry.area}`) : entry.group === 'actions' ? t('kind.action') : null
  return (
    <Command.Item
      value={entry.id}
      disabled={entry.disabled}
      onSelect={onSelect}
      className={cx('pal-opt', entry.tone === 'stop' && 'is-stop', entry.disabled && 'is-disabled')}
      data-testid={`palette-item-${entry.id}`}
    >
      <span className={cx('pal-icon', entry.tone === 'stop' && 'is-stop')} aria-hidden="true">
        <Icon strokeWidth={1.75} />
      </span>
      <span className="pal-text">
        <span className="pal-label">
          <Marked label={label} query={query} />
        </span>
        {entry.subKey && <span className="pal-sub">{t(entry.subKey, entry.subValues)}</span>}
      </span>
      <span className="pal-end">
        {end && <span className="pal-area">{end}</span>}
        <span className="pal-enter" aria-hidden="true">
          <CornerDownLeft />
        </span>
      </span>
    </Command.Item>
  )
}
