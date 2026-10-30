import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { cards, DAY, watchlistMessage } from './fixtures/watchlist'
import { useLiveFeed } from './useLiveFeed'
import { watchlistKey } from './watchlist'

/* ATL-115: the card re-renders on `watchlist.updated` (ATL-103) and on a newer watchlist card. */

const handlers = new Map<string, (e: unknown) => void>()
vi.mock('../../lib/echo', () => ({
  getEcho: () => ({
    private: () => ({
      listen: (name: string, fn: (e: unknown) => void) => handlers.set(name, fn),
      stopListening: (name: string) => handlers.delete(name),
    }),
  }),
  hasConnected: () => true,
  reconnectEcho: () => {},
  useLiveStatus: () => 'connected',
}))

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  qc.setQueryData(watchlistKey('demo', DAY), { env: 'demo', date: DAY, watchlist: null })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  renderHook(() => useLiveFeed('demo', 'thread-main'), { wrapper })
  const invalidated = () => qc.getQueryState(watchlistKey('demo', DAY))?.isInvalidated
  return { invalidated }
}

describe('watchlist live refresh', () => {
  it('refetches the day on watchlist.updated for this env, not another', () => {
    const { invalidated } = setup()
    expect(invalidated()).toBe(false)
    handlers.get('.watchlist.updated')!({ type: 'watchlist.updated', env: 'live', payload: {} })
    expect(invalidated()).toBe(false)
    handlers.get('.watchlist.updated')!({ type: 'watchlist.updated', env: 'demo', payload: { action: 'removed' } })
    expect(invalidated()).toBe(true)
  })

  it('refetches the day when a new watchlist card arrives', () => {
    const { invalidated } = setup()
    handlers.get('.warroom.message')!(watchlistMessage(cards.rerun()))
    expect(invalidated()).toBe(true)
  })
})
