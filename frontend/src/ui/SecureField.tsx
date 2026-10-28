import { Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { TextField, type TextFieldProps } from './TextField'

export type SecureFieldProps = Omit<TextFieldProps, 'type' | 'endSlot'>

/**
 * A masked field with a show/hide button at the inline end (design §9). Keys
 * and secrets are LTR strings, so in Arabic the input, its padding and the
 * button stay LTR; the label above still follows the page.
 */
export function SecureField({ disabled, className, ...props }: SecureFieldProps) {
  const { t } = useTranslation('ui')
  const [revealed, setRevealed] = useState(false)

  return (
    <TextField
      {...props}
      className={className}
      disabled={disabled}
      type={revealed ? 'text' : 'password'}
      dir="ltr"
      autoComplete={props.autoComplete ?? 'off'}
      spellCheck={false}
      autoCapitalize="off"
      inputClassName="font-mono text-subheadline tracking-[0.02em]"
      endSlot={
        <button
          type="button"
          disabled={disabled}
          aria-pressed={revealed}
          aria-label={revealed ? t('secure.hide') : t('secure.show')}
          onClick={() => setRevealed((v) => !v)}
          className="grid size-8 place-items-center rounded-[8px] text-label-secondary hover:bg-fill-3 hover:text-label disabled:opacity-40"
        >
          {revealed ? (
            <EyeOff aria-hidden="true" className="size-4" strokeWidth={1.5} />
          ) : (
            <Eye aria-hidden="true" className="size-4" strokeWidth={1.5} />
          )}
        </button>
      }
    />
  )
}
