import { Check, ChevronDown, Search, X } from 'lucide-react'
import { DropdownMenu } from 'radix-ui'
import { Fragment, useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useTradingEnv } from '../../app/shell/tradingEnv'
import { Button, SegmentedControl, Sheet, SheetClose } from '../../ui'
import { cx } from '../../ui/cx'
import { useDir } from '../../ui/useDir'
import { formatDay } from '../portfolio/time'
import { Medallion } from '../warroom/Medallion'
import { baghdadDay } from '../warroom/time'
import { useInstrumentSearch } from '../warroom/watchlist'
import { BLANK, type JournalFilters, type Outcome, type RangePreset } from './api'
import { NAMEABLE, useTagLabel } from './format'
import { TAG_GROUPS, TAG_VALUES } from './model'
import type { Lang } from '../../lib/format'

/*
 * The Journal's filter bar (design §7 "Filters"): Dates, the instrument, the outcome, the agents the
 * post-mortem names and a tag grouped Setup / Market / Regime / Catalyst; then removable chips with
 * Clear filters. Every filter lives in the URL (JournalPage), so a reload restores it.
 */

const menuItem =
  'group flex min-h-[30px] w-full cursor-default items-center gap-2.5 rounded-[7px] px-2.5 py-1 text-start text-subheadline text-label outline-none select-none data-highlighted:bg-accent-solid data-highlighted:text-on-solid'
const menuLabel = 'px-2.5 pt-1.5 pb-1 text-footnote font-semibold text-label-secondary'
const menuSep = 'mx-2.5 my-[5px] h-[0.5px] bg-separator'

interface Option {
  value: string
  label: string
  icon?: ReactNode
}

function Item({ o }: { o: Option }) {
  return (
    <DropdownMenu.RadioItem value={o.value} className={menuItem} data-value={o.value}>
      {o.icon}
      <span>{o.label}</span>
      <DropdownMenu.ItemIndicator className="ms-auto">
        <Check aria-hidden="true" strokeWidth={2} className="size-4 text-accent group-data-highlighted:text-on-solid" />
      </DropdownMenu.ItemIndicator>
    </DropdownMenu.RadioItem>
  )
}

