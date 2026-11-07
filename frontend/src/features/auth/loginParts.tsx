import { Check, Copy } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../../ui/cx'

const COPIED_FEEDBACK_MS = 2000

/** A notice card on the login column: blue info, or red error. */
export function Notice({ tone, icon, title, body }: { tone: 'info' | 'error'; icon?: ReactNode; title: string; body?: string }) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={
        'flex items-start gap-3 rounded-card px-4 py-3.5 text-start shadow-[inset_0_0_0_0.5px_var(--hairline-color)] ' +
        (tone === 'error' ? 'bg-loss-wash' : 'bg-fill-4')
      }
    >
      {icon && (
        <span aria-hidden="true" className={'mt-px shrink-0 [&_svg]:size-5 [&_svg]:stroke-[1.5] ' + (tone === 'error' ? 'text-loss-ink' : 'text-accent')}>
          {icon}
        </span>
      )}
      <div>
        <p className="text-subheadline font-semibold text-label">{title}</p>
        {body && <p className="mt-0.5 text-footnote text-label-secondary">{body}</p>}
      </div>
    </div>
  )
}

/** A Terminal command the owner copies: always left to right, selectable, with a Copy button. */
export function CommandChip({ command, small = false }: { command: string; small?: boolean }) {
  const { t } = useTranslation('auth')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const id = setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS)
    return () => clearTimeout(id)
  }, [copied])

  async function copy() {
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
    } catch {
      // Clipboard blocked: the command stays selectable on screen.
    }
  }

  return (
    <>
      <span
        dir="ltr"
        className={cx(
          'inline-flex items-center gap-2 rounded-[8px] bg-background-secondary ps-3.5 pe-2 font-mono text-label shadow-[inset_0_0_0_0.5px_var(--hairline-color)]',
          small ? 'py-1.5 text-footnote' : 'py-2 text-subheadline',
        )}
      >
        <code className="select-all">{command}</code>
        <button
          type="button"
          onClick={() => void copy()}
          aria-label={copied ? t('first.copied') : t('first.copy')}
          title={copied ? t('first.copied') : t('first.copy')}
          className="grid size-[26px] place-items-center rounded-[6px] text-label-secondary hover:bg-fill-2 hover:text-label focus-visible:shadow-focus"
        >
          {copied ? (
            <Check aria-hidden="true" className="size-4 text-profit-ink" strokeWidth={1.5} />
          ) : (
            <Copy aria-hidden="true" className="size-4" strokeWidth={1.5} />
          )}
        </button>
      </span>
      <p role="status" className="sr-only">
        {copied ? t('first.copied') : ''}
      </p>
    </>
  )
}
