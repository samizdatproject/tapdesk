export const DEFAULT_BACKEND = 'http://localhost:8788'

export type Venue = 'binance' | 'bybit' | 'coinbase' | 'okx'

/** Best bid/ask over the venues with a fresh quote; `missing` when no venue has the coin. */
export interface WatchRow {
  symbol: string
  bid: number
  bidVenue: string
  ask: number
  askVenue: string
  mid: number
  change: number | null
  stale: boolean
  missing: boolean
}
export interface Wallet {
  address: string
  ton: number | null
  usdt: number | null
  valueUsd: number | null
  ok: boolean
  error: string
}
export interface Position {
  symbol: string
  qty: number
  avg: number
  price: number | null
  value: number
  upnl: number
}
export interface PaperSummary {
  cash: number
  deposits: number
  realised: number
  equity: number
  pnl: number
  pnlPct: number
  positions: Position[]
}
export interface Alert {
  id: number
  symbol: string
  op: 'above' | 'below'
  level: number
}
export interface Session {
  total: number
  wallet: Wallet | null
  watch: WatchRow[]
  paper: PaperSummary
  alerts: Alert[]
}
export interface ArrivalCard {
  kind: 'arrival'
  id: string
  arrivalId: string
  symbol: string
  amount: number
  valueUsd: number | null
  test: boolean
  buySymbol: string
  fixedUsd: number
}
export interface AlertCard {
  kind: 'alert'
  id: string
  symbol: string
  op: 'above' | 'below'
  level: number
  price: number
}
export type Card = ArrivalCard | AlertCard

export interface Fill {
  side: 'buy' | 'sell'
  symbol: string
  qty: number
  price: number
  venue: string
  usd: number
  credited: number
}
export type OrderResult = { clientMsgId: string } & (
  | { ok: true; fill: Fill; venueName: string }
  | { ok: false; human: string }
)
export type AlertResult = { clientMsgId: string } & ({ ok: true; alert: Alert } | { ok: false; human: string })

/** What a voice command (or a card option) asks for; nothing runs before a tap. */
export type Command =
  | { type: 'order'; side: 'buy' | 'sell'; symbol: string; usd?: number; qty?: number; fraction?: number; label: string }
  | { type: 'alert'; symbol: string; op: 'above' | 'below'; level: number; label: string }
  | { type: 'session'; label: string }
  | { type: 'positions'; label: string }

/** Speech-to-text result; `command` is null when it wasn't understood (`human` says why). */
export interface SttMsg {
  id: string
  text: string
  human: string
  command: Command | null
  examples: string[]
}
export interface ServerError {
  code: string
  human: string
  for?: string
}

/** Server → client. New members also need a case in parseServerMsg. */
export type ServerMsg =
  | ({ type: 'session' } & Session)
  | ({ type: 'card' } & Card)
  | ({ type: 'order_result' } & OrderResult)
  | ({ type: 'alert_result' } & AlertResult)
  | ({ type: 'stt' } & SttMsg)
  | ({ type: 'error' } & ServerError)

export interface OrderRequest {
  side: 'buy' | 'sell'
  symbol: string
  usd?: number
  qty?: number
  fraction?: number
  arrivalId?: string
  cardId?: string
}
/** Client → server. */
export type ClientMsg =
  | { type: 'hello'; token: string }
  | { type: 'open_session' }
  | ({ type: 'confirm_order'; clientMsgId: string } & OrderRequest)
  | { type: 'set_alert'; clientMsgId: string; symbol: string; op: 'above' | 'below'; level: number }
  | { type: 'cancel'; cardId: string }
  | { type: 'audio_begin'; id: string }
  | { type: 'audio_end' }
  | { type: 'audio_cancel' }

/** Phone-screen settings (GET/PUT /settings). */
export interface Settings {
  tonAddress: string
  watchlist: string[]
  alerts: { symbol: string; op: 'above' | 'below'; level: number }[]
}
export interface SettingsView extends Settings {
  wallet: Wallet | null
  paper: { cash: number; equity: number; pnl: number; pnlPct: number }
}

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const numOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const str = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : v == null ? fallback : String(v))
const rec = (v: unknown): Record<string, unknown> | null =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])
const op = (v: unknown): 'above' | 'below' => (v === 'above' ? 'above' : 'below')

function parseWatch(raw: unknown): WatchRow[] {
  const w = rec(raw)
  if (!w || !w.symbol) return []
  return [
    {
      symbol: str(w.symbol),
      bid: num(w.bid),
      bidVenue: str(w.bidVenue),
      ask: num(w.ask),
      askVenue: str(w.askVenue),
      mid: num(w.mid),
      change: numOrNull(w.change),
      stale: w.stale === true,
      missing: w.missing === true || !(num(w.ask) > 0),
    },
  ]
}

