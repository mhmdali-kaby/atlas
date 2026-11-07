import { useLayoutEffect, useRef, useState } from 'react'
import { HERO_STYLE, PRIMITIVE_TOKENS, RADII, SEMANTIC_GROUPS, TEXT_STYLES, type TokenGroup } from './tokenCatalogue'

/*
 * The token reference page shown in Storybook (Foundations/Tokens).
 * It never names a colour value: every swatch paints `var(--token)` and prints
 * the value the browser resolved, so the page always shows what tokens.css says.
 * Developer documentation, so the copy is English only.
 */

function Swatch({ token }: { token: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [value, setValue] = useState('')

  useLayoutEffect(() => {
    if (ref.current) setValue(getComputedStyle(ref.current).getPropertyValue(token).trim())
  }, [token])

  return (
    <div ref={ref} data-token={token} className="flex items-center gap-3">
      <div
        aria-hidden
        className="size-10 shrink-0 rounded-control border-[0.5px] border-separator"
        style={{ background: `var(${token})` }}
      />
      <div className="min-w-0">
        <div className="fig text-footnote font-semibold">{token}</div>
        <div data-token-value className="fig truncate text-caption text-label-secondary">
          {value}
        </div>
      </div>
    </div>
  )
}

function Group({ group }: { group: TokenGroup }) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h3 className="text-headline">{group.title}</h3>
        {group.note ? <p className="text-footnote text-label-secondary">{group.note}</p> : null}
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-x-6 gap-y-3">
        {group.tokens.map((token) => (
          <Swatch key={token} token={token} />
        ))}
      </div>
    </section>
  )
}

/** One complete token sheet rendered in a forced theme scope. */
export function TokenSheet({ theme }: { theme: 'light' | 'dark' }) {
  return (
    <section
      data-theme-sheet={theme}
      aria-label={`${theme === 'light' ? 'Light' : 'Dark'} theme`}
      className={`${theme} flex flex-col gap-8 rounded-panel border-[0.5px] border-hairline bg-background p-8 text-label shadow-2`}
    >
      <h2 className="text-title-2">{theme === 'light' ? 'Light' : 'Dark'}</h2>

      <Group group={PRIMITIVE_TOKENS} />
      {SEMANTIC_GROUPS.map((group) => (
        <Group key={group.title} group={group} />
      ))}

      <section className="flex flex-col gap-3">
        <h3 className="text-headline">Radii</h3>
        <div className="flex flex-wrap gap-6">
          {RADII.map((radius) => (
            <div key={radius.name} data-radius={radius.name} className="flex flex-col gap-2">
              <div className={`size-24 border-[0.5px] border-separator bg-fill-3 ${radius.className}`} />
              <div className="fig text-footnote font-semibold">--radius-{radius.name}</div>
              <div className="text-caption text-label-secondary">{radius.use}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-headline">Text styles</h3>
        <div className="flex flex-col divide-y-[0.5px] divide-separator">
          {[...TEXT_STYLES, HERO_STYLE].map((style) => (
            <div key={style.name} data-text-style={style.name} className="flex items-baseline justify-between gap-6 py-3">
              <span className={style.className}>Total equity 5,068,412.56</span>
              <span className="fig shrink-0 text-footnote text-label-secondary">
                text-{style.name} · {style.spec}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-headline">Materials and elevation</h3>
        <div className="flex flex-wrap gap-6">
          <div className="vibrancy-bar rounded-panel border-[0.5px] border-hairline p-5 text-subheadline">vibrancy-bar</div>
          <div className="vibrancy-sidebar rounded-panel border-[0.5px] border-hairline p-5 text-subheadline shadow-sidebar">
            vibrancy-sidebar
          </div>
          <div className="vibrancy-popover rounded-card border-[0.5px] border-hairline p-5 text-subheadline shadow-popover">
            vibrancy-popover
          </div>
          <div className="sheet-surface p-5 text-subheadline">sheet-surface</div>
        </div>
      </section>
    </section>
  )
}

export function TokensPage() {
  return (
    <div className="flex flex-col gap-8 bg-background-window p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-large-title">Tokens</h1>
        <p className="text-subheadline text-label-secondary">
          Every colour, radius and text style from src/styles/tokens.css, in light and dark.
        </p>
      </header>
      <div className="grid gap-8 xl:grid-cols-2">
        <TokenSheet theme="light" />
        <TokenSheet theme="dark" />
      </div>
    </div>
  )
}
