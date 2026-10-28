import { render, screen } from '@testing-library/react'
import i18n from '../i18n'
import { EnvFooter } from './AppShell'

describe('risk footer', () => {
  it('states the environment in English', () => {
    render(<EnvFooter label={i18n.t('footer', { env: i18n.t('env.demo') })} />)
    expect(screen.getByRole('contentinfo')).toHaveTextContent('Automated trading can lose money. Demo/Live: Demo')
  })

  it('switches the document to RTL for Arabic', async () => {
    await i18n.changeLanguage('ar')
    expect(document.documentElement.dir).toBe('rtl')
    await i18n.changeLanguage('en')
    expect(document.documentElement.dir).toBe('ltr')
  })
})
