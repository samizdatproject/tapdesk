import { describe, expect, it } from 'vitest'
import type { ArrivalCard, Session, WatchRow } from '../net'
import { arrivalHeader, arrivalOptions, arrivalPage, positionRow, positionsPage } from './cards'
import { amount, fit, pct, price, qty, signedUsd, usd } from './format'
import { sessionBody, sessionFooter, sessionHeader, walletLine, watchRow } from './session'

const row = (over: Partial<WatchRow> = {}): WatchRow => ({
  symbol: 'BTC',
  bid: 58_390,
  bidVenue: 'bybit',
  ask: 58_402.4,
  askVenue: 'binance',
  mid: 58_396,
  change: 1.234,
  stale: false,
  missing: false,
  ...over,
})

const session = (over: Partial<Session> = {}): Session => ({
  total: 10_412.3,
  wallet: { address: 'UQAb…x9Zk', ton: 10.5, usdt: 1, valueUsd: 10.6, ok: true, error: '' },
  watch: [row(), row({ symbol: 'ETH', ask: 1661.04, change: -0.5, askVenue: 'coinbase' }), row({ symbol: 'TON', ask: 0.92571, change: -3.3, askVenue: 'okx' }), row({ symbol: 'SOL', missing: true }), row({ symbol: 'DOGE' })],
  paper: { cash: 9_750, deposits: 1, realised: 0, equity: 10_001.7, pnl: 0.7, pnlPct: 0.007, positions: [] },
  alerts: [],
  ...over,
})

describe('numbers', () => {
  it('formats money, signs and percentages with ASCII signs', () => {
    expect([usd(10_412.3), usd(12.4), usd(-3.1)]).toEqual(['$10,412', '$12.40', '-$3.10'])
    expect([signedUsd(12.4), signedUsd(-3.1), signedUsd(0)]).toEqual(['+$12.40', '-$3.10', '$0.00'])
    expect([pct(1.234), pct(-3.3), pct(0)]).toEqual(['+1.2%', '-3.3%', '+0.0%'])
  })

  it('formats prices by size', () => {
    expect([price(58_402.4), price(68.4), price(0.92571)]).toEqual(['58,402', '68.40', '0.9257'])
  })

  it('never rounds a small quantity to zero', () => {
    expect([qty(0.0000171263), qty(0.0042817), qty(12.5), qty(1234.5)]).toEqual(['0.00001713', '0.004282', '12.5', '1,235'])
    expect([amount(1, 'USDT'), amount(2.5, 'TON')]).toEqual(['1.00 USDT', '2.5 TON'])
  })

  it('fits list rows into the 63-byte limit', () => {
    expect(fit('x'.repeat(80))).toHaveLength(55)
    expect(fit('short')).toBe('short')
  })
})

describe('session screen', () => {
  it('rows show the best ask, the 24 h change and the venue', () => {
    expect(watchRow(row())).toBe('BTC 58,402 +1.2% bin')
    expect(watchRow(row({ change: null }))).toBe('BTC 58,402 bin')
    expect(watchRow(row({ stale: true }))).toBe('BTC 58,402 stale')
    expect(watchRow(row({ missing: true }))).toBe('BTC  --  no venue lists it')
  })

  it('header has the total and paper PnL; body the wallet, paper and four rows', () => {
    const s = session()
    expect(sessionHeader(s)).toBe('Total $10,412 · PnL +$0.70 (+0.0%)')
    expect(sessionBody(s).split('\n')).toEqual([
      'Wallet 10.5 TON + 1.00 USDT = $10.60',
      'Paper $10,002 · cash $9,750',
      '',
      'BTC 58,402 +1.2% bin',
      'ETH 1,661 -0.5% cb',
      'TON 0.9257 -3.3% okx',
      'SOL  --  no venue lists it',
    ])
    expect(sessionBody(null)).toBe('Connecting to your TapDesk server…')
  })

  it('wallet line covers no address, loading and errors', () => {
    expect(walletLine(null)).toBe('Wallet: add a TON address on the phone')
    expect(walletLine({ address: 'UQAb…x9Zk', ton: null, usdt: null, valueUsd: null, ok: false, error: '' })).toBe('Wallet UQAb…x9Zk · loading…')
    expect(walletLine({ address: 'UQAb…x9Zk', ton: 1, usdt: 0, valueUsd: 0.92, ok: false, error: 'x' })).toContain('(old)')
  })

  it('footer shows the link state and alerts', () => {
    expect(sessionFooter('offline', null)).toBe('Offline · retrying…')
    expect(sessionFooter('online', session({ alerts: [{ id: 1, symbol: 'BTC', op: 'below', level: 57_000 }] }))).toBe(
      'tap positions · hold talk · 1 alert · 2x exit',
    )
  })
})

describe('cards', () => {
  const card: ArrivalCard = {
    kind: 'arrival',
    id: 'arrival:ev1',
    arrivalId: 'ev1',
    symbol: 'USDT',
    amount: 1,
    valueUsd: 1,
    test: false,
    buySymbol: 'BTC',
    fixedUsd: 250,
  }

  it('arrival card offers buy-with-it, a fixed $250 buy and ignore', () => {
    expect(arrivalHeader(card)).toBe('Received 1.00 USDT on TON')
    expect(arrivalHeader({ ...card, test: true })).toBe('Received 1.00 USDT on TON (test)')
    const [withIt, fixed, ignore] = arrivalOptions(card)
    expect(withIt.label).toBe('Paper-buy BTC with it')
    expect(withIt.order).toEqual({ side: 'buy', symbol: 'BTC', arrivalId: 'ev1', cardId: 'arrival:ev1' })
    expect(withIt.confirm).toBe('Paper-buy BTC with 1.00 USDT')
    expect(fixed.label).toBe('Paper-buy $250 BTC')
    expect(fixed.order).toEqual({ side: 'buy', symbol: 'BTC', usd: 250, cardId: 'arrival:ev1' })
    expect(ignore.order).toBeNull()
    expect(arrivalPage(card).list?.items).toEqual(['Paper-buy BTC with it', 'Paper-buy $250 BTC', 'Ignore'])
  })

  it('positions rows and the empty page', () => {
    expect(positionRow({ symbol: 'BTC', qty: 0.0042817, avg: 58_391, price: 58_500, value: 250.5, upnl: 0.47 })).toBe(
      'BTC 0.004282 @58,391 +$0.47',
    )
    expect(positionsPage(session()).list).toBeUndefined()
    expect(positionsPage(session()).texts[0].content).toContain('No paper positions yet.')
  })
})
