import { render, screen } from '@testing-library/react'
import { act } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '../../i18n'
import { OkxAccountLink } from './OkxAccountLink'

const env = vi.hoisted(() => ({ value: 'demo' as 'demo' | 'live' }))
vi.mock('./tradingEnv', () => ({ useTradingEnv: () => env.value }))

afterEach(async () => {
  env.value = 'demo'
  await act(() => i18n.changeLanguage('en'))
})

describe('OkxAccountLink', () => {
  it('opens the OKX demo assets page in a new tab while Atlas trades demo', () => {
    render(<OkxAccountLink />)
    const link = screen.getByRole('link', { name: 'Open OKX demo account' })
    expect(link).toHaveAttribute('href', 'https://www.okx.com/balance/assets')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(link).toHaveAttribute('title', expect.stringContaining('Demo trading'))
  })

  it('opens the OKX live overview while Atlas trades live', () => {
    env.value = 'live'
    render(<OkxAccountLink />)
    const link = screen.getByRole('link', { name: 'Open OKX live account' })
    expect(link).toHaveAttribute('href', 'https://www.okx.com/balance/overview')
  })

  it('is named in Arabic', async () => {
    await act(() => i18n.changeLanguage('ar'))
    render(<OkxAccountLink />)
    expect(screen.getByRole('link', { name: 'افتح حساب OKX التجريبي' })).toBeInTheDocument()
  })
})
