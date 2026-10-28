import { CircleAlert, CircleHelp, Minus, Plus } from 'lucide-react'
import { Tooltip } from 'radix-ui'
import { useTranslation } from 'react-i18next'
import { formatDecimal } from '../../lib/format'
import { cx } from '../../ui/cx'
import { useLang } from '../portfolio/hooks'
import { LimitValue, type PresetId, type RiskLimits } from '../onboarding'
import { parseLimit, serverBounds, STEPPER, type LimitDef } from './model'

/** "0%", "125×", "20 R" as one isolated run, so the unit stays on its number inside Arabic text. */
function unitText(def: LimitDef, value: string): string {
  return `⁦${value}${def.unit}⁩`
}

/** The message under a field: the empty-field check, or the server's 422 in the owner's language. */
function useLimitError(def: LimitDef, text: string, serverError: string | undefined): string | null {
  const { t } = useTranslation('settings')
  const { t: tOb } = useTranslation('onboarding')
  const lang = useLang()
  const label = tOb(`lim.${def.key}`)
  if (parseLimit(text) === null) return t('risk.err.empty', { label })
  if (!serverError) return null
  const bounds = serverBounds(serverError)
  if (!bounds) return t('risk.err.other', { label })
  const min = unitText(def, formatDecimal(bounds.min, lang, 0))
  const max = unitText(def, formatDecimal(bounds.max, lang, 0))
  return t(bounds.kind === 'exclusive' ? 'risk.err.range' : 'risk.err.between', { label, min, max })
}

export interface LimitRowProps {
  def: LimitDef
  text: string
  base: PresetId
  presets: Record<PresetId, RiskLimits>
  serverError?: string
  onChange: (text: string) => void
  onStep: (dir: 1 | -1) => void
  onBlur: () => void
}

/** Design settings.js limitRow: label + help, "Balanced: 5×" when changed, the stepper, the error. */
export function LimitRow({ def, text, base, presets, serverError, onChange, onStep, onBlur }: LimitRowProps) {
  const { t } = useTranslation('settings')
  const { t: tOb } = useTranslation('onboarding')
  const label = tOb(`lim.${def.key}`)
  const error = useLimitError(def, text, serverError)
  const value = parseLimit(text)
  const bounds = STEPPER[def.field]
  const presetValue = presets[base][def.field]
  const changed = value !== null && value !== Number(presetValue)
  const id = `lim-${def.key}`

  return (
    <div className="st-limit-row" data-limit={def.field}>
      <div className="st-limit-main">
        <span className="st-limit-label">
          <label htmlFor={id} className="text-callout">
            {label}
          </label>
          <Tooltip.Root>
            <Tooltip.Trigger asChild>
              <button type="button" className="st-limit-help" aria-label={t('risk.about', { label })}>
                <CircleHelp aria-hidden="true" strokeWidth={1.5} />
              </button>
            </Tooltip.Trigger>
            <Tooltip.Portal>
              <Tooltip.Content
                side="top"
                sideOffset={6}
                className="z-70 max-w-[280px] rounded-card px-3 py-1.5 text-footnote text-label vibrancy-popover shadow-[var(--ui-shadow-popover),0_0_0_0.5px_var(--hairline-color)]"
              >
                {t(`risk.help.${def.key}`)}
              </Tooltip.Content>
            </Tooltip.Portal>
          </Tooltip.Root>
        </span>
        <span id={`${id}-meta`} className="st-limit-meta text-footnote">
          {changed && (
            <>
              {tOb(`rp.${base}`)}: <LimitValue field={def.field} value={presetValue} />
            </>
          )}
        </span>
        {error && (
          <p id={`${id}-err`} className="st-field-error text-footnote" data-testid={`error-${def.field}`}>
            <CircleAlert aria-hidden="true" strokeWidth={1.75} />
            <span>{error}</span>
          </p>
        )}
      </div>
      <div className={cx('st-stepper', error && 'is-error')}>
        <div className="st-stepper-field" dir="ltr">
          <input
            id={id}
            className={cx('st-stepper-input', !def.unit && 'no-unit')}
            inputMode="decimal"
            autoComplete="off"
            spellCheck={false}
            value={text}
            aria-invalid={error ? true : undefined}
            aria-describedby={`${id}-meta${error ? ` ${id}-err` : ''}`}
            onChange={(e) => onChange(e.target.value)}
            onBlur={onBlur}
            onKeyDown={(e) => {
              if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                e.preventDefault()
                onStep(e.key === 'ArrowUp' ? 1 : -1)
              }
            }}
          />
          {def.unit && (
            <span aria-hidden="true" className="st-stepper-unit">
              {def.unit.trim()}
            </span>
          )}
        </div>
        <div className="st-stepper-buttons">
          <button type="button" aria-label={t('risk.decrease', { label })} disabled={value !== null && value <= bounds.min} onClick={() => onStep(-1)}>
            <Minus aria-hidden="true" strokeWidth={2} />
          </button>
          <span className="st-stepper-sep" aria-hidden="true" />
          <button type="button" aria-label={t('risk.increase', { label })} disabled={value !== null && value >= bounds.max} onClick={() => onStep(1)}>
            <Plus aria-hidden="true" strokeWidth={2} />
          </button>
        </div>
      </div>
    </div>
  )
}
