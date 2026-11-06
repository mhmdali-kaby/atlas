import { ArrowLeftRight, ChartColumn, ChartSpline, Gauge, History, Layers, ListChecks, Wallet } from 'lucide-react'
import type { PaletteModule } from '../command-palette/types'

/* Portfolio's pages in the ⌘K palette (ATL-249). Labels are the sidebar's own. */
const palette: PaletteModule = {
  entries: [
    { id: 'page.portfolio.overview', group: 'pages', area: 'portfolio', order: 10, to: '/portfolio', icon: ChartSpline, labelKey: 'portfolio:nav.overview', keywords: 'portfolio equity pnl balance محفظة رصيد أرباح' },
    { id: 'page.portfolio.positions', group: 'pages', area: 'portfolio', order: 20, to: '/portfolio/positions', icon: Layers, labelKey: 'portfolio:nav.positions', keywords: 'open positions close مراكز مفتوحة' },
    { id: 'page.portfolio.orders', group: 'pages', area: 'portfolio', order: 25, to: '/portfolio/orders', icon: ListChecks, labelKey: 'portfolio-orders:nav', keywords: 'pending cancel algo أوامر معلقة' },
    { id: 'page.portfolio.holdings', group: 'pages', area: 'portfolio', order: 30, to: '/portfolio/holdings', icon: Wallet, labelKey: 'portfolio:nav.holdings', keywords: 'assets balances أرصدة أصول' },
    { id: 'page.portfolio.cashflows', group: 'pages', area: 'portfolio', order: 40, to: '/portfolio/cashflows', icon: ArrowLeftRight, labelKey: 'portfolio:nav.cashflows', keywords: 'deposits withdrawals إيداع سحب' },
    { id: 'page.portfolio.history', group: 'pages', area: 'portfolio', order: 50, to: '/portfolio/history', icon: History, labelKey: 'portfolio-history:nav', keywords: 'fills closed trades csv سجل صفقات' },
    { id: 'page.portfolio.performance', group: 'pages', area: 'portfolio', order: 60, to: '/portfolio/performance', icon: ChartColumn, labelKey: 'portfolio-performance:nav', keywords: 'win rate sharpe drawdown أداء' },
    { id: 'page.portfolio.scorecards', group: 'pages', area: 'portfolio', order: 70, to: '/portfolio/scorecards', icon: Gauge, labelKey: 'command-palette:page.pfScorecards', keywords: 'agent pnl scorecards بطاقات' },
  ],
}

export default palette
