import type { AlertResult, OrderResult } from '../net'
import { price, qty, venueName } from './format'
import type { Page } from './manager'
import { LINE_UNITS, fitWidth, wrapText } from './text'

export type Result = OrderResult | AlertResult

export type ConfirmPhase =
  | { phase: 'counting'; leftMs: number }
  | { phase: 'placing' }
  | { phase: 'done'; result: Extract<Result, { ok: true }> }
  | { phase: 'failed'; human: string }
  | { phase: 'cancelled' }

export interface ConfirmFlowOptions {
  /** Called once the undo window has passed; returns false if it could not go out. */
  send(clientMsgId: string): boolean
  onChange(phase: ConfirmPhase): void
  newId?: () => string
  undoMs?: number
  tickMs?: number
  timeoutMs?: number
}

/**
 * The undo window: count down, then send once, then wait for the result. Undo is
 * possible only while counting. Never resends by itself; `retry()` re-runs the
 * window with the SAME clientMsgId, so the server answers a duplicate with the
 * first fill instead of filling twice. A new choice by the user gets a new id.
 */
export class ConfirmFlow {
  private clientMsgId = ''
  private state: ConfirmPhase
  private timer: ReturnType<typeof setTimeout> | null = null
  private readonly undoMs: number
  private readonly tickMs: number
  private readonly timeoutMs: number

  constructor(private readonly opts: ConfirmFlowOptions) {
    this.undoMs = opts.undoMs ?? 2000
    this.tickMs = opts.tickMs ?? 500
    this.timeoutMs = opts.timeoutMs ?? 8000
    this.state = { phase: 'counting', leftMs: this.undoMs }
  }

  get phase(): ConfirmPhase {
    return this.state
  }

  get total(): number {
    return this.undoMs
  }

  start(): void {
    this.set({ phase: 'counting', leftMs: this.undoMs })
    this.tick()
  }

  /** Undo. True if it was stopped in time. */
  cancel(): boolean {
    if (this.state.phase !== 'counting') return false
    this.clear()
    this.set({ phase: 'cancelled' })
    return true
  }

  ack(result: Result): void {
    if (result.clientMsgId !== this.clientMsgId || this.state.phase !== 'placing') return
    this.clear()
    this.set(result.ok ? { phase: 'done', result } : { phase: 'failed', human: result.human })
  }

  /** A server error while waiting fails this attempt. */
  fail(human: string): void {
    if (this.state.phase !== 'placing') return
    this.clear()
    this.set({ phase: 'failed', human })
  }

  retry(): void {
    if (this.state.phase === 'failed') this.start()
  }

  dispose(): void {
    this.clear()
  }

  private tick(): void {
    if (this.state.phase !== 'counting') return
    const left = this.state.leftMs
    if (left <= 0) {
      this.fire()
      return
    }
    this.timer = setTimeout(() => {
      this.set({ phase: 'counting', leftMs: Math.max(0, left - this.tickMs) })
      this.tick()
    }, this.tickMs)
  }

  private fire(): void {
    if (!this.clientMsgId) this.clientMsgId = (this.opts.newId ?? (() => crypto.randomUUID()))()
    this.set({ phase: 'placing' })
    if (!this.opts.send(this.clientMsgId)) {
      this.set({ phase: 'failed', human: 'Offline. Nothing was placed.' })
      return
    }
    this.timer = setTimeout(
      () => this.set({ phase: 'failed', human: 'No answer from the server. A retry is safe: it uses the same order id.' }),
      this.timeoutMs,
    )
  }

  private clear(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
  }

  private set(phase: ConfirmPhase): void {
    this.state = phase
    this.opts.onChange(phase)
  }
}

const BAR_CELLS = 24

export function progressBar(leftMs: number, totalMs: number, cells = BAR_CELLS): string {
  const done = Math.round(cells * Math.min(1, Math.max(0, 1 - leftMs / totalMs)))
  return '━'.repeat(done) + '─'.repeat(cells - done)
}

export interface ConfirmContext {
  /** What will happen, e.g. "Paper-buy BTC with 1.00 USDT". */
  label: string
  /** Indicative price line, e.g. "best ask now 58,391 on Coinbase"; may be empty. */
  hint: string
  undoMs: number
}

/** "Filled 0.0043 BTC @ 58,391 on Coinbase" or "Alert set: BTC below 57,000". */
export function doneLine(result: Extract<Result, { ok: true }>): string {
  if ('fill' in result) {
    const f = result.fill
    const verb = f.side === 'buy' ? 'Filled' : 'Sold'
    return `${verb} ${qty(f.qty)} ${f.symbol} @ ${price(f.price)} on ${result.venueName || venueName(f.venue)}`
  }
  const a = result.alert
  return `Alert set: ${a.symbol} ${a.op} ${price(a.level)}`
}

export function confirmText(ctx: ConfirmContext, phase: ConfirmPhase): string {
  const head = [fitWidth(ctx.label, LINE_UNITS), ...(ctx.hint ? [fitWidth(ctx.hint, LINE_UNITS)] : []), '']
  switch (phase.phase) {
    case 'counting':
      return [...head, `${progressBar(phase.leftMs, ctx.undoMs)}  ${Math.ceil(phase.leftMs / 1000)} s`, '', '2x undo · paper only'].join('\n')
    case 'placing':
      return [...head, 'Placing…'].join('\n')
    case 'done':
      return [
        ...wrapText(doneLine(phase.result)).slice(0, 2),
        '',
        'fill' in phase.result ? 'Paper trade: no real order was sent.' : 'It shows here as a card while TapDesk is open.',
      ].join('\n')
    case 'failed':
      return [...head, ...wrapText(phase.human).slice(0, 2), '', 'tap retry · 2x back'].join('\n')
    case 'cancelled':
      return 'Cancelled. Nothing was placed.'
  }
}

export function confirmPage(content: string): Page {
  return { id: 'confirm', texts: [{ name: 'msg', x: 0, y: 0, w: 576, h: 288, content, capture: true }] }
}
