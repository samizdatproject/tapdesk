import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AlertCard, ArrivalCard, ClientMsg, Command, Session } from '../net'
import type { State } from '../state'
import { Glasses } from '.'
import type { Page, ScreenManager } from './manager'
import { MENU_TALK } from './session'

const SESSION: Session = {
  total: 10_010,
  wallet: { address: 'UQAb…x9Zk', ton: 10, usdt: 1, valueUsd: 10.2, ok: true, error: '' },
  watch: [
    { symbol: 'BTC', bid: 58_380, bidVenue: 'bybit', ask: 58_391, askVenue: 'coinbase', mid: 58_385, change: 1.2, stale: false, missing: false },
  ],
  paper: {
    cash: 9_750,
    deposits: 0,
    realised: 0,
    equity: 9_999,
    pnl: -1,
    pnlPct: -0.01,
    positions: [{ symbol: 'BTC', qty: 0.0042817, avg: 58_391, price: 58_380, value: 249.95, upnl: -0.05 }],
  },
  alerts: [],
}
const ARRIVAL: ArrivalCard = {
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
const ALERT: AlertCard = { kind: 'alert', id: 'alert:3', symbol: 'BTC', op: 'below', level: 57_000, price: 56_940 }
const FILL = { side: 'buy' as const, symbol: 'BTC', qty: 0.0000171, price: 58_391, venue: 'coinbase', usd: 1, credited: 1 }

const state = (over: Partial<State> = {}): State => ({ token: 't', base: 'http://x', link: 'online', session: SESSION, notice: '', ...over })

function setup() {
  const pages: Page[] = []
  const texts: [string, string][] = []
  const sent: ClientMsg[] = []
  let pageId: string | null = null
  const screens = {
    get pageId() {
      return pageId
    },
    show: (p: Page) => {
      pages.push(p)
      pageId = p.id
      return Promise.resolve(true)
    },
    setText: (name: string, content: string) => texts.push([name, content]),
    exit: vi.fn(),
  } as unknown as ScreenManager
  const mic: boolean[] = []
  const g = new Glasses(screens, {
    send: m => (sent.push(m), true),
    sendAudio: () => true,
    mic: async on => (mic.push(on), true),
  })
  g.render(state())
  return {
    g,
    pages,
    sent,
    mic,
    screens,
    page: () => pages[pages.length - 1],
    text: () => texts[texts.length - 1]?.[1] ?? '',
    lastSent: () => sent[sent.length - 1],
  }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('Glasses', () => {
  it('shows the intro until paired, then Session as the root; double-tap exits', () => {
    const t = setup()
    expect(t.page().id).toBe('session')
    t.g.input({ kind: 'DOUBLE' })
    expect(t.screens.exit).toHaveBeenCalled()
    t.g.render(state({ token: null }))
    expect(t.page().id).toBe('intro')
  })

  it('core moment: arrival card, one tap, 2 s undo, fill with venue, back to Session', () => {
    const t = setup()
    t.g.onCard(ARRIVAL)
    expect(t.page().id).toBe('arrival')
    expect(t.page().list?.items[0]).toBe('Paper-buy BTC with it')

    t.g.input({ kind: 'LIST_SELECT', index: 0 })
    expect(t.page().id).toBe('confirm')
    expect(t.page().texts[0].content).toContain('Paper-buy BTC with 1.00 USDT')
    expect(t.page().texts[0].content).toContain('best ask now 58,391 on Coinbase')
    vi.advanceTimersByTime(1999)
    expect(t.sent.filter(m => m.type === 'confirm_order')).toEqual([])
    vi.advanceTimersByTime(1)
    const order = t.lastSent() as Extract<ClientMsg, { type: 'confirm_order' }>
    expect(order).toMatchObject({ type: 'confirm_order', side: 'buy', symbol: 'BTC', arrivalId: 'ev1', cardId: 'arrival:ev1' })

    t.g.onResult({ clientMsgId: order.clientMsgId, ok: true, fill: FILL, venueName: 'Coinbase' })
    expect(t.text()).toContain('Filled 0.0000171 BTC @ 58,391 on Coinbase')
    vi.advanceTimersByTime(2500)
    expect(t.page().id).toBe('session')
    t.g.onCard(ARRIVAL) // resent on reconnect: already answered here
    expect(t.page().id).toBe('session')
  })

  it('undo goes back to the card and nothing is ordered', () => {
    const t = setup()
    t.g.onCard(ARRIVAL)
    t.g.input({ kind: 'LIST_SELECT', index: 1 })
    vi.advanceTimersByTime(800)
    t.g.input({ kind: 'DOUBLE' })
    vi.advanceTimersByTime(5000)
    expect(t.sent.some(m => m.type === 'confirm_order')).toBe(false)
    expect(t.page().id).toBe('arrival')
  })

  it('Ignore and double-tap dismiss the card on the server', () => {
    const t = setup()
    t.g.onCard(ARRIVAL)
    t.g.input({ kind: 'LIST_SELECT', index: 2 })
    expect(t.lastSent()).toEqual({ type: 'cancel', cardId: 'arrival:ev1' })
    expect(t.page().id).toBe('session')
    t.g.onCard({ ...ARRIVAL, id: 'arrival:ev2', arrivalId: 'ev2' })
    vi.advanceTimersByTime(400)
    t.g.input({ kind: 'DOUBLE' })
    expect(t.lastSent()).toEqual({ type: 'cancel', cardId: 'arrival:ev2' })
  })

  it('cards wait while a confirm is running, then show in order', () => {
    const t = setup()
    t.g.onCard(ARRIVAL)
    t.g.input({ kind: 'LIST_SELECT', index: 1 })
    t.g.onCard(ALERT)
    expect(t.page().id).toBe('confirm')
    vi.advanceTimersByTime(2000)
    const order = t.lastSent() as Extract<ClientMsg, { type: 'confirm_order' }>
    t.g.onResult({ clientMsgId: order.clientMsgId, ok: true, fill: { ...FILL, usd: 250 }, venueName: 'Coinbase' })
    vi.advanceTimersByTime(2500)
    expect(t.page().id).toBe('alert')
    expect(t.page().texts[0].content).toContain('ALERT  BTC below 57,000')
    t.g.input({ kind: 'CLICK' })
    expect(t.lastSent()).toEqual({ type: 'cancel', cardId: 'alert:3' })
    expect(t.page().id).toBe('session')
  })

  it('a refused order shows why and retries with the same id', () => {
    const t = setup()
    t.g.onCard(ARRIVAL)
    t.g.input({ kind: 'LIST_SELECT', index: 0 })
    vi.advanceTimersByTime(2000)
    const first = t.lastSent() as Extract<ClientMsg, { type: 'confirm_order' }>
    t.g.onResult({ clientMsgId: first.clientMsgId, ok: false, human: 'No fresh BTC price right now. Try again.' })
    expect(t.text()).toContain('No fresh BTC price')
    t.g.input({ kind: 'CLICK' })
    vi.advanceTimersByTime(2000)
    const second = t.lastSent() as Extract<ClientMsg, { type: 'confirm_order' }>
    expect(second.clientMsgId).toBe(first.clientMsgId)
  })

  it('positions: tap a row to sell all through the undo window', () => {
    const t = setup()
    t.g.input({ kind: 'CLICK' })
    expect(t.page().id).toBe('positions')
    expect(t.page().list?.items).toEqual(['BTC 0.004282 @58,391 -$0.05'])
    vi.advanceTimersByTime(400)
    t.g.input({ kind: 'LIST_SELECT', index: 0 })
    expect(t.page().texts[0].content).toContain('Paper-sell all your BTC (0.004282)')
    expect(t.page().texts[0].content).toContain('best bid now 58,380 on Bybit')
    vi.advanceTimersByTime(2000)
    expect(t.lastSent()).toMatchObject({ type: 'confirm_order', side: 'sell', symbol: 'BTC', fraction: 1 })
  })

  it('hold to talk: transcript and parsed command, then a tap confirms through the undo window', async () => {
    const t = setup()
    t.g.input({ kind: 'LONG_START' })
    await vi.advanceTimersByTimeAsync(0)
    const begin = t.sent.find(m => m.type === 'audio_begin') as { id: string }
    expect(t.mic).toEqual([true])
    t.g.input({ kind: 'LONG_END' })
    expect(t.mic).toEqual([true, false])
    expect(t.lastSent()).toEqual({ type: 'audio_end' })
    const cmd: Command = { type: 'alert', symbol: 'BTC', op: 'below', level: 57_000, label: 'Alert: BTC below 57,000' }
    t.g.onStt({ id: begin.id, text: 'Alert me if BTC drops below 57K.', human: '', command: cmd, examples: [] })
    expect(t.text()).toContain('-> Alert: BTC below 57,000')
    expect(t.sent.some(m => m.type === 'set_alert')).toBe(false) // nothing runs before the tap
    t.g.input({ kind: 'CLICK' })
    vi.advanceTimersByTime(2000)
    expect(t.lastSent()).toMatchObject({ type: 'set_alert', symbol: 'BTC', op: 'below', level: 57_000 })
    const id = (t.lastSent() as { clientMsgId: string }).clientMsgId
    t.g.onResult({ clientMsgId: id, ok: true, alert: { id: 1, symbol: 'BTC', op: 'below', level: 57_000 } })
    expect(t.text()).toContain('Alert set: BTC below 57,000')
  })

  it('"how am I doing" opens Session after a tap; unknown speech offers a retry', async () => {
    const t = setup()
    t.g.input({ kind: 'MENU', itemId: MENU_TALK })
    await vi.advanceTimersByTimeAsync(0)
    let begin = t.sent.filter(m => m.type === 'audio_begin').pop() as { id: string }
    t.g.input({ kind: 'CLICK' })
    t.g.onStt({ id: begin.id, text: 'la la', human: "Didn't catch that", command: null, examples: ['"pnl"'] })
    expect(t.text()).toContain("Didn't catch that")
    t.g.input({ kind: 'CLICK' }) // retry
    await vi.advanceTimersByTimeAsync(0)
    begin = t.sent.filter(m => m.type === 'audio_begin').pop() as { id: string }
    t.g.input({ kind: 'CLICK' })
    t.g.onStt({ id: begin.id, text: 'how am I doing', human: '', command: { type: 'session', label: 'Open session' }, examples: [] })
    expect(t.page().id).toBe('dictate')
    expect(t.text()).toContain('-> Open session')
    t.g.input({ kind: 'CLICK' })
    expect(t.page().id).toBe('session')
  })

  it('updates Session text in place on every price tick', () => {
    const t = setup()
    const shown = t.pages.length
    t.g.render(state({ session: { ...SESSION, total: 10_500 } }))
    expect(t.pages.length).toBe(shown) // no rebuild
  })
})
