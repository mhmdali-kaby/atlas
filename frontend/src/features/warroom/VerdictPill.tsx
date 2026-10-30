import { CircleCheck, CircleMinus, CircleX, Scale, type LucideProps } from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { isSmallSampleRule, type Correction, type Verdict } from './historian'

/* The HistoryVerdict card's verdict pill and the note on what a verdict rule changed. */

/** Lucide has no dashed check circle: its circle-dashed with circle-check's tick. */
function CircleCheckDashed(props: LucideProps) {
  return (
    <svg viewBox="0 0 24 24" width={24} height={24} fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M10.1 2.182a10 10 0 0 1 3.8 0" />
      <path d="M13.9 21.818a10 10 0 0 1-3.8 0" />
      <path d="M17.609 3.721a10 10 0 0 1 2.69 2.7" />
      <path d="M2.182 13.9a10 10 0 0 1 0-3.8" />
      <path d="M20.279 17.609a10 10 0 0 1-2.7 2.69" />
      <path d="M21.818 10.1a10 10 0 0 1 0 3.8" />
      <path d="M3.721 6.391a10 10 0 0 1 2.7-2.69" />
      <path d="M6.391 20.279a10 10 0 0 1-2.69-2.7" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  )
}

const VERDICT_ICON: Record<Verdict, (p: LucideProps) => ReactNode> = {
  supports: (p) => <CircleCheck {...p} />,
  weakly_supports: (p) => <CircleCheckDashed {...p} />,
  does_not_support: (p) => <CircleMinus {...p} />,
  contradicts: (p) => <CircleX {...p} />,
}

export function VerdictPill({ verdict }: { verdict: Verdict }) {
  const { t } = useTranslation('warroom')
  return (
    <span className={`verdict v-${verdict}`} data-testid="hv-verdict" data-verdict={verdict}>
      <span className="sr-only">{t('hv.verdictSr')} </span>
      {VERDICT_ICON[verdict]({ 'aria-hidden': true, strokeWidth: 1.75 })}
      <span>{t(`hv.v.${verdict}`)}</span>
    </span>
  )
}

/**
 * What a verdict rule changed (card meta verdict_corrections, ATL-93/ATL-99): a small-sample cap
 * to weakly_supports reads "Capped from Supports: 12 cases", a gapped backtest "Lowered from …",
 * and anything else — contradicts → does_not_support on a small sample, or a rule this build does
 * not know — the generic "Changed from Contradicts: 5 cases".
 */
export function CorrectionNote({ c, n }: { c: Correction; n: number }) {
  const { t } = useTranslation('warroom')
  const from = t(`hv.v.${c.from}`)
  const cases = t('hv.cases', { count: c.n ?? n })
  const small = isSmallSampleRule(c.rule)
  const [text, tip] =
    small && c.to === 'weakly_supports'
      ? [t('hv.corr.capped', { from, cases }), t('hv.corr.cappedTip')]
      : c.rule === 'backtest_on_gapped_data'
        ? [t('hv.corr.gapped', { from }), t('hv.corr.gappedTip')]
        : [t('hv.corr.changed', { from, cases }), t(small ? 'hv.corr.changedTip' : 'hv.corr.changedOtherTip')]
  return (
    <span className="hv-corr" tabIndex={0} title={tip} data-testid="hv-corr" data-rule={c.rule}>
      <Scale aria-hidden="true" strokeWidth={1.5} />
      <span>{text}</span>
    </span>
  )
}
