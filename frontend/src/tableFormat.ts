export function compactNumber(value: number | null | undefined, digits = 3) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: digits,
  }).format(value)
}
