import type { Input } from '../bridge'
import type { AlertResult, Card, ClientMsg, Command, OrderRequest, OrderResult, ServerError, SttMsg } from '../net'
import type { State } from '../state'
import { arrivalOptions, cardPage, positionsHeader, positionsPage } from './cards'
import { ConfirmFlow, confirmPage, confirmText, type ConfirmContext, type ConfirmPhase } from './confirm'
import { Dictation, dictatePage, dictateText, type DictPhase } from './dictate'
import { price, qty, venueName } from './format'
import type { ScreenManager } from './manager'
import {
  MENU_POSITIONS,
  MENU_REFRESH,
  MENU_TALK,
  introPage,
  sessionBody,
  sessionFooter,
  sessionHeader,
  sessionPage,
} from './session'

const CLICK_AFTER_DOUBLE_MS = 300 // a double-tap can trail a stray single tap
const RESULT_MS = 2500 // how long "Filled …" stays up
const CANCELLED_MS = 1000

type From = 'session' | 'positions' | 'card'

type View =
  | { name: 'intro' }
  | { name: 'session' }
  | { name: 'positions' }
  | { name: 'card'; card: Card }
  | { name: 'confirm'; flow: ConfirmFlow; ctx: ConfirmContext; card: Card | null; from: From }
  | { name: 'dictate'; dict: Dictation; route: 'hold' | 'tap'; from: 'session' | 'positions' }

/** What the glasses controller needs from the outside world. */
export interface GlassesIO {
  send(msg: ClientMsg): boolean
  sendAudio(pcm: Uint8Array): boolean
  /** Microphone on/off; resolves false if it could not be switched on. */
  mic(on: boolean): Promise<boolean>
}

/**
 * The glasses state machine: maps app state, inputs and server messages to pages.
 * Session is the root. Cards (a wallet arrival, a fired alert) queue up and are
 * shown from Session or Positions, never on top of a confirm or a recording.
 */
export class Glasses {
  private view: View = { name: 'intro' }
  private state: State | null = null
  private queue: Card[] = []
  private handled = new Set<string>() // card ids already answered on these glasses
  private lastDoubleAt = -Infinity
  private delay: ReturnType<typeof setTimeout> | null = null

  constructor(
    private readonly screens: ScreenManager,
    private readonly io: GlassesIO,
  ) {}

  get viewName(): View['name'] {
    return this.view.name
  }

  private send(msg: ClientMsg): boolean {
    return this.io.send(msg)
  }

  render(state: State): void {
    this.state = state
    if (!state.token) {
      this.reset()
      if (this.screens.pageId !== 'intro') void this.screens.show(introPage())
      return
    }
    if (this.view.name === 'intro') {
      this.toSession()
      return
    }
    if (this.view.name === 'dictate' && state.link !== 'online') this.view.dict.fail('Connection lost. Not recorded.')
    if (this.view.name === 'session') {
      if (this.screens.pageId !== 'session') void this.screens.show(sessionPage(state.session, state.link))
      this.screens.setText('hdr', sessionHeader(state.session))
      this.screens.setText('body', sessionBody(state.session))
      this.screens.setText('ftr', sessionFooter(state.link, state.session))
    } else if (this.view.name === 'positions') {
      this.screens.setText('hdr', positionsHeader(state.session))
    }
  }