/** A filter button with a radio menu (accent wash while set), options optionally in labelled groups. */
function FilterMenu({
  testId,
  heading,
  label,
  value,
  all,
  groups,
  note,
  onChange,
  disabled,
  extra,
}: {
  testId: string
  heading: string
  label: string
  value: string
  all: Option
  groups: Array<{ label?: string; options: Option[] }>
  note?: string
  onChange: (v: string) => void
  disabled?: boolean
  extra?: ReactNode
}) {
  const dir = useDir()
  return (
    <DropdownMenu.Root dir={dir} modal={false}>
      <DropdownMenu.Trigger asChild disabled={disabled}>
        <button type="button" className={cx('ph-menu jr-menu', value !== all.value && 'is-set')} data-testid={testId} aria-label={`${heading}: ${label}`}>
          <span>{label}</span>
          <ChevronDown aria-hidden="true" strokeWidth={2} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={6}
          className="shell-popover z-60 max-h-[min(60vh,460px)] min-w-[220px] overflow-auto rounded-card p-[5px] vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
        >
          <DropdownMenu.Label className={menuLabel}>{note ?? heading}</DropdownMenu.Label>
          <DropdownMenu.RadioGroup value={value} onValueChange={onChange}>
            <Item o={all} />
            {groups.map((g, i) => (
              <Fragment key={g.label ?? i}>
                <DropdownMenu.Separator className={menuSep} />
                {g.label && <DropdownMenu.Label className={menuLabel}>{g.label}</DropdownMenu.Label>}
                {g.options.map((o) => (
                  <Item key={o.value} o={o} />
                ))}
              </Fragment>
            ))}
          </DropdownMenu.RadioGroup>
          {extra}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

/** The exact-instId field: suggestions come from ATL-119's instrument search. */
function InstrumentField({ value, onApply, disabled }: { value: string; onApply: (v: string) => void; disabled?: boolean }) {
  const { t, i18n } = useTranslation('journal')
  const env = useTradingEnv()
  const [draft, setDraft] = useState(value)
  const [last, setLast] = useState(value)
  if (value !== last) {
    setLast(value)
    setDraft(value)
  }
  const listId = useId()
  const q = draft.trim().toUpperCase()
  const hits = useInstrumentSearch(env, q.length >= 2 && q !== value ? q : '')
  const ids = hits.data?.data.map((h) => h.inst_id) ?? []
  // The placeholder mixes Arabic with a ticker: isolate it so its words keep their order (design §7).
  const ph = i18n.language === 'ar' ? `⁧${t('f.instPh')}⁩` : t('f.instPh')
  return (
    <label className={cx('ph-search jr-inst-field', value && 'is-set')}>
      <Search aria-hidden="true" strokeWidth={2} />
      <span className="sr-only">{t('f.inst')}</span>
      <input
        className="ph-search-input jr-inst-input"
        type="search"
        data-testid="filter-inst"
        value={draft}
        list={listId}
        placeholder={ph}
        autoComplete="off"
        spellCheck={false}
        dir="ltr"
        disabled={disabled}
        onChange={(e) => {
          const v = e.target.value
          setDraft(v)
          if (v.trim() === '') onApply('')
          else if (ids.includes(v.trim().toUpperCase())) onApply(v.trim().toUpperCase())
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            onApply(draft.trim().toUpperCase())
          }
        }}
        onBlur={() => {
          if (draft.trim().toUpperCase() !== value) onApply(draft.trim().toUpperCase())
        }}
      />
      <datalist id={listId}>
        {ids.map((id) => (
          <option key={id} value={id} />
        ))}
      </datalist>
    </label>
  )
}

/** Custom dates: two Baghdad days, inclusive, from ≤ to. */
function RangeSheet({ initial, max, onApply, onClose }: { initial: { from: string; to: string }; max: string; onApply: (from: string, to: string) => void; onClose: () => void }) {
  const { t } = useTranslation('journal')
  const [from, setFrom] = useState(initial.from)
  const [to, setTo] = useState(initial.to)
  const [bad, setBad] = useState(false)
  const errId = useId()
  const apply = () => {
    if (!from || !to || from > to) {
      setBad(true)
      return
    }
    onApply(from, to)
  }
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={t('r.sheetTitle')}
      description={t('r.sheetText')}
      actions={
        <>
          <SheetClose>
            <Button variant="secondary">{t('r.cancel')}</Button>
          </SheetClose>
          <Button variant="primary" onClick={apply} data-testid="range-apply">
            {t('r.apply')}
          </Button>
        </>
      }
    >
      <div className="ph-range-fields">
        {(
          [
            ['from', from, setFrom],
            ['to', to, setTo],
          ] as const
        ).map(([k, v, setV]) => (
          <label key={k} className="grid gap-1.5">
            <span className="text-footnote font-medium text-label-secondary">{t(`r.${k}`)}</span>
            <input
              className="ph-date"
              type="date"
              dir="ltr"
              value={v}
              max={max}
              data-testid={`range-${k}`}
              aria-invalid={bad || undefined}
              aria-describedby={bad ? errId : undefined}
              onChange={(e) => {
                setV(e.target.value)
                setBad(false)
              }}
            />
          </label>
        ))}
      </div>
      {bad && (
        <p id={errId} role="alert" className="mt-3 text-footnote text-loss-ink">
          {t('r.bad')}
        </p>
      )}
    </Sheet>
  )
}

