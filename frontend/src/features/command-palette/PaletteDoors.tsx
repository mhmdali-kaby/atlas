import { Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { openPalette, paletteKeys } from './store'
import './palette.css'

/** The sidebar's search capsule (design v22 §6): the palette's visible door, with its keys on it. */
export function PaletteButton() {
  const { t } = useTranslation('command-palette')
  const keys = paletteKeys()
  const label = t('openKeys', { keys })
  return (
    <button
      type="button"
      className="sb-search"
      onClick={openPalette}
      aria-keyshortcuts="Meta+K Control+K"
      aria-label={label}
      title={label}
      data-testid="palette-open"
    >
      <Search aria-hidden="true" />
      <kbd dir="ltr">{keys}</kbd>
    </button>
  )
}
