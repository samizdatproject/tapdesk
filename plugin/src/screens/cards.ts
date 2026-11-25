import type { AlertCard, ArrivalCard, Card, OrderRequest, Position, Session } from '../net'
import { amount, fit, price, qty, signedUsd, usd, usdShort } from './format'
import { BODY, FOOTER, HEADER, type Page } from './manager'

/** A tap-able choice and what it asks the server for (null: just dismiss). */
export interface Option {
  label: string
  order: OrderRequest | null
  /** Shown on the confirm screen. */
  confirm: string
}

/** The arrival card's choices, in list order. */
export function arrivalOptions(card: ArrivalCard): Option[] {
  const got = amount(card.amount, card.symbol)
  const sym = card.buySymbol
  return [
    {
      label: `Paper-buy ${sym} with it`,
      order: { side: 'buy', symbol: sym, arrivalId: card.arrivalId, cardId: card.id },
      confirm: `Paper-buy ${sym} with ${got}`,
    },
    {
      label: `Paper-buy ${usdShort(card.fixedUsd)} ${sym}`,
      order: { side: 'buy', symbol: sym, usd: card.fixedUsd, cardId: card.id },
      confirm: `Paper-buy ${usdShort(card.fixedUsd)} of ${sym}`,
    },
    { label: 'Ignore', order: null, confirm: '' },
  ]
}

export function arrivalHeader(card: ArrivalCard): string {
  return fit(`Received ${amount(card.amount, card.symbol)} on TON${card.test ? ' (test)' : ''}`, 55, 200)
}

export function arrivalPage(card: ArrivalCard): Page {
  const value = card.valueUsd === null ? '' : `~${usd(card.valueUsd)} · `
  return {
    id: 'arrival',
    texts: [
      { name: 'hdr', ...HEADER, content: arrivalHeader(card) },
      { name: 'ftr', ...FOOTER, content: `${value}paper only · 2x ignore`, dim: true },
    ],
    list: { name: 'options', ...BODY, items: arrivalOptions(card).map(o => fit(o.label)) },
  }
}

export function alertText(card: AlertCard): string {
  return [
    `ALERT  ${card.symbol} ${card.op} ${price(card.level)}`,
    '',
    `Now ${price(card.price)} (mid of the best bid and ask)`,
    '',
    'Set by you · fired once',
    '',
    'tap ok',
  ].join('\n')
}

export function alertPage(card: AlertCard): Page {
  return { id: 'alert', texts: [{ name: 'msg', x: 0, y: 0, w: 576, h: 288, content: alertText(card), capture: true }] }
}

export function cardPage(card: Card): Page {
  return card.kind === 'arrival' ? arrivalPage(card) : alertPage(card)
}

// ---- positions ----

export function positionRow(p: Position): string {
  return fit(`${p.symbol} ${qty(p.qty)} @${price(p.avg)} ${signedUsd(p.upnl)}`)
}

export function positionsHeader(s: Session | null): string {
  if (!s) return 'Positions'
  return `Cash ${usd(s.paper.cash)} · realised ${signedUsd(s.paper.realised)}`
}

export const NO_POSITIONS = [
  'No paper positions yet.',
  '',
  'Hold to talk, for example:',
  '"buy 100 dollars of TON"',
  '',
  '2x back',
].join('\n')

/** The list is drawn once per visit (a redraw would move the highlight); the header stays live. */
export function positionsPage(s: Session | null): Page {
  const positions = s?.paper.positions ?? []
  const texts: Page['texts'] = [
    { name: 'hdr', ...HEADER, content: positionsHeader(s) },
    { name: 'ftr', ...FOOTER, content: 'tap: sell all · hold talk · 2x back', dim: true },
  ]
  if (positions.length === 0) {
    return { id: 'positions', texts: [{ name: 'msg', x: 0, y: 0, w: 576, h: 288, content: NO_POSITIONS, capture: true }] }
  }
  return { id: 'positions', texts, list: { name: 'positions', ...BODY, items: positions.slice(0, 20).map(positionRow) } }
}
