import { ChevronRight, Lock } from 'lucide-react'
import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatFixed, type Lang } from '../../lib/format'
import { cx } from '../../ui/cx'
import { useDir } from '../../ui/useDir'
import { AUTONOMY_LEVELS, DEFAULT_AUTONOMY, DEFAULT_PROFILE, ENABLED_AUTONOMY, PRESET_IDS, type PresetId, type RiskLimits, type RiskProfile } from './settings'
import './pickers.css'

/** The ten limits in display order (design data.js limitDefs): unit and decimals per field. */
export const LIMIT_DEFS = [
  { field: 'max_loss_per_trade_pct', key: 'lossPerTrade', unit: '%', digits: 1 },
  { field: 'max_position_pct', key: 'positionSize', unit: '%', digits: 0 },
  { field: 'max_daily_loss_pct', key: 'dailyLoss', unit: '%', digits: 1 },
  { field: 'max_weekly_loss_pct', key: 'weeklyLoss', unit: '%', digits: 0 },
  { field: 'max_leverage', key: 'leverage', unit: '×', digits: 0 },
  { field: 'max_open_positions', key: 'openPositions', unit: '', digits: 0 },
  { field: 'max_share_per_instrument_pct', key: 'instrumentShare', unit: '%', digits: 0 },
  { field: 'max_correlated_exposure_pct', key: 'correlated', unit: '%', digits: 0 },
  { field: 'min_expected_r', key: 'minR', unit: ' R', digits: 1 },
  { field: 'max_consecutive_losses', key: 'lossStreak', unit: '', digits: 0 },
] as const satisfies readonly { field: keyof RiskLimits; key: string; unit: string; digits: number }[]

/** The four figures each profile card shows (design forms.js keyFigures). */
const KEY_FIGURES = ['max_loss_per_trade_pct', 'max_position_pct', 'max_daily_loss_pct', 'max_leverage'] as const

function useLang(): Lang {
  const { i18n } = useTranslation()
  return i18n.language === 'ar' ? 'ar' : 'en'
}

/** "1.0%", "5×", "1.8 R": the number and its unit, kept in visual order in RTL. */
export function LimitValue({ field, value }: { field: keyof RiskLimits; value: string | number | undefined }) {
  const lang = useLang()
  const def = LIMIT_DEFS.find((d) => d.field === field)
  if (!def) return null
  return (
    <span className="ob-num">
      {formatFixed(value, def.digits, lang)}
      {def.unit}
    </span>
  )
}

/**
 * Radio-card keyboard model (design forms.js mountRadios): one tab stop on the checked card,
 * arrows move and select (Left/Right flip in RTL), disabled cards are skipped.
 */
function useRadioGroup<T extends string | number>(options: readonly T[], value: T, onChange: (v: T) => void, isDisabled: (v: T) => boolean) {
  const refs = useRef(new Map<T, HTMLButtonElement>())
  const dir = useDir()
  const enabled = options.filter((o) => !isDisabled(o))

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, current: T) => {
    const forwardKeys = ['ArrowDown', dir === 'rtl' ? 'ArrowLeft' : 'ArrowRight']
    const backKeys = ['ArrowUp', dir === 'rtl' ? 'ArrowRight' : 'ArrowLeft']
    let step = 0
    if (forwardKeys.includes(event.key)) step = 1
    else if (backKeys.includes(event.key)) step = -1
    else if (event.key === 'Home') step = -Infinity
    else if (event.key === 'End') step = Infinity
    if (step === 0 || enabled.length === 0) return
    event.preventDefault()
    const i = Math.max(0, enabled.indexOf(current))
    const next =
      step === -Infinity ? enabled[0] : step === Infinity ? enabled[enabled.length - 1] : enabled[(i + step + enabled.length) % enabled.length]
    if (next === undefined) return
    onChange(next)
    refs.current.get(next)?.focus()
  }

  const itemProps = (option: T) => {
    const disabled = isDisabled(option)
    const checked = option === value
    return {
      ref: (el: HTMLButtonElement | null) => {
        if (el) refs.current.set(option, el)
        else refs.current.delete(option)
      },
      type: 'button' as const,
      role: 'radio',
      'aria-checked': checked,
      'aria-disabled': disabled || undefined,
      // The checked card is the group's tab stop; with none checked, the first enabled one.
      tabIndex: checked || (!options.includes(value) && option === enabled[0]) ? 0 : -1,
      onClick: () => {
        if (!disabled) onChange(option)
      },
      onKeyDown: (e: KeyboardEvent<HTMLButtonElement>) => onKeyDown(e, option),
    }
  }
  return itemProps
}

export interface RiskProfilePickerProps {
  /** The selected profile; "custom" only when `customValues` is given. */
  value: RiskProfile
  onChange: (profile: RiskProfile) => void
  /** GET /api/settings `presets`. */
  presets: Record<PresetId, RiskLimits>
  /** Adds a fourth "Custom" card showing these values (Settings → Risk, ATL-21). */
  customValues?: RiskLimits
  className?: string
}

/**
 * Design forms.js profileCards: Conservative / Balanced / Aggressive (+ Custom) as radio cards,
 * each with its max loss per trade, max position, daily loss and max leverage from the API.
 */
