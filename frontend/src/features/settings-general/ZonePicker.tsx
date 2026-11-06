import { Check, ChevronsUpDown, Earth, Search, SearchX } from 'lucide-react'
import { Popover } from 'radix-ui'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { Lang } from '../../lib/format'
import { TEAM_TZ, zoneOffsetMin } from '../../lib/ownerZone'
import { allZones, offsetDigits, zoneCity, zoneCityLatin, zoneName, zoneOffset, hasLocalCity } from '../../lib/zones'
import { Button, Sheet } from '../../ui'
import { useDir } from '../../ui/useDir'

/*
 * The time-zone picker (design v21 owner-setup §7.3): a pop-up button showing the city and
 * offset; it opens a 420 px glass popover (a sheet on a phone) with a search combobox over a
 * listbox. "Suggested" comes first (Baghdad with "Team clock", This Mac, the current zone), then
 * every IANA zone plus UTC sorted by city in the UI language. ↑↓ move, Return chooses, Escape
 * closes and returns focus to the button.
 */

const NS = 'settings-general'
const PHONE_QUERY = '(max-width: 639px)'

/** Lower-case, no accents, Arabic hamza / ta marbuta / alif maqsura normalised, isolates dropped. */
export function normalise(s: string): string {
  return s
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[أإآ]/g, 'ا')
    .replace(/ة/g, 'ه')
    .replace(/ى/g, 'ي')
    .replace(/[_/]/g, ' ')
    .replace(/[⁦-⁩]/g, '')
    .replace(/−/g, '-')
}

export interface ZoneRow {
  zone: string
  city: string
  name: string
  cities: string[]
  key: string
}

export function zoneRows(lang: Lang, at: number): ZoneRow[] {
  const rows = allZones().map((zone) => {
    const off = offsetDigits(zoneOffsetMin(zone, at)).replace('−', '-')
    const city = zoneCity(zone, lang)
    const name = zoneName(zone, lang)
    const cities = [normalise(city), normalise(zoneCityLatin(zone)), normalise(zoneCity(zone, 'ar'))]
    return {
      zone,
      city,
      name,
      cities,
      key: normalise([city, zoneCityLatin(zone), zoneCity(zone, 'ar'), zone, name, `gmt${off}`, `utc${off}`, off].join(' ')),
    }
  })
  const coll = new Intl.Collator(lang === 'ar' ? 'ar' : 'en')
  return rows.sort((a, b) => coll.compare(a.city, b.city))
}

/** Rows matching a query: a city that starts with it first, then a word in it, then anything else. */
export function searchZones(rows: ZoneRow[], query: string): ZoneRow[] {
  const n = normalise(query.trim())
  if (!n) return rows
  const terms = n.split(/\s+/).filter(Boolean)
  const score = (r: ZoneRow) =>
    r.cities.some((c) => c.startsWith(n)) ? 0 : r.cities.some((c) => c.split(' ').some((w) => w.startsWith(n))) ? 1 : 2
  return rows
    .filter((r) => terms.every((x) => r.key.includes(x)))
    .map((r) => [score(r), r] as const)
    .sort((a, b) => a[0] - b[0])
    .map(([, r]) => r)
}

const optId = (zone: string, suggested: boolean) => `zp-${zone.replace(/[^A-Za-z0-9]/g, '-')}${suggested ? '-s' : ''}`

interface ZonePickerProps {
  value: string
  macZone: string
  lang: Lang
  now: number
  onChoose: (zone: string) => void
}

export function ZonePicker({ value, macZone, lang, now, onChoose }: ZonePickerProps) {
  const { t } = useTranslation(NS)
  const dir = useDir()
  const [open, setOpen] = useState(false)
  const [phone, setPhone] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.(PHONE_QUERY).matches)
  useEffect(() => {
    const q = window.matchMedia?.(PHONE_QUERY)
    if (!q) return
    const on = () => setPhone(q.matches)
    q.addEventListener('change', on)
    return () => q.removeEventListener('change', on)
  }, [])
  const btnRef = useRef<HTMLButtonElement>(null)
  const city = zoneCity(value, lang)
  const offset = zoneOffset(value, lang, now)

  const choose = (zone: string) => {
    setOpen(false)
    if (zone !== value) onChoose(zone)
    requestAnimationFrame(() => btnRef.current?.focus())
  }

  const button = (
    <button
      ref={btnRef}
      type="button"
      className="gn-zone-btn"
      aria-haspopup="dialog"
      aria-expanded={open}
      data-testid="zone-button"
      onClick={phone ? () => setOpen(true) : undefined}
    >
      <Earth aria-hidden="true" strokeWidth={1.5} />
      <span className="gn-zone-city">{city}</span>
      <span className="gn-zone-off">{offset}</span>
      {/* The accessible name starts with the visible city and offset (axe label-content-name-mismatch). */}
      <span className="sr-only">{t('tzChangeHint')}</span>
      <ChevronsUpDown aria-hidden="true" strokeWidth={1.5} className="gn-zone-caret" />
    </button>
  )

  const body = <PickerBody value={value} macZone={macZone} lang={lang} now={now} onChoose={choose} onEscape={() => setOpen(false)} />

  if (phone) {
    return (
      <>
        {button}
        <Sheet
          open={open}
          onOpenChange={setOpen}
          title={t('zp.title')}
          className="zp-sheet"
          actions={
            <Button variant="secondary" onClick={() => setOpen(false)}>
              {t('zp.close')}
            </Button>
          }
        >
          {body}
        </Sheet>
      </>
    )
  }

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>{button}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          dir={dir}
          align="end"
          side="bottom"
          sideOffset={6}
          collisionPadding={8}
          avoidCollisions
          aria-label={t('zp.title')}
          className="zp zp-pop z-60 vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
          onOpenAutoFocus={(e) => {
            e.preventDefault()
            ;(e.currentTarget as HTMLElement | null)?.querySelector<HTMLInputElement>('input')?.focus()
          }}
        >
          {body}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

