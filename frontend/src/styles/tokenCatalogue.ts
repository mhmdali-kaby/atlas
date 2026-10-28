/* What the Tokens story lists. Names only: values come from tokens.css at runtime. */

export type TokenGroup = { title: string; note?: string; tokens: readonly string[] }

/** §15 colour tokens exactly as the brief lists them (primitives). */
export const PRIMITIVE_TOKENS: TokenGroup = {
  title: '§15 primitives',
  note: 'The brief’s values, unchanged. Fills, icons, chart lines and focus rings use them.',
  tokens: [
    '--sys-bg',
    '--sys-bg-secondary',
    '--sys-bg-tertiary',
    '--sys-bg-grouped',
    '--sys-label',
    '--sys-label-secondary',
    '--sys-separator',
    '--sys-blue',
    '--sys-green',
    '--sys-red',
    '--sys-orange',
    '--sys-yellow',
    '--sys-purple',
    '--sys-teal',
    '--sys-grey',
  ],
}

/** Semantic tokens: what components use (Tailwind: bg-background, text-label …). */
export const SEMANTIC_GROUPS: readonly TokenGroup[] = [
  {
    title: 'Surfaces',
    tokens: ['--bg', '--bg-secondary', '--bg-tertiary', '--bg-grouped', '--bg-grouped-row', '--bg-elevated', '--bg-window'],
  },
  {
    title: 'Text and lines',
    note: 'Secondary label is the AA variant of §15’s 0.6 alpha.',
    tokens: ['--label', '--label-secondary', '--label-tertiary', '--separator', '--separator-strong', '--hairline-color', '--on-solid'],
  },
  {
    title: 'Accent and status',
    note: 'Text uses the -ink variant; the plain token is for fills.',
    tokens: [
      '--accent',
      '--accent-ink',
      '--accent-solid',
      '--accent-solid-hover',
      '--accent-solid-active',
      '--accent-wash',
      '--profit',
      '--profit-ink',
      '--profit-wash',
      '--loss',
      '--loss-ink',
      '--loss-wash',
      '--warning',
      '--warning-ink',
      '--warning-wash',
      '--agent',
      '--demo',
      '--demo-ink',
      '--demo-wash',
      '--destructive-solid',
      '--neutral',
    ],
  },
  {
    title: 'Control fills',
    tokens: [
      '--fill-1',
      '--fill-2',
      '--fill-3',
      '--fill-4',
      '--field-bg',
      '--field-border',
      '--field-border-hover',
      '--switch-off',
      '--switch-off-border',
      '--knob',
      '--selection',
      '--scrim',
    ],
  },
  {
    title: 'Materials',
    tokens: ['--material-bar', '--material-sidebar', '--material-popover'],
  },
  {
    title: 'Seal and chart',
    tokens: [
      '--seal-ink-1',
      '--seal-ink-2',
      '--seal-ground',
      '--seal-muted',
      '--chart-line',
      '--chart-area-top',
      '--chart-area-bottom',
      '--chart-grid',
      '--chart-axis',
      '--wash-1',
      '--wash-2',
    ],
  },
]

export const RADII = [
  { name: 'control', className: 'rounded-control', use: 'Buttons, fields' },
  { name: 'card', className: 'rounded-card', use: 'Cards, grouped lists, menus' },
  { name: 'panel', className: 'rounded-panel', use: 'Sidebar, table panels, toasts' },
  { name: 'sheet', className: 'rounded-sheet', use: 'Sheets' },
] as const

/** The §15 type scale (ten styles) plus the design's hero. */
export const TEXT_STYLES = [
  { name: 'large-title', className: 'text-large-title', spec: '34 / 41 semibold' },
  { name: 'title-1', className: 'text-title-1', spec: '28 / 34 semibold' },
  { name: 'title-2', className: 'text-title-2', spec: '22 / 28 semibold' },
  { name: 'title-3', className: 'text-title-3', spec: '20 / 25 semibold' },
  { name: 'headline', className: 'text-headline', spec: '17 / 22 semibold' },
  { name: 'body', className: 'text-body', spec: '17 / 22' },
  { name: 'callout', className: 'text-callout', spec: '16 / 21' },
  { name: 'subheadline', className: 'text-subheadline', spec: '15 / 20' },
  { name: 'footnote', className: 'text-footnote', spec: '13 / 18 (Arabic 14 / 21)' },
  { name: 'caption', className: 'text-caption', spec: '12 / 16 (Arabic 13 / 19)' },
] as const

export const HERO_STYLE = { name: 'hero', className: 'text-hero', spec: '48 / 56 semibold — total equity only' } as const