  input(input: Input): void {
    if (input.kind === 'AUDIO') {
      if (this.view.name === 'dictate') this.view.dict.frame(input.pcm)
      return
    }
    const now = Date.now()
    if (input.kind === 'DOUBLE') this.lastDoubleAt = now
    else if ((input.kind === 'CLICK' || input.kind === 'LIST_SELECT') && now - this.lastDoubleAt < CLICK_AFTER_DOUBLE_MS) {
      return
    }
    if (input.kind === 'EXIT') {
      this.reset()
      return
    }
    const view = this.view
    switch (view.name) {
      case 'intro':
        if (input.kind === 'DOUBLE') this.screens.exit()
        break
      case 'session':
        if (input.kind === 'DOUBLE') this.screens.exit() // root page
        else if (input.kind === 'CLICK') this.showPositions()
        else if (input.kind === 'LONG_START') this.startDictation('hold', 'session')
        else if (input.kind === 'MENU') {
          if (input.itemId === MENU_POSITIONS) this.showPositions()
          else if (input.itemId === MENU_TALK) this.startDictation('tap', 'session')
          else if (input.itemId === MENU_REFRESH) this.send({ type: 'open_session' })
        }
        break
      case 'positions':
        if (input.kind === 'DOUBLE') this.toSession()
        else if (input.kind === 'LONG_START') this.startDictation('hold', 'positions')
        else if (input.kind === 'LIST_SELECT') {
          const p = this.state?.session?.paper.positions[input.index]
          if (p) {
            this.startConfirm(
              { label: `Paper-sell all your ${p.symbol} (${qty(p.qty)})`, hint: this.hint(p.symbol, 'sell'), undoMs: 2000 },
              id => this.send({ type: 'confirm_order', clientMsgId: id, side: 'sell', symbol: p.symbol, fraction: 1 }),
              null,
              'positions',
            )
          }
        }
        break
      case 'card': {
        const card = view.card
        if (card.kind === 'alert') {
          if (input.kind === 'CLICK' || input.kind === 'DOUBLE') this.dismiss(card)
        } else if (input.kind === 'DOUBLE') this.dismiss(card)
        else if (input.kind === 'LIST_SELECT') {
          const option = arrivalOptions(card)[input.index]
          if (!option) break
          const order = option.order
          if (!order) this.dismiss(card)
          else {
            this.startConfirm(
              { label: option.confirm, hint: this.hint(order.symbol, order.side), undoMs: 2000 },
              id => this.send({ type: 'confirm_order', clientMsgId: id, ...order }),
              card,
              'card',
            )
          }
        }
        break
      }
      case 'confirm': {
        const phase = view.flow.phase.phase
        if (phase === 'counting' && (input.kind === 'DOUBLE' || input.kind === 'FG_ENTER')) view.flow.cancel()
        else if (phase === 'failed' && input.kind === 'CLICK') view.flow.retry()
        else if (phase === 'failed' && input.kind === 'DOUBLE') this.back(view)
        else if (phase === 'done' && (input.kind === 'CLICK' || input.kind === 'DOUBLE')) this.next()
        break
      }
      case 'dictate': {
        const { dict } = view
        const phase = dict.phase
        if (dict.active) {
          if (input.kind === 'DOUBLE' || input.kind === 'FG_ENTER') dict.cancel()
          else if (view.route === 'hold' ? input.kind === 'LONG_END' : input.kind === 'CLICK') dict.stop()
        } else if (input.kind === 'DOUBLE' && phase.phase !== 'cancelled') this.backFrom(view.from)
        else if (input.kind === 'CLICK' && phase.phase === 'preview') this.runCommand(phase.command, view.from)
        else if (input.kind === 'CLICK' && phase.phase === 'empty') this.startDictation('tap', view.from)
        break
      }
    }
  }

  // ---- server messages ----

  onCard(card: Card): void {
    if (this.handled.has(card.id) || this.queue.some(c => c.id === card.id)) return
    if (this.view.name === 'card' && this.view.card.id === card.id) return
    this.queue.push(card)
    if (this.view.name === 'session' || this.view.name === 'positions') this.next()
  }

  onResult(result: OrderResult | AlertResult): void {
    if (this.view.name === 'confirm') this.view.flow.ack(result)
  }

  onStt(msg: SttMsg): void {
    if (this.view.name === 'dictate') this.view.dict.onStt(msg)
  }

  /** Errors name the request they answer (`for`), so each lands on the flow that sent it. */
  onError(err: ServerError): void {
    const view = this.view
    if (view.name === 'confirm' && (!err.for || err.for === 'confirm_order' || err.for === 'set_alert')) {
      view.flow.fail(err.human)
    } else if (view.name === 'dictate' && (!err.for || err.for.startsWith('audio_'))) {
      view.dict.fail(err.human)
    }
  }

  // ---- transitions ----

  private toSession(): void {
    this.leave()
    this.view = { name: 'session' }
    const s = this.state
    void this.screens.show(sessionPage(s?.session ?? null, s?.link ?? 'connecting'))
  }

  private showPositions(): void {
    this.leave()
    this.view = { name: 'positions' }
    void this.screens.show(positionsPage(this.state?.session ?? null))
  }

  private showCard(card: Card): void {
    this.leave()
    this.view = { name: 'card', card }
    void this.screens.show(cardPage(card))
  }

  /** The next queued card, or Session. */
  private next(): void {
    const card = this.queue[0]
    if (card) this.showCard(card)
    else this.toSession()
  }

  private markHandled(card: Card): void {
    this.handled.add(card.id)
    this.queue = this.queue.filter(c => c.id !== card.id)
  }

  /** "Ignore" / "ok": the server stops offering the card. */
  private dismiss(card: Card): void {
    this.send({ type: 'cancel', cardId: card.id })
    this.markHandled(card)
    this.next()
  }