export function JournalFilterBar({
  f,
  onChange,
  now,
  lang,
  disabled,
}: {
  f: JournalFilters
  onChange: (next: JournalFilters) => void
  now: number
  lang: Lang
  disabled?: boolean
}) {
  const { t } = useTranslation(['journal', 'warroom'])
  const tagLabel = useTagLabel()
  const [rangeOpen, setRangeOpen] = useState(false)
  const patch = (p: Partial<JournalFilters>) => onChange({ ...f, ...p })
  const fmtDay = (d: string) => formatDay(`${d}T09:00:00Z`, lang)
  const rangeLabel = f.range === 'custom' ? t('f.span', { from: fmtDay(f.from), to: fmtDay(f.to) }) : f.range === 'all' ? t('f.dates') : t(`f.${f.range}`)
  const agentName = (a: string) => t(`warroom:agents.${a}.name`, { defaultValue: a })

  const chips: Array<[keyof JournalFilters, string]> = []
  if (f.range !== 'all') chips.push(['range', rangeLabel])
  if (f.inst) chips.push(['inst', f.inst])
  if (f.outcome !== 'all') chips.push(['outcome', t(`f.${f.outcome}`)])
  if (f.agent !== 'all') chips.push(['agent', agentName(f.agent)])
  if (f.tag !== 'all') chips.push(['tag', tagLabel(f.tag)])
  const clearOne = (k: keyof JournalFilters) => patch(k === 'range' ? { range: 'all', from: '', to: '' } : { [k]: BLANK[k] })

  return (
    <>
      <div className="jr-filters" role="group" aria-label={t('filters')} inert={disabled || undefined}>
        <FilterMenu
          testId="filter-range"
          heading={t('f.dates')}
          label={rangeLabel}
          value={f.range}
          all={{ value: 'all', label: t('f.all') }}
          groups={[{ options: (['7d', '30d'] as const).map((v) => ({ value: v, label: t(`f.${v}`) })) }]}
          onChange={(v) => {
            if (v === 'custom') setRangeOpen(true)
            else patch({ range: v as RangePreset, from: '', to: '' })
          }}
          extra={
            <>
              <DropdownMenu.Separator className={menuSep} />
              <DropdownMenu.Item className={menuItem} onSelect={() => setRangeOpen(true)} data-testid="range-custom">
                <span>{t('f.custom')}</span>
                {f.range === 'custom' && <Check aria-hidden="true" strokeWidth={2} className="ms-auto size-4 text-accent group-data-highlighted:text-on-solid" />}
              </DropdownMenu.Item>
            </>
          }
        />
        <InstrumentField value={f.inst} onApply={(inst) => patch({ inst })} disabled={disabled} />
        <SegmentedControl<Outcome | 'all'>
          aria-label={t('f.outcome')}
          className="jr-outcome"
          value={f.outcome}
          onValueChange={(outcome) => patch({ outcome })}
          options={(['all', 'win', 'loss', 'scratch'] as const).map((v) => ({ value: v, label: t(v === 'all' ? 'f.any' : `f.${v}`) }))}
        />
        <FilterMenu
          testId="filter-agent"
          heading={t('f.agent')}
          note={t('f.agentNote')}
          label={f.agent === 'all' ? t('f.agent') : agentName(f.agent)}
          value={f.agent}
          all={{ value: 'all', label: t('f.agentAll') }}
          groups={[{ options: NAMEABLE.map((a) => ({ value: a, label: agentName(a), icon: <Medallion slug={a} size={22} /> })) }]}
          onChange={(agent) => patch({ agent })}
        />
        <FilterMenu
          testId="filter-tag"
          heading={t('f.tag')}
          label={f.tag === 'all' ? t('f.tag') : tagLabel(f.tag)}
          value={f.tag}
          all={{ value: 'all', label: t('f.tagAll') }}
          groups={TAG_GROUPS.map((g) => ({ label: t(`tg.${g}`), options: TAG_VALUES[g].map((v) => ({ value: `${g}:${v}`, label: tagLabel(`${g}:${v}`) })) }))}
          onChange={(tag) => patch({ tag })}
        />
      </div>
      {chips.length > 0 && (
        <div className="ph-chips jr-chips" data-testid="journal-chips">
          {chips.map(([k, label]) => (
            <span key={k} className="ph-chip">
              <span dir={k === 'inst' ? 'ltr' : undefined}>{label}</span>
              <button type="button" className="ph-chip-x" aria-label={t('f.remove', { label })} onClick={() => clearOne(k)}>
                <X aria-hidden="true" />
              </button>
            </span>
          ))}
          <Button size="sm" variant="plain" onClick={() => onChange(BLANK)}>
            {t('f.clear')}
          </Button>
        </div>
      )}
      {rangeOpen && (
        <RangeSheet
          initial={{ from: f.from || baghdadDay(now - 6 * 86_400_000), to: f.to || baghdadDay(now) }}
          max={baghdadDay(now)}
          onClose={() => setRangeOpen(false)}
          onApply={(from, to) => {
            setRangeOpen(false)
            patch({ range: 'custom', from, to })
          }}
        />
      )}
    </>
  )
}