function PickerBody({
  value,
  macZone,
  lang,
  now,
  onChoose,
  onEscape,
}: {
  value: string
  macZone: string
  lang: Lang
  now: number
  onChoose: (zone: string) => void
  onEscape: () => void
}) {
  const { t } = useTranslation(NS)
  const [query, setQuery] = useState('')
  const rows = useMemo(() => zoneRows(lang, now), [lang, now])
  const byZone = useMemo(() => new Map(rows.map((r) => [r.zone, r])), [rows])
  const hits = useMemo(() => searchZones(rows, query), [rows, query])
  const searching = query.trim() !== ''

  const suggested: [ZoneRow, string][] = []
  if (!searching) {
    const team = byZone.get(TEAM_TZ)
    if (team) suggested.push([team, t('zp.team')])
    const mac = byZone.get(macZone)
    if (mac && macZone !== TEAM_TZ) suggested.push([mac, t('zp.mac')])
    const cur = byZone.get(value)
    if (cur && value !== TEAM_TZ && value !== macZone) suggested.push([cur, ''])
  }
  const options: { id: string; zone: string }[] = [
    ...suggested.map(([r]) => ({ id: optId(r.zone, true), zone: r.zone })),
    ...hits.map((r) => ({ id: optId(r.zone, false), zone: r.zone })),
  ]
  const initial = !searching ? optId(value, false) : (options[0]?.id ?? null)
  const [active, setActive] = useState<string | null>(initial)
  const listRef = useRef<HTMLUListElement>(null)

  // A new query puts the first match first; opening scrolls to the current zone.
  useEffect(() => {
    setActive(searching ? (options[0]?.id ?? null) : optId(value, false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`#${CSS.escape(optId(value, false))}`)
    el?.scrollIntoView?.({ block: 'center' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const move = (step: number) => {
    if (!options.length) return
    const i = options.findIndex((o) => o.id === active)
    const next = options[(i + step + options.length) % options.length]
    if (!next) return
    setActive(next.id)
    listRef.current?.querySelector<HTMLElement>(`#${CSS.escape(next.id)}`)?.scrollIntoView?.({ block: 'nearest' })
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      move(e.key === 'ArrowDown' ? 1 : -1)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const o = options.find((x) => x.id === active)
      if (o) onChoose(o.zone)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onEscape()
    }
  }

  const option = (r: ZoneRow, suggestedRow: boolean, badge?: string): ReactNode => {
    const id = optId(r.zone, suggestedRow)
    const time = new Intl.DateTimeFormat('en-GB', { timeZone: r.zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(now)
    return (
      <li
        key={id}
        id={id}
        role="option"
        aria-selected={r.zone === value}
        data-zone={r.zone}
        className={`zp-opt${active === id ? ' is-active' : ''}`}
        onPointerMove={() => active !== id && setActive(id)}
        onClick={() => onChoose(r.zone)}
      >
        <span className="zp-check" aria-hidden="true">
          {r.zone === value && <Check strokeWidth={2.25} />}
        </span>
        <span className="zp-main">
          <span className="zp-city" lang={hasLocalCity(r.zone) || lang === 'en' ? undefined : 'en'}>
            {r.city}
          </span>
          <span className="zp-name">{r.name}</span>
        </span>
        {badge ? <span className="zp-badge">{badge}</span> : <span />}
        <span className="zp-time">
          <bdi dir="ltr">{time}</bdi>
        </span>
        <span className="zp-off">{zoneOffset(r.zone, lang, now)}</span>
      </li>
    )
  }

  const count = searching ? hits.length : rows.length
  return (
    <>
      <div className="zp-head">
        <label className="zp-search">
          <Search aria-hidden="true" strokeWidth={1.5} />
          <span className="sr-only">{t('zp.search')}</span>
          <input
            type="search"
            role="combobox"
            aria-expanded="true"
            aria-controls="zp-list"
            aria-autocomplete="list"
            aria-activedescendant={active ?? undefined}
            autoComplete="off"
            spellCheck={false}
            dir="auto"
            placeholder={t('zp.search')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            data-testid="zone-search"
          />
        </label>
      </div>
      <ul className="zp-list" id="zp-list" role="listbox" aria-label={t('zp.title')} ref={listRef}>
        {searching ? (
          hits.length ? (
            hits.map((r) => option(r, false))
          ) : (
            <li className="zp-none" role="presentation">
              <SearchX aria-hidden="true" strokeWidth={1.5} />
              <span>{t('zp.none', { q: query.trim() })}</span>
            </li>
          )
        ) : (
          <>
            <li role="presentation">
              <span className="zp-group-title" id="zp-g1">
                {t('zp.suggested')}
              </span>
              <ul role="group" aria-labelledby="zp-g1">
                {suggested.map(([r, badge]) => option(r, true, badge))}
              </ul>
            </li>
            <li role="presentation">
              <span className="zp-group-title" id="zp-g2">
                {t('zp.all')}
              </span>
              <ul role="group" aria-labelledby="zp-g2">
                {rows.map((r) => option(r, false))}
              </ul>
            </li>
          </>
        )}
      </ul>
      <p className="zp-count" aria-live="polite">
        {t('zp.count', { count, n: count })}
      </p>
    </>
  )
}
