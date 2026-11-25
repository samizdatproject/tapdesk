import { describe, expect, it } from 'vitest'
import { backoffDelay, normaliseBase, parseServerMsg, wsUrl } from './net'

const parse = (m: unknown) => parseServerMsg(JSON.stringify(m))

describe('urls and backoff', () => {
  it('derives the WebSocket URL and normalises the backend URL', () => {
    expect(wsUrl('http://localhost:8788')).toBe('ws://localhost:8788/ws')
    expect(normaliseBase(' http://localhost:8788/ ')).toBe('http://localhost:8788')
    expect(() => normaliseBase('ftp://x')).toThrow()
  })

  it('doubles from 1 s and caps at 30 s', () => {
    expect([0, 1, 5, 20].map(n => backoffDelay(n))).toEqual([1000, 2000, 30000, 30000])
  })
})

describe('parseServerMsg', () => {
  it('parses a session and marks coins no venue lists', () => {
    const msg = parse({
      type: 'session',
      total: 10_010.5,
      wallet: { address: 'UQAb…x9Zk', ton: 10, usdt: 1, valueUsd: 10.2, ok: true, error: '' },
      watch: [
        { symbol: 'BTC', bid: 1, bidVenue: 'okx', ask: 2, askVenue: 'coinbase', mid: 1.5, change: null, stale: false },
        { symbol: 'ETH', missing: true },
      ],
      paper: { cash: 9750, equity: 10000, pnl: 0, pnlPct: 0, positions: [{ symbol: 'BTC', qty: 0.1, avg: 1, price: null, value: 1, upnl: 0 }] },
      alerts: [{ id: 1, symbol: 'BTC', op: 'above', level: 60000 }],
    })
    expect(msg?.type).toBe('session')
    if (msg?.type !== 'session') return
    expect(msg.watch.map(w => [w.symbol, w.missing])).toEqual([['BTC', false], ['ETH', true]])
    expect(msg.paper.positions[0].price).toBeNull()
    expect(msg.alerts[0].op).toBe('above')
    expect(msg.wallet?.ton).toBe(10)
  })

  it('parses arrival and alert cards, and drops unknown ones', () => {
    const arrival = parse({ type: 'card', kind: 'arrival', id: 'arrival:e', arrivalId: 'e', symbol: 'USDT', amount: 1, valueUsd: 1, test: true, buySymbol: 'BTC', fixedUsd: 250 })
    expect(arrival).toMatchObject({ kind: 'arrival', arrivalId: 'e', test: true, fixedUsd: 250 })
    expect(parse({ type: 'card', kind: 'alert', id: 'alert:1', symbol: 'BTC', op: 'below', level: 57000, price: 56900 })).toMatchObject({ kind: 'alert', price: 56900 })
    expect(parse({ type: 'card', kind: 'weird', id: 'x' })).toBeNull()
    expect(parse({ type: 'card', kind: 'alert' })).toBeNull()
  })

  it('parses order and alert results', () => {
    const fill = { side: 'buy', symbol: 'BTC', qty: 0.01, price: 58391, venue: 'coinbase', usd: 583.91, credited: 0 }
    expect(parse({ type: 'order_result', clientMsgId: 'm', ok: true, fill, venueName: 'Coinbase' })).toMatchObject({ ok: true, venueName: 'Coinbase' })
    expect(parse({ type: 'order_result', clientMsgId: 'm', ok: false, human: 'No' })).toEqual({ type: 'order_result', clientMsgId: 'm', ok: false, human: 'No' })
    expect(parse({ type: 'order_result', ok: true, fill })).toBeNull()
    expect(parse({ type: 'alert_result', clientMsgId: 'a', ok: true, alert: { id: 2, symbol: 'TON', op: 'above', level: 1.8 } })).toMatchObject({ ok: true })
  })

  it('parses stt with its command, keeping only known command fields', () => {
    const msg = parse({ type: 'stt', id: 'd', text: ' buy 100 dollars of TON ', human: '', command: { type: 'order', side: 'buy', symbol: 'TON', usd: 100, label: 'Paper-buy $100 of TON', extra: 1 } })
    expect(msg).toEqual({
      type: 'stt',
      id: 'd',
      text: 'buy 100 dollars of TON',
      human: '',
      command: { type: 'order', side: 'buy', symbol: 'TON', usd: 100, label: 'Paper-buy $100 of TON' },
      examples: [],
    })
    expect(parse({ type: 'stt', id: 'd', text: 'x', human: "Didn't catch that", command: null, examples: ['"pnl"'] })).toMatchObject({ command: null, examples: ['"pnl"'] })
  })

  it('ignores garbage', () => {
    expect(parseServerMsg('not json')).toBeNull()
    expect(parseServerMsg(new Uint8Array([1]))).toBeNull()
    expect(parse({ type: 'nope' })).toBeNull()
    expect(parse({ type: 'error', code: 'failed', human: 'Something went wrong', for: 'confirm_order' })).toEqual({
      type: 'error',
      code: 'failed',
      human: 'Something went wrong',
      for: 'confirm_order',
    })
  })
})
