/** "+1.2R", "−1.0R", "0.0R": the sign comes from the rounded value, so it never reads "−0.0R". */
export function rText(v: string | number, digits = 1): string {
  const nf = new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: 'exceptZero' })
  const value = typeof v === 'number' ? v : (v.trim() as Intl.StringNumericLiteral)
  return `${nf.format(value).replace(/-/g, '−')}R`
}