function parseWallet(raw: unknown): Wallet | null {
  const w = rec(raw)
  if (!w) return null
  return {
    address: str(w.address),
    ton: numOrNull(w.ton),
    usdt: numOrNull(w.usdt),
    valueUsd: numOrNull(w.valueUsd),
    ok: w.ok === true,
    error: str(w.error),
  }
}

function parseAlert(raw: unknown): Alert[] {
  const a = rec(raw)
  return a ? [{ id: num(a.id), symbol: str(a.symbol), op: op(a.op), level: num(a.level) }] : []
}

function parsePaper(raw: unknown): PaperSummary {
  const p = rec(raw) ?? {}
  return {
    cash: num(p.cash),
    deposits: num(p.deposits),
    realised: num(p.realised),
    equity: num(p.equity),
    pnl: num(p.pnl),
    pnlPct: num(p.pnlPct),
    positions: arr(p.positions).flatMap((x): Position[] => {
      const q = rec(x)
      return q && q.symbol
        ? [{ symbol: str(q.symbol), qty: num(q.qty), avg: num(q.avg), price: numOrNull(q.price), value: num(q.value), upnl: num(q.upnl) }]
        : []
    }),
  }
}

function parseCommand(raw: unknown): Command | null {
  const c = rec(raw)
  if (!c) return null
  const label = str(c.label)
  if (c.type === 'session' || c.type === 'positions') return { type: c.type, label }
  if (c.type === 'alert') return { type: 'alert', symbol: str(c.symbol), op: op(c.op), level: num(c.level), label }
  if (c.type === 'order' && (c.side === 'buy' || c.side === 'sell')) {
    const cmd: Command = { type: 'order', side: c.side, symbol: str(c.symbol), label }
    for (const k of ['usd', 'qty', 'fraction'] as const) if (numOrNull(c[k]) !== null) cmd[k] = num(c[k])
    return cmd
  }
  return null
}

function parseFill(raw: unknown): Fill | null {
  const f = rec(raw)
  if (!f) return null
  return {
    side: f.side === 'sell' ? 'sell' : 'buy',
    symbol: str(f.symbol),
    qty: num(f.qty),
    price: num(f.price),
    venue: str(f.venue),
    usd: num(f.usd),
    credited: num(f.credited),
  }
}

/** Pure: a WebSocket text frame in, a typed message (or null to ignore) out. */
export function parseServerMsg(data: unknown): ServerMsg | null {
  if (typeof data !== 'string') return null
  let msg: Record<string, unknown>
  try {
    const parsed = rec(JSON.parse(data))
    if (!parsed) return null
    msg = parsed
  } catch {
    return null
  }
  switch (msg.type) {
    case 'session':
      return {
        type: 'session',
        total: num(msg.total),
        wallet: parseWallet(msg.wallet),
        watch: arr(msg.watch).flatMap(parseWatch),
        paper: parsePaper(msg.paper),
        alerts: arr(msg.alerts).flatMap(parseAlert),
      }
    case 'card': {
      const id = str(msg.id)
      if (!id) return null
      if (msg.kind === 'arrival') {
        return {
          type: 'card',
          kind: 'arrival',
          id,
          arrivalId: str(msg.arrivalId),
          symbol: str(msg.symbol),
          amount: num(msg.amount),
          valueUsd: numOrNull(msg.valueUsd),
          test: msg.test === true,
          buySymbol: str(msg.buySymbol, 'BTC'),
          fixedUsd: num(msg.fixedUsd) || 250,
        }
      }
      if (msg.kind === 'alert') {
        return { type: 'card', kind: 'alert', id, symbol: str(msg.symbol), op: op(msg.op), level: num(msg.level), price: num(msg.price) }
      }
      return null
    }
    case 'order_result': {
      const clientMsgId = str(msg.clientMsgId)
      const fill = parseFill(msg.fill)
      if (!clientMsgId) return null
      return msg.ok === true && fill
        ? { type: 'order_result', clientMsgId, ok: true, fill, venueName: str(msg.venueName, fill.venue) }
        : { type: 'order_result', clientMsgId, ok: false, human: str(msg.human, 'Not filled') }
    }
    case 'alert_result': {
      const clientMsgId = str(msg.clientMsgId)
      const alert = parseAlert(msg.alert)[0]
      return msg.ok === true && alert
        ? { type: 'alert_result', clientMsgId, ok: true, alert }
        : { type: 'alert_result', clientMsgId, ok: false, human: str(msg.human, 'Alert not set') }
    }
    case 'stt':
      return {
        type: 'stt',
        id: str(msg.id),
        text: str(msg.text).trim(),
        human: str(msg.human),
        command: parseCommand(msg.command),
        examples: arr(msg.examples).map(e => str(e)),
      }
    case 'error': {
      const err: ServerError = { code: str(msg.code), human: str(msg.human, 'Server error') }
      if (typeof msg.for === 'string') err.for = msg.for
      return { type: 'error', ...err }
    }
    default:
      return null
  }
}

