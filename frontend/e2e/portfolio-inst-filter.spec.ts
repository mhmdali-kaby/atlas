import { expect, test, type Page } from '@playwright/test'

/*
 * ATL-220: `?inst_id=` on Portfolio → Positions and Orders (design v18 "Autonomy and
 * notifications" §5.6) — a notification link filters every table to that one instrument, with a
 * removable chip; a position the stop has already closed shows an empty state that opens History
 * filtered to the same instrument. Screenshots go to ../../evidence/ATL-220/.
 */

const SHOTS = '../../evidence/ATL-220'
const OWNER = { id: '01a18376-1d35-70c2-ad01-6d1d2cd870ad', name: 'Mohammed Ali', email: 'owner@atlas.test', locale: 'en', theme: 'system' }
const NOW = Date.now()
const HOUR = 3_600_000
const MIN = 60_000

const BTC = {
  id: '809d7f30',
  inst_id: 'BTC-USDT-SWAP',
  side: 'long',
  pos_side: 'net',
  size: '25',
  leverage: '5',
  entry: '67250.5',
  mark: '68120.3',
  liq_px: '54112.8',
  margin: '3362.525',
  settle_ccy: 'USDT',
  upl_usd: '217.45',
  upl_pct: '6.466866',
  margin_mode: 'isolated',
  opened_at: new Date(NOW - 9 * HOUR).toISOString(),
  stop: null,
  take_profits: null,
  agent: null,
  proposal_id: null,
}
const XAU = { ...BTC, id: '85242c4a', inst_id: 'XAU-USDT-SWAP', side: 'short', size: '400', upl_usd: '-53.6', opened_at: new Date(NOW - 27 * HOUR).toISOString() }

const SOL = {
  id: '9d6c1f2e-0000-4000-8000-000000000001',
  env: 'demo',
  inst_id: 'SOL-USDT-SWAP',
  side: 'buy',
  pos_side: 'net',
  ord_type: 'post_only',
  td_mode: 'isolated',
  px: '249.4',
  sz: '12.02',
  filled_sz: '3',
  avg_px: null,
  lever: 3,
  state: 'partially_filled',
  purpose: 'entry',
  origin: 'atlas',
  reduce_only: false,
  cl_ord_id: 'e3f0',
  ord_id: '123456',
  proposal_id: null,
  last_error_code: null,
  last_error_msg: null,
  can_cancel: true,
  created_at: new Date(NOW - 69 * MIN).toISOString(),
  updated_at: new Date(NOW - 60 * MIN).toISOString(),
}
const ETH = { ...SOL, id: '9d6c1f2e-0000-4000-8000-000000000002', inst_id: 'ETH-USDT-SWAP', side: 'sell', state: 'live' }
const algo = (over: Record<string, unknown>) => ({
  id: 'a1',
  env: 'demo',
  order_id: null,
  inst_id: 'BTC-USDT-SWAP',
  kind: 'sl',
  protects: true,
  side: 'sell',
  pos_side: 'net',
  trigger_px: null,
  sl_trigger_px: null,
  tp_trigger_px: null,
  ord_px: null,
  sz: '250',
  state: 'live',
  origin: 'atlas',
  reduce_only: true,
  algo_id: '7001',
  algo_cl_ord_id: 's3f',
  last_error_code: null,
  last_error_msg: null,
  created_at: new Date(NOW - 300 * MIN).toISOString(),
  updated_at: new Date(NOW - 300 * MIN).toISOString(),
  ...over,
})
const ALGOS = [
  algo({ id: 'a1', kind: 'oco', sl_trigger_px: '113200', tp_trigger_px: '121300' }),
  algo({ id: 'a2', inst_id: 'XAU-USDT-SWAP', kind: 'sl', trigger_px: '4117.6', sz: '4000', side: 'buy' }),
  algo({ id: 'a3', inst_id: 'XAU-USDT-SWAP', kind: 'tp', protects: false, trigger_px: '4075.8', sz: '2000', side: 'buy' }),
]

const closedNvda = { id: 'c1', pos_id: '1', inst_id: 'NVDA-USDT-SWAP', asset_class: 'stock', side: 'long', pos_side: 'net', margin_mode: 'isolated', close_type: '2', leverage: '3', ccy: 'USDT', size: '10', entry: '180', exit: '185', realised_pnl: '50', pnl: '50', fees: '-0.5', funding: '0', liq_penalty: '0', r: '1.1', outcome: 'win', opened_at: new Date(NOW - 5 * HOUR).toISOString(), closed_at: new Date(NOW - HOUR).toISOString(), proposal_id: null, agent: 'strategist', setup_tag: null, origin: 'atlas' }
const closedBtc = { ...closedNvda, id: 'c2', inst_id: 'BTC-USDT-SWAP' }

