import type { LinkStatus, Session, Wallet, WatchRow } from '../net'
import { amount, pct, price, signedUsd, usd, venueShort } from './format'
import { BODY, FOOTER, HEADER, type Page } from './manager'
import { LINE_UNITS, fitWidth } from './text'

export const MENU_POSITIONS = 1
export const MENU_TALK = 2
export const MENU_REFRESH = 3
/** Watchlist rows that fit under the wallet and paper lines. */
export const SESSION_ROWS = 4

export const INTRO_TEXT = [
  'TapDesk',
  'Paper trading · public market data · no financial advice',
  'Unofficial · not affiliated with any exchange',
  '',
  'Pair on your phone screen: enter the code shown by the backend.',
  '',
  'Double-tap to exit',
].join('\n')

export const CONNECTING_TEXT = 'Connecting to your TapDesk server…'

/** Root page until the phone screen has paired with a backend. */
export function introPage(): Page {
  return { id: 'intro', texts: [{ name: 'intro', x: 0, y: 0, w: 576, h: 288, content: INTRO_TEXT, capture: true }] }
}

/** "BTC 58,402 +1.2% cb": best ask (what a buy pays) and the venue that has it. */
export function watchRow(w: WatchRow): string {
  if (w.missing) return `${w.symbol}  --  no venue lists it`
  const change = w.change === null ? '' : ` ${pct(w.change)}`
  return w.stale ? `${w.symbol} ${price(w.ask)} stale` : `${w.symbol} ${price(w.ask)}${change} ${venueShort(w.askVenue)}`
}

export function walletLine(w: Wallet | null): string {
  if (!w) return 'Wallet: add a TON address on the phone'
  if (w.ton === null) return w.error ? `Wallet ${w.address} · ${w.error}` : `Wallet ${w.address} · loading…`
  const coins = `${amount(w.ton, 'TON')} + ${amount(w.usdt ?? 0, 'USDT')}`
  const value = w.valueUsd === null ? '' : ` = ${usd(w.valueUsd)}`
  return fitWidth(`Wallet ${coins}${value}${w.ok ? '' : ' (old)'}`, LINE_UNITS)
}

/** Header: everything (wallet value + paper equity) and the paper PnL. */
export function sessionHeader(s: Session | null): string {
  if (!s) return 'TapDesk · paper'
  return `Total ${usd(s.total)} · PnL ${signedUsd(s.paper.pnl)} (${pct(s.paper.pnlPct)})`
}

export function sessionBody(s: Session | null): string {
  if (!s) return CONNECTING_TEXT
  const n = s.paper.positions.length
  const paper = `Paper ${usd(s.paper.equity)} · cash ${usd(s.paper.cash)}${n ? ` · ${n} pos` : ''}`
  return [walletLine(s.wallet), fitWidth(paper, LINE_UNITS), '', ...s.watch.slice(0, SESSION_ROWS).map(watchRow)].join('\n')
}

export function sessionFooter(link: LinkStatus, s: Session | null): string {
  if (link === 'offline') return 'Offline · retrying…'
  if (link === 'connecting') return 'Connecting…'
  const alerts = s?.alerts.length ? ` · ${s.alerts.length} alert${s.alerts.length === 1 ? '' : 's'}` : ''
  return `tap positions · hold talk${alerts} · 2x exit`
}

export function sessionPage(s: Session | null, link: LinkStatus): Page {
  return {
    id: 'session',
    texts: [
      { name: 'hdr', ...HEADER, content: sessionHeader(s) },
      { name: 'body', ...BODY, content: sessionBody(s), capture: true },
      { name: 'ftr', ...FOOTER, content: sessionFooter(link, s), dim: true },
    ],
    menu: [
      { name: 'Positions', id: MENU_POSITIONS },
      { name: 'Voice command', id: MENU_TALK },
      { name: 'Refresh', id: MENU_REFRESH },
    ],
  }
}