export function RiskProfilePicker({ value, onChange, presets, customValues, className }: RiskProfilePickerProps) {
  const { t } = useTranslation('onboarding')
  const list: RiskProfile[] = customValues ? [...PRESET_IDS, 'custom'] : [...PRESET_IDS]
  const item = useRadioGroup<RiskProfile>(list, value, onChange, () => false)
  const uid = useId()
  return (
    <div role="radiogroup" aria-label={t('rp.label')} className={cx('ob-profile-cards', customValues && 'has-custom', className)}>
      {list.map((profile) => {
        const values = profile === 'custom' ? customValues : presets[profile]
        const titleId = `${uid}-${profile}-title`
        const textId = `${uid}-${profile}-text`
        return (
          <button key={profile} {...item(profile)} aria-labelledby={titleId} aria-describedby={textId} data-value={profile} className="ob-choice ob-profile-card">
            <span className="ob-pc-head">
              <span aria-hidden="true" className="ob-choice-radio" />
              <span id={titleId} className="ob-choice-title">
                {t(`rp.${profile}`)}
              </span>
              {profile === DEFAULT_PROFILE && <span className="ob-tag">{t('rp.default')}</span>}
            </span>
            <span id={textId} className="ob-choice-text">
              {t(`rp.${profile}Text`)}
            </span>
            <span className="ob-pc-figures">
              {KEY_FIGURES.map((field) => {
                const def = LIMIT_DEFS.find((d) => d.field === field)
                return (
                  <span key={field} className="ob-pc-fig" data-field={field}>
                    <span className="ob-pc-fig-label">{t(`lim.${def?.key ?? field}`)}</span>
                    <span className="ob-pc-fig-value">
                      <LimitValue field={field} value={values?.[field]} />
                    </span>
                  </span>
                )
              })}
            </span>
          </button>
        )
      })}
    </div>
  )
}

/** Design forms.js limitsCompare: all ten limits side by side, the selected column washed. */
export function LimitsCompare({ presets, selected }: { presets: Record<PresetId, RiskLimits>; selected: RiskProfile }) {
  const { t } = useTranslation('onboarding')
  return (
    <div className="ob-compare-wrap">
      <table className="ob-compare">
        <thead>
          <tr>
            <th scope="col">{t('rp.label')}</th>
            {PRESET_IDS.map((p) => (
              <th key={p} scope="col" className={cx('is-num', p === selected && 'is-on')}>
                {t(`rp.${p}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {LIMIT_DEFS.map((def) => (
            <tr key={def.field}>
              <th scope="row">{t(`lim.${def.key}`)}</th>
              {PRESET_IDS.map((p) => (
                <td key={p} className={cx('is-num', p === selected && 'is-on')}>
                  <LimitValue field={def.field} value={presets[p][def.field]} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The "Compare all ten limits" disclosure under the cards. */
export function CompareDisclosure({ children }: { children: ReactNode }) {
  const { t } = useTranslation('onboarding')
  const [open, setOpen] = useState(false)
  const id = useId()
  return (
    <div className="grid justify-items-start gap-2.5">
      <button type="button" className="ob-disclosure" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        <ChevronRight aria-hidden="true" strokeWidth={2} />
        <span>{t(open ? 'rp.compareHide' : 'rp.compare')}</span>
      </button>
      <div id={id} hidden={!open} className="w-full">
        {open && children}
      </div>
    </div>
  )
}

export interface AutonomyPickerProps {
  value: number
  onChange: (level: number) => void
  /** Levels the owner may pick; the rest show "Available in a later phase". Phase 0: 1 and 2. */
  enabled?: readonly number[]
}

/** Design forms.js autonomyList: levels 1–4, each explained; later-phase levels disabled. */
export function AutonomyPicker({ value, onChange, enabled = ENABLED_AUTONOMY }: AutonomyPickerProps) {
  const { t } = useTranslation('onboarding')
  const lang = useLang()
  const isDisabled = (n: number) => !enabled.includes(n)
  const item = useRadioGroup<number>(AUTONOMY_LEVELS, value, onChange, isDisabled)
  const uid = useId()
  return (
    <div role="radiogroup" aria-label={t('au.label')} className="ob-autonomy-list">
      {AUTONOMY_LEVELS.map((n) => (
        <button
          key={n}
          {...item(n)}
          aria-labelledby={`${uid}-${n}-title`}
          aria-describedby={`${uid}-${n}-text${isDisabled(n) ? ` ${uid}-${n}-later` : ''}`}
          data-value={n}
          className="ob-choice ob-autonomy-row"
        >
          <span aria-hidden="true" className="ob-au-level ob-num">
            {formatFixed(n, 0, lang)}
          </span>
          <span className="ob-au-main">
            <span className="ob-choice-title">
              <span id={`${uid}-${n}-title`}>{t(`au.l${n}`)}</span>
              {n === DEFAULT_AUTONOMY && <span className="ob-tag">{t('rp.default')}</span>}
            </span>
            <span id={`${uid}-${n}-text`} className="ob-choice-text">
              {t(`au.l${n}Text`)}
            </span>
            {isDisabled(n) && (
              <span id={`${uid}-${n}-later`} className="ob-au-later">
                <Lock aria-hidden="true" strokeWidth={1.5} />
                {t('laterPhase')}
              </span>
            )}
          </span>
          <span aria-hidden="true" className="ob-choice-radio" />
        </button>
      ))}
    </div>
  )
}
