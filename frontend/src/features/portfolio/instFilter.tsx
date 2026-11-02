import { ListFilter, X } from 'lucide-react'
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router'
import { InstId } from './parts'

/*
 * `?inst_id=` on Positions and Orders (design v18 "Autonomy and notifications" §5.6, ATL-220): a
 * notification link ("stop hit on NVDA-USDT-SWAP" → /portfolio/positions?inst_id=NVDA-USDT-SWAP)
 * filters every table on the page to that one instrument. The filter lives in the URL itself (a
 * reload keeps it), and a 30px removable chip clears it back to every row.
 */

/** The `?inst_id=` filter, read straight from the URL so a reload keeps it. */
export function useInstFilterParam(): [string | null, () => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.get('inst_id')
  const instId = raw && raw.trim() !== '' ? raw.trim().toUpperCase() : null
  const clear = useCallback(() => {
    setParams(
      (p) => {
        const next = new URLSearchParams(p)
        next.delete('inst_id')
        return next
      },
      { replace: true },
    )
  }, [setParams])
  return [instId, clear]
}

export function InstFilterChip({ instId, shown, total, onClear }: { instId: string; shown: number; total: number; onClear: () => void }) {
  const { t } = useTranslation('portfolio')
  return (
    <div className="pf-inst-filter" data-testid="inst-filter">
      <span className="pf-inst-chip">
        <ListFilter aria-hidden="true" strokeWidth={1.75} className="pf-inst-chip-icon" />
        <InstId id={instId} />
        <button
          type="button"
          className="pf-inst-chip-x"
          aria-label={t('filter.remove', { inst: instId })}
          title={t('filter.removeTip')}
          onClick={onClear}
          data-testid="inst-filter-remove"
        >
          <X aria-hidden="true" />
        </button>
      </span>
      <span className="pf-inst-count">{t('filter.showing', { n: shown, total })}</span>
    </div>
  )
}
