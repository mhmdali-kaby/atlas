import { render, screen } from '@testing-library/react'
import { useTranslation } from 'react-i18next'
import i18n from './index'

/*
 * ATL-193: a locale namespace added after a checkout has already run Vitest
 * once must still be picked up on the next run, with nothing cleared by
 * hand. This namespace (en/ar `zz-probe.json`) exists only to prove that:
 * if Vitest's fs module cache ever serves a stale `import.meta.glob` result
 * for `src/i18n/index.ts` again, this is the first test to fail, rendering
 * the raw key instead of the translated text.
 */

function Probe() {
  const { t } = useTranslation('zz-probe')
  return <p>{t('title')}</p>
}

afterEach(async () => {
  await i18n.changeLanguage('en')
})

describe('a locale namespace added after the fs module cache was warm', () => {
  it('is picked up in English without clearing any cache', () => {
    render(<Probe />)
    expect(screen.getByText('Probe English')).toBeInTheDocument()
  })

  it('is picked up in Arabic without clearing any cache', async () => {
    await i18n.changeLanguage('ar')
    render(<Probe />)
    expect(screen.getByText('مسبار عربي')).toBeInTheDocument()
  })
})