async function fakeApi(page: Page, opts: { positions?: unknown[]; history?: unknown[] } = {}) {
  const positions = opts.positions ?? [BTC, XAU]
  const history = opts.history ?? [closedNvda, closedBtc]
  await page.route(/^http:\/\/localhost:8000\//, async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const cors = { 'Access-Control-Allow-Origin': req.headers()['origin'] ?? '*', 'Access-Control-Allow-Credentials': 'true' }
    const json = (status: number, body?: unknown) =>
      route.fulfill({ status, contentType: 'application/json', headers: cors, body: body === undefined ? '' : JSON.stringify(body) })
    if (req.method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: { ...cors, 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': 'GET,POST' } })
    }
    const p = url.pathname
    if (p === '/sanctum/csrf-cookie') return json(204)
    if (p === '/api/auth/status') return json(200, { owner_exists: true })
    if (p === '/api/auth/me') return json(200, { data: OWNER })
    if (p === '/api/connections') return json(200, { data: [] })
    if (p === '/api/portfolio/summary') return json(200, { data: { env: 'demo', reconciled_at: new Date(NOW).toISOString(), stale: false } })
    if (p === '/api/portfolio/positions') return json(200, { data: { taken_at: new Date(NOW).toISOString(), items: positions } })
    if (p === '/api/portfolio/orders') return json(200, { data: { env: 'demo', orders: [SOL, ETH], algo_orders: ALGOS } })
    if (p === '/api/portfolio/history') {
      const type = url.searchParams.get('type') ?? 'fills'
      const instId = url.searchParams.get('inst_id')
      let rows = type === 'closed' ? history : []
      if (instId) rows = rows.filter((r): r is { inst_id: string } => typeof r === 'object' && r !== null && (r as { inst_id: string }).inst_id === instId)
      return json(200, { data: rows, meta: { type, per_page: 200, next_cursor: null, prev_cursor: null } })
    }
    if (p.startsWith('/api/exports/')) return route.fulfill({ status: 200, contentType: 'text/csv', headers: cors, body: 'id\n1\n' })
    return json(404, { message: 'Not found' })
  })
}

async function open(page: Page, path: string, opts: Parameters<typeof fakeApi>[1] = {}) {
  await page.setViewportSize({ width: 1280, height: 900 })
  await fakeApi(page, opts)
  await page.goto(path)
}

test('Positions filters to the linked instrument, with a removable chip', async ({ page }) => {
  await open(page, '/portfolio/positions?inst_id=BTC-USDT-SWAP')
  await expect(page.getByTestId('positions-table')).toBeVisible()
  await expect(page.getByTestId('position-row')).toHaveCount(1)
  await expect(page.getByTestId('position-row')).toHaveAttribute('data-inst', 'BTC-USDT-SWAP')
  const chip = page.getByTestId('inst-filter')
  await expect(chip).toBeVisible()
  await expect(chip).toContainText('Showing 1 of 2')
  await page.screenshot({ path: `${SHOTS}/positions-filtered.png` })

  await page.getByTestId('inst-filter-remove').click()
  await expect(page.getByTestId('position-row')).toHaveCount(2)
  await expect(page.getByTestId('inst-filter')).toHaveCount(0)
  await expect(page).toHaveURL('/portfolio/positions')
})

test('Orders filters open and algo tables to the linked instrument, with a removable chip', async ({ page }) => {
  await open(page, '/portfolio/orders?inst_id=XAU-USDT-SWAP')
  await expect(page.getByTestId('open-orders-table')).toBeVisible()
  await expect(page.getByTestId('open-orders-filtered-empty')).toBeVisible()
  await expect(page.getByTestId('algo-order-row')).toHaveCount(2)
  await expect(page.getByTestId('inst-filter')).toContainText('Showing 2 of 5')

  await page.getByTestId('inst-filter-remove').click()
  await expect(page.getByTestId('open-order-row')).toHaveCount(2)
  await expect(page.getByTestId('algo-order-row')).toHaveCount(3)
  await expect(page).toHaveURL('/portfolio/orders')
})

test('Positions with no open position on the linked instrument offers Show in History, filtered there', async ({ page }) => {
  await open(page, '/portfolio/positions?inst_id=NVDA-USDT-SWAP', { positions: [BTC] })
  const empty = page.getByTestId('positions-filtered-empty')
  await expect(empty).toBeVisible()
  await expect(empty).toContainText('No open position on NVDA-USDT-SWAP')
  await page.screenshot({ path: `${SHOTS}/positions-closed-empty.png` })

  await page.getByTestId('positions-show-history').click()
  await expect(page).toHaveURL(/\/portfolio\/history/)
  await expect(page.getByTestId('history-page')).toHaveAttribute('data-type', 'closed')
  await expect(page.getByText('NVDA-USDT-SWAP', { exact: false }).first()).toBeVisible()
  await expect(page.getByText('BTC-USDT-SWAP', { exact: false })).toHaveCount(0)

  const chip = page.getByTestId('history-chips')
  await expect(chip).toBeVisible()
  await expect(chip).toContainText('NVDA-USDT-SWAP')
  // The `inst_id` query is read once and dropped (design v18 §5.6); a reload would start blank.
  await expect(page).toHaveURL('/portfolio/history')

  await chip.getByRole('button', { name: /remove/i }).click()
  await expect(page.getByText('BTC-USDT-SWAP', { exact: false })).toBeVisible()
})