/** Reconnect delay for the n-th consecutive failure (0-based): 1, 2, 4, 8, 16, 30, 30… s. */
export function backoffDelay(attempt: number, baseMs = 1000, maxMs = 30_000): number {
  return Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt))
}

/** Trims and validates a backend base URL; throws on anything not http(s). */
export function normaliseBase(input: string): string {
  const url = new URL(input.trim())
  if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new Error('Use an http:// or https:// URL')
  return url.origin + url.pathname.replace(/\/+$/, '')
}

export function wsUrl(base: string): string {
  return base.replace(/^http/, 'ws') + '/ws'
}

// ---- HTTP ----

export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
  }
}

async function request<T>(
  base: string,
  path: string,
  opts: { method?: string; token?: string; body?: unknown } = {},
): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 8000)
  const headers: Record<string, string> = {}
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`
  let res: Response
  try {
    res = await fetch(base + path, {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: ctrl.signal,
    })
  } catch {
    throw new HttpError(0, `Can't reach the TapDesk server at ${base}`)
  } finally {
    clearTimeout(timer)
  }
  if (!res.ok) {
    // FastAPI puts the reason in `detail`; show it when it is plain text.
    const detail = await res.json().then(
      (b: unknown) => (typeof rec(b)?.detail === 'string' ? (rec(b)?.detail as string) : ''),
      () => '',
    )
    throw new HttpError(res.status, detail || `Server said ${res.status} ${res.statusText}`.trim())
  }
  return (await res.json()) as T
}

export const api = {
  healthz: (base: string) => request<{ ok: boolean; tagline: string }>(base, '/healthz'),
  pair: (base: string, code: string) =>
    request<{ token: string }>(base, '/pair', { method: 'POST', body: { code } }),
  getSettings: (base: string, token: string) => request<SettingsView>(base, '/settings', { token }),
  /** 400 with a human `detail` when the server rejects a value. */
  putSettings: (base: string, token: string, body: Partial<Settings> & { resetPaper?: boolean }) =>
    request<{ ok: boolean } & SettingsView>(base, '/settings', { method: 'PUT', token, body }),
}

// ---- WebSocket ----

export type LinkStatus = 'connecting' | 'online' | 'offline'

export interface LinkHandlers {
  message(msg: ServerMsg): void
  status(status: LinkStatus): void
  /** The server revoked this device (close code 4401): stop and pair again. */
  unpaired(): void
}

const CLOSE_UNPAIRED = 4401

/**
 * One live connection to the backend. Every (re)connect sends `hello`, and the
 * server answers with a fresh session and any pending cards, so state is fully
 * resynced each time.
 */
export class Link {
  private ws: WebSocket | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private attempt = 0
  private online = false
  private gen = 0 // bumps on every socket; stale sockets' events are ignored

  constructor(
    private readonly url: string,
    private readonly token: string,
    private readonly on: LinkHandlers,
  ) {}

  start(): void {
    this.stop()
    this.open()
  }

  stop(): void {
    this.gen++
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    this.ws?.close()
    this.ws = null
  }

  send(msg: ClientMsg): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false
    this.ws.send(JSON.stringify(msg))
    return true
  }

  /** Binary frame: raw PCM for a recording in progress. */
  sendBinary(bytes: Uint8Array): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false
    this.ws.send(bytes)
    return true
  }

  private open(): void {
    const gen = ++this.gen
    this.online = false
    if (this.attempt === 0) this.on.status('connecting') // retries stay 'offline'
    let ws: WebSocket
    try {
      ws = new WebSocket(this.url)
    } catch (err) {
      console.warn('[tapdesk] websocket', err)
      this.retry(gen)
      return
    }
    this.ws = ws
    ws.onopen = () => {
      if (gen === this.gen) this.send({ type: 'hello', token: this.token })
    }
    ws.onmessage = ev => {
      if (gen !== this.gen) return
      const msg = parseServerMsg(ev.data)
      if (!msg) return
      // Only a real reply proves the token works; an error-then-close keeps backing off.
      if (msg.type !== 'error' && !this.online) {
        this.online = true
        this.attempt = 0
        this.on.status('online')
      }
      this.on.message(msg)
    }
    ws.onclose = ev => {
      if (gen === this.gen && ev.code === CLOSE_UNPAIRED) {
        this.stop()
        this.on.unpaired()
        return
      }
      this.retry(gen)
    }
  }

  private retry(gen: number): void {
    if (gen !== this.gen) return
    this.gen++
    this.ws = null
    this.on.status('offline')
    const delay = backoffDelay(this.attempt++)
    console.log(`[tapdesk] offline, retrying in ${delay} ms`)
    this.timer = setTimeout(() => this.open(), delay)
  }
}