  private backFrom(from: From, card: Card | null = null): void {
    if (from === 'card' && card) this.showCard(card)
    else if (from === 'positions') this.showPositions()
    else this.next()
  }

  private back(view: Extract<View, { name: 'confirm' }>): void {
    this.backFrom(view.from, view.card)
  }

  /** "best ask now 58,391 on Coinbase", from the latest session; empty if unknown. */
  private hint(symbol: string, side: 'buy' | 'sell'): string {
    const w = this.state?.session?.watch.find(r => r.symbol === symbol && !r.missing)
    if (!w || w.stale) return ''
    return side === 'buy'
      ? `best ask now ${price(w.ask)} on ${venueName(w.askVenue)}`
      : `best bid now ${price(w.bid)} on ${venueName(w.bidVenue)}`
  }

  private startConfirm(ctx: ConfirmContext, send: (clientMsgId: string) => boolean, card: Card | null, from: From): void {
    this.leave()
    const flow: ConfirmFlow = new ConfirmFlow({
      undoMs: ctx.undoMs,
      send,
      onChange: phase => this.onConfirmPhase(flow, phase),
    })
    this.view = { name: 'confirm', flow, ctx, card, from }
    void this.screens.show(confirmPage(confirmText(ctx, flow.phase)))
    flow.start()
  }

  private onConfirmPhase(flow: ConfirmFlow, phase: ConfirmPhase): void {
    const view = this.view
    if (view.name !== 'confirm' || view.flow !== flow) return
    if (phase.phase === 'done') {
      if (view.card) this.markHandled(view.card)
      this.later(() => this.next(), RESULT_MS)
    } else if (phase.phase === 'cancelled') {
      this.later(() => this.back(view), CANCELLED_MS)
    }
    this.screens.setText('msg', confirmText(view.ctx, phase))
  }

  /** A confirmed voice command: orders and alerts go through the undo window. */
  private runCommand(cmd: Command, from: 'session' | 'positions'): void {
    if (cmd.type === 'session') this.toSession()
    else if (cmd.type === 'positions') this.showPositions()
    else if (cmd.type === 'alert') {
      this.startConfirm(
        { label: cmd.label, hint: '', undoMs: 2000 },
        id => this.send({ type: 'set_alert', clientMsgId: id, symbol: cmd.symbol, op: cmd.op, level: cmd.level }),
        null,
        from,
      )
    } else {
      const order: OrderRequest = { side: cmd.side, symbol: cmd.symbol }
      if (cmd.usd !== undefined) order.usd = cmd.usd
      if (cmd.qty !== undefined) order.qty = cmd.qty
      if (cmd.fraction !== undefined) order.fraction = cmd.fraction
      this.startConfirm(
        { label: cmd.label, hint: this.hint(cmd.symbol, cmd.side), undoMs: 2000 },
        id => this.send({ type: 'confirm_order', clientMsgId: id, ...order }),
        null,
        from,
      )
    }
  }

  private startDictation(route: 'hold' | 'tap', from: 'session' | 'positions'): void {
    this.leave()
    const dict: Dictation = new Dictation({
      mic: on => this.io.mic(on),
      send: msg => this.send(msg),
      sendAudio: pcm => this.io.sendAudio(pcm),
      onChange: phase => this.onDictPhase(dict, phase),
    })
    this.view = { name: 'dictate', dict, route, from }
    void this.screens.show(dictatePage(dictateText(dict.phase, route)))
    void dict.start()
  }

  private onDictPhase(dict: Dictation, phase: DictPhase): void {
    const view = this.view
    if (view.name !== 'dictate' || view.dict !== dict) return
    if (phase.phase === 'cancelled') this.later(() => this.backFrom(view.from), CANCELLED_MS)
    this.screens.setText('msg', dictateText(phase, view.route))
  }

  // ---- helpers ----

  private later(fn: () => void, ms: number): void {
    if (this.delay) clearTimeout(this.delay)
    this.delay = setTimeout(() => {
      this.delay = null
      fn()
    }, ms)
  }

  /** Called before switching pages: stop timers, any order in its undo window and the mic. */
  private leave(): void {
    if (this.delay) clearTimeout(this.delay)
    this.delay = null
    if (this.view.name === 'confirm') this.view.flow.dispose()
    if (this.view.name === 'dictate') this.view.dict.dispose() // the mic never outlives its page
  }

  private reset(): void {
    this.leave()
    this.view = { name: 'intro' }
    this.queue = []
  }
}
