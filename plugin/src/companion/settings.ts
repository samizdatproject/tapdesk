import type { Settings } from '../net'

// Mirrors the server's rules so most mistakes show up before Save; the server
// still has the last word (its 400 detail is shown inline).
export const MAX_WATCH = 8
export const MAX_ALERTS = 20
const SYMBOL = /^[A-Z0-9]{2,10}$/
const RAW = /^(-1|0):[0-9a-fA-F]{64}$/
const FRIENDLY = /^[A-Za-z0-9_\-+/]{48}$/

function crc16(data: Uint8Array): number {
  let crc = 0
  for (const byte of data) {
    crc ^= byte << 8
    for (let i = 0; i < 8; i++) crc = (crc & 0x8000 ? (crc << 1) ^ 0x1021 : crc << 1) & 0xffff
  }
  return crc
}

/** null if it is a valid mainnet TON address (or empty), else a message to show. */
export function tonAddressError(input: string): string | null {
  const a = input.trim()
  if (!a || RAW.test(a)) return null
  if (!FRIENDLY.test(a)) return "That doesn't look like a TON address (48 characters, starting with EQ or UQ)."
  let bytes: Uint8Array
  try {
    bytes = Uint8Array.from(atob(a.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0))
  } catch {
    return "That TON address isn't valid."
  }
  if (bytes.length !== 36 || crc16(bytes.slice(0, 34)) !== ((bytes[34] << 8) | bytes[35])) {
    return "That TON address has a typo (checksum doesn't match)."
  }
  if (bytes[0] & 0x80) return "That's a testnet address; TapDesk watches mainnet."
  return null
}

export function cleanSymbol(raw: string): string {
  return raw.trim().toUpperCase()
}

/** Adds one coin; on a problem returns the list unchanged and a message to show. */
export function addSymbol(list: string[], raw: string): { list: string[]; error: string | null } {
  const s = cleanSymbol(raw)
  if (!SYMBOL.test(s)) return { list, error: 'Type a ticker like BTC or SOL.' }
  if (['USDT', 'USD', 'USDC'].includes(s)) return { list, error: `${s} is the quote currency.` }
  if (list.includes(s)) return { list, error: `${s} is already on the list.` }
  if (list.length >= MAX_WATCH) return { list, error: `At most ${MAX_WATCH} coins.` }
  return { list: [...list, s], error: null }
}

export type AlertRow = Settings['alerts'][number]

export function addAlert(
  list: AlertRow[],
  raw: { symbol: string; op: string; level: string },
): { list: AlertRow[]; error: string | null } {
  const symbol = cleanSymbol(raw.symbol)
  const level = Number(raw.level.replace(/,/g, '').trim())
  if (!SYMBOL.test(symbol)) return { list, error: 'Type a ticker like BTC.' }
  if (raw.op !== 'above' && raw.op !== 'below') return { list, error: 'Pick above or below.' }
  if (!Number.isFinite(level) || level <= 0) return { list, error: 'The level must be a number above zero.' }
  if (list.some(a => a.symbol === symbol && a.op === raw.op && a.level === level)) {
    return { list, error: 'That alert is already set.' }
  }
  if (list.length >= MAX_ALERTS) return { list, error: `At most ${MAX_ALERTS} alerts.` }
  return { list: [...list, { symbol, op: raw.op, level }], error: null }
}
