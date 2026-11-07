# ATL-15 — core controls in `src/ui/` and figure formatting in `src/lib/format.ts`

Built to design system "Atlas: Phase 0" §5, §7, §8 and §9. Import everything from `src/ui` (barrel `src/ui/index.ts`).

## The two-minute version

- **Never format a number yourself.** Money, percentages and signs go through `formatMoney` / `formatPercent` / `formatSigned`, or `<SignedValue>` for PnL and funding. Pass the API's decimal **string** straight in; nothing converts it to a float.
- **`null` is not zero.** Every formatter returns `"—"` for `null`/`undefined`/`""`. Do not pre-convert.
- **Figures use `en-US` in Arabic too** (design §5 decision, which supersedes the task's "ar-IQ-u-nu-latn for figures"): `−$1,234.50` reads the same in both languages. Wrap any figure you render yourself in the `fig` utility (LTR isolate + number font). Dates use `DATE_LOCALE[lang]` (`ar-IQ-u-nu-latn` in Arabic).
- **Logical properties only in `src/ui`.** `src/ui/no-physical-classes.test.ts` fails on `ml- mr- pl- pr- left- right-` (any variant, leading minus), `text-left/right`, and physical margin/padding/left/right in CSS.
- **One spring:** `SPRING` in `src/ui/motion.ts` is 300/30 (design §7, which rules out stiffer springs because 350–400 overshoot). Wrap motion trees in `<MotionConfig reducedMotion="user">`.
- **Your own `shadow-*` on a focusable element hides the base focus ring.** Add `focus-visible:shadow-focus` (Button and Toggle do).

## `src/lib/format.ts`

```ts
type Lang = 'en' | 'ar'
type Figure = string | number | null | undefined
FIGURE_LOCALE: { en: 'en-US', ar: 'en-US' }
DATE_LOCALE:   { en: 'en-US', ar: 'ar-IQ-u-nu-latn' }
NO_VALUE = '—'

formatMoney('5068412.56', 'USD')        // "$5,068,412.56"
formatMoney('-214.07', 'USD', 'ar')     // "−$214.07"
formatMoney('1500.5', 'USDT')           // "1,500.50 USDT" (non-ISO code → suffix, NBSP)
formatPercent('0.76')                   // "0.76%"  (value already in percent units)
formatSigned('-1234.5', 'USD')          // "−$1,234.50"  (U+2212 minus)
formatSigned('2104.18', 'USD', 'ar')    // "+$2,104.18"
formatSigned('0', 'USD')                // "$0.00"  (zero has no sign)
formatSigned('0.76', 'percent')         // "+0.76%"
formatSigned(null, 'USD')               // "—"
signOf('-0.00')                         // 0   (-1 | 0 | 1 | null, from the string, no float)
```

Signs come from `Intl.NumberFormat` `signDisplay` (`auto` / `exceptZero`); the only post-processing is swapping U+002D for U+2212.

## Components

| Component | Props worth knowing |
| --- | --- |
| `Button` | `variant`: `primary` · `secondary` (default) · `destructive` · `plain` · `plain-destructive`; `size`: `sm` 28 · `md` 32 · `lg` 44; `icon` (16px, before the label); `loading` (spinner, keeps label, `aria-busy`); `asChild` (e.g. wrap a router `<Link>`). `type` defaults to `"button"`. |
| `TextField` | `label` (required), `hint`, `error` (sets `aria-invalid`, describedby, red border, never clears value), `inputClassName`, `endSlot`; all `<input>` props. `className` goes on the wrapper. |
| `SecureField` | Same as TextField minus `type`. Masked, show/hide button (`aria-pressed`, "Show"/"Hide"), mono font, `dir="ltr"` on the field in both languages. |
| `Toggle` | Radix Switch Root props (`checked`, `defaultChecked`, `onCheckedChange`, `disabled`). Give it `aria-label` or `aria-labelledby`. |
| `SegmentedControl<V>` | `options: {value, label, disabled?}[]`, `value`/`defaultValue`, `onValueChange(v: V)`, `aria-label` (required), `disabled`. Radix RadioGroup; arrows follow the reading direction. |
| `GroupedList` / `Row` | `GroupedList {title?, note?}`; `Row {icon?, label, sub?, children (value/control at the end), onClick?, disabled?, labelId?}`. `labelId` lets a Toggle in the row use `aria-labelledby`. |
| `Card` | A div: radius 12, padding 20, tertiary background, hairline + shadow-1. |
| `Sheet` / `SheetClose` | `open`/`defaultOpen`/`onOpenChange`, `trigger`, `title`, `description`, `icon`, `tone: 'default' \| 'destructive'`, `children`, `actions` (inline end; destructive last). Radix Dialog: focus trap, Esc, scrim click, focus returns to the trigger. Wrap a cancel Button in `<SheetClose>`. |
| `Toaster` / `toast()` | Mount `<Toaster />` once at the app root (ATL-17's shell). `toast({ title: t('saved'), body?, tone?: 'success' \| 'warning' \| 'error' \| 'info' })`. Top end, 4.2s, polite live region. Store: `useToasts`. |
| `SignedValue` | `value`, `unit` (`'USD'` or `'percent'`), `nullTip?` (makes "—" focusable, read out instead of the dash). Profit/loss ink + sign + mirrored arrow + sr-only "gain"/"loss". Zero: no sign, no icon. |
| `Spinner` | 12-spoke, `size` 16 or 28, `currentColor`. |

Not built here (design §9 lists them, the task did not): Badge, Notice, Menu, Tooltip, Table, Skeleton, Stepper, ChoiceCard, AccountSeal, AppIcon.

## Tokens added to `styles/tokens.css`

`--segment-thumb` (`bg-segment-thumb`; dark #636366 per the prototype), `--segment-thumb-shadow` (`shadow-segment-thumb`), `--knob-shadow` (`shadow-knob`), `--error-ring` (`shadow-error-ring`, the 30% red focus ring on an invalid field).

## i18n

New namespace `ui` (`src/i18n/locales/{en,ar}/ui.json`): `secure.show/hide`, `signed.gain/loss`, `loading`, `close`, `dismiss`, `notifications`.

## Storybook

Stories under **Controls/** — each has `States`, `FocusVisible` (where focusable), `Dark`, `RightToLeft`, `DarkRightToLeft`; play functions assert the ring, mirroring and colours in real Chromium. Story copy is bilingual via `src/storybook/copy.ts`.

## Gotchas found on the way

- If a new locale JSON doesn't show up in Vitest, the transform cache is stale: `npx vitest --run --clearCache`.
- Radix RadioGroup selects on focus **while the arrow key is held** and moves focus in a `setTimeout`. In tests use `keyboard('{ArrowRight>}')`, `waitFor`, then `keyboard('{/ArrowRight}')`.
- Tailwind v4's `-scale-x-100` sets the CSS `scale` property, not `transform`; motion's transforms don't fight it.
