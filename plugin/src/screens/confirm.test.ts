import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Fill } from '../net'
import { ConfirmFlow, confirmText, doneLine, progressBar, type ConfirmPhase } from './confirm'

const FILL: Fill = { side: 'buy', symbol: 'BTC', qty: 0.0042817, price: 58_391, venue: 'coinbase', usd: 250, credited: 0 }
const ok = (id: string) => ({ clientMsgId: id, ok: true as const, fill: FILL, venueName: 'Coinbase' })

function setup(sendOk = true) {
  const phases: ConfirmPhase[] = []
  const sent: string[] = []
  let n = 0
  const flow = new ConfirmFlow({
    send: id => {
      sent.push(id)
      return sendOk
    },
    onChange: p => phases.push(p),
    newId: () => `id${++n}`,
  })
  return { flow, phases, sent, last: () => phases[phases.length - 1] }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('ConfirmFlow', () => {
  it('counts down 2 s in 500 ms steps, then places once', () => {
    const { flow, phases, sent } = setup()
    flow.start()
    vi.advanceTimersByTime(1999)
    expect(sent).toEqual([])
    vi.advanceTimersByTime(1)
    expect(sent).toEqual(['id1'])
    expect(phases.map(p => (p.phase === 'counting' ? p.leftMs : p.phase))).toEqual([2000, 1500, 1000, 500, 0, 'placing'])
  })

  it('undo during the window means nothing is placed', () => {
    const { flow, sent, last } = setup()
    flow.start()
    vi.advanceTimersByTime(1200)
    expect(flow.cancel()).toBe(true)
    vi.advanceTimersByTime(10_000)
    expect(sent).toEqual([])
    expect(last()).toEqual({ phase: 'cancelled' })
  })

  it('cannot undo once placed, and only its own result counts', () => {
    const { flow, last } = setup()
    flow.start()
    vi.advanceTimersByTime(2000)
    expect(flow.cancel()).toBe(false)
    flow.ack(ok('other'))
    expect(last()).toEqual({ phase: 'placing' })
    flow.ack(ok('id1'))
    expect(last()).toEqual({ phase: 'done', result: ok('id1') })
  })

  it('a refusal can be retried with the same id after a new window', () => {
    const { flow, sent, last } = setup()
    flow.start()
    vi.advanceTimersByTime(2000)
    flow.ack({ clientMsgId: 'id1', ok: false, human: 'No fresh BTC price right now. Try again.' })
    expect(last()).toEqual({ phase: 'failed', human: 'No fresh BTC price right now. Try again.' })
    flow.retry()
    vi.advanceTimersByTime(2000)
    expect(sent).toEqual(['id1', 'id1'])
  })

  it('times out after 8 s, never resends by itself, and a retry reuses the id', () => {
    const { flow, sent, last } = setup()
    flow.start()
    vi.advanceTimersByTime(2000 + 8000)
    expect(last().phase).toBe('failed')
    vi.advanceTimersByTime(60_000)
    expect(sent).toEqual(['id1'])
    flow.retry()
    vi.advanceTimersByTime(2000)
    expect(sent).toEqual(['id1', 'id1']) // the server answers a duplicate with the first fill
  })

  it('a new flow gets a new id', () => {
    let n = 0
    const ids: string[] = []
    for (let i = 0; i < 2; i++) {
      const f = new ConfirmFlow({ send: id => (ids.push(id), true), onChange: () => {}, newId: () => `n${++n}` })
      f.start()
      vi.advanceTimersByTime(2000)
    }
    expect(ids).toEqual(['n1', 'n2'])
  })

  it('fails at once when offline, and stops quietly when disposed', () => {
    const offline = setup(false)
    offline.flow.start()
    vi.advanceTimersByTime(2000)
    expect(offline.last()).toEqual({ phase: 'failed', human: 'Offline. Nothing was placed.' })
    const gone = setup()
    gone.flow.start()
    gone.flow.dispose()
    vi.advanceTimersByTime(10_000)
    expect(gone.sent).toEqual([])
  })
})

describe('confirm text', () => {
  const ctx = { label: 'Paper-buy BTC with 1.00 USDT', hint: 'best ask now 58,391 on Coinbase', undoMs: 2000 }

  it('draws the undo window in ASCII-safe text', () => {
    expect(confirmText(ctx, { phase: 'counting', leftMs: 1000 })).toBe(
      [ctx.label, ctx.hint, '', `${'━'.repeat(12)}${'─'.repeat(12)}  1 s`, '', '2x undo · paper only'].join('\n'),
    )
    expect(progressBar(2000, 2000)).toBe('─'.repeat(24))
  })

  it('shows the fill with its venue, and says it was paper', () => {
    expect(doneLine(ok('x'))).toBe('Filled 0.004282 BTC @ 58,391 on Coinbase')
    expect(confirmText(ctx, { phase: 'done', result: ok('x') })).toBe(
      'Filled 0.004282 BTC @ 58,391 on Coinbase\n\nPaper trade: no real order was sent.',
    )
    expect(doneLine({ clientMsgId: 'a', ok: true, alert: { id: 1, symbol: 'BTC', op: 'below', level: 57_000 } })).toBe(
      'Alert set: BTC below 57,000',
    )
    expect(
      confirmText(ctx, { phase: 'done', result: { clientMsgId: 'a', ok: true, alert: { id: 1, symbol: 'BTC', op: 'below', level: 57_000 } } }),
    ).toBe('Alert set: BTC below 57,000\n\nIt shows here as a card while TapDesk is open.')
    expect(confirmText(ctx, { phase: 'failed', human: 'Not enough paper cash ($10.00)' })).toContain('tap retry · 2x back')
    expect(confirmText(ctx, { phase: 'cancelled' })).toBe('Cancelled. Nothing was placed.')
  })
})
