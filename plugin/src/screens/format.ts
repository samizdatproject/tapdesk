// Pure number formatting for the 576x288 monochrome display.
// ASCII signs only: the glasses font drops the Unicode minus, check marks and emoji.

export const VENUE_SHORT: Record<string, string> = { binance: 'bin', bybit: 'byb', coinbase: 'cb', okx: 'okx' }
export const VENUE_NAME: Record<string, string> = { binance: 'Binance', bybit: 'Bybit', coinbase: 'Coinbase', okx: 'OKX' }

const group = (x: number, digits: number) =>
  x.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })

/** "$10,412" from $1,000 up, "$12.40" below. Negative values get an ASCII "-". */
export function usd(x: number): string {
  const a = Math.abs(x)
  return `${x < 0 ? '-' : ''}$${group(a, a >= 1000 ? 0 : 2)}`
}

/** Whole dollars without cents ("$250"), anything else as usd(). */
export function usdShort(x: number): string {
  return Number.isInteger(x) ? `${x < 0 ? '-' : ''}$${group(Math.abs(x), 0)}` : usd(x)
}

/** "+$12.40" / "-$3.10"; no sign for zero. */
export function signedUsd(x: number): string {
  const s = usd(Math.abs(x))
  return Math.abs(x) < 0.005 ? s : `${x < 0 ? '-' : '+'}${s}`
}

/** "+1.2%" / "-3.3%" */
export function pct(x: number): string {
  return `${x < 0 ? '-' : '+'}${Math.abs(x).toFixed(1)}%`
}

/** `n` significant digits without exponent notation, trailing zeros dropped. */
export function sig(x: number, n = 4): string {
  if (x === 0 || !Number.isFinite(x)) return '0'
  const digits = Math.min(12, Math.max(0, n - 1 - Math.floor(Math.log10(Math.abs(x)))))
  const s = x.toFixed(digits)
  return s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s
}

/** Prices: "58,402", "68.40", "0.9146". */
export function price(x: number): string {
  const a = Math.abs(x)
  if (a >= 1000) return group(x, 0)
  if (a >= 1) return group(x, 2)
  return sig(x, 4)
}

/** Quantities: "1,234", "12.5", "0.004282", "0.00001713" (never "0.0000"). */
export function qty(x: number): string {
  const a = Math.abs(x)
  if (a >= 1000) return group(x, 0)
  if (a >= 1) return sig(x, 6)
  return sig(x, 4)
}

/** Coin amounts as received: "1.00 USDT", "2.5 TON". */
export function amount(x: number, symbol: string): string {
  return symbol === 'USDT' ? `${group(x, 2)} USDT` : `${qty(x)} ${symbol}`
}

export const venueName = (v: string) => VENUE_NAME[v] ?? v
export const venueShort = (v: string) => VENUE_SHORT[v] ?? v

// Measured in the simulator: list items are capped at 63 UTF-8 bytes.
export const ROW_MAX_CHARS = 55
export const ROW_MAX_BYTES = 63
export const LIST_MAX_ITEMS = 20

const encoder = new TextEncoder()
const bytes = (s: string) => encoder.encode(s).length

/** Cuts by code point so it fits both limits, ending in "…" when cut. */
export function fit(text: string, maxChars = ROW_MAX_CHARS, maxBytes = ROW_MAX_BYTES): string {
  const chars = Array.from(text)
  if (chars.length <= maxChars && bytes(text) <= maxBytes) return text
  const out: string[] = []
  let used = bytes('…')
  for (const ch of chars) {
    const b = bytes(ch)
    if (out.length + 2 > maxChars || used + b > maxBytes) break
    out.push(ch)
    used += b
  }
  return out.join('').trimEnd() + '…'
}
