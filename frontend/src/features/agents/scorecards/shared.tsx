import type { ReactElement } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { Fig } from '../parts'
import { SC_NS } from './i18n'

/** A translated sentence whose <f> become LTR figure islands. */
export function T({ k, values, f }: { k: string; values?: Record<string, unknown>; f?: ReactElement }) {
  const { t } = useTranslation(SC_NS)
  return <Trans t={t} i18nKey={k} values={values} components={{ f: f ?? <Fig />, cmd: <code dir="ltr" className="cmd" /> }} />
}
