import { AudioInputSource } from '@evenrealities/even_hub_sdk'
import { connectBridge, type Input } from './bridge'
import { mountCompanion } from './companion'
import { DEFAULT_BACKEND, HttpError, Link, api, normaliseBase, wsUrl, type ServerMsg } from './net'
import { Glasses } from './screens'
import { ScreenManager } from './screens/manager'
import { createStore } from './state'
import { KEY_BACKEND, KEY_TOKEN, load, save, useBridgeStorage } from './storage'

const store = createStore({ token: null, base: DEFAULT_BACKEND, link: 'offline', session: null, notice: '' })
let link: Link | null = null
let glasses: Glasses | null = null

const SIGNED_OUT = 'The server signed this device out. Pair again.'

// One handler per server message type.
const onServer: { [K in ServerMsg['type']]: (msg: Extract<ServerMsg, { type: K }>) => void } = {
  session: ({ type: _, ...session }) => store.set({ session }),
  card: ({ type: _, ...card }) => glasses?.onCard(card),
  order_result: ({ type: _, ...result }) => glasses?.onResult(result),
  alert_result: ({ type: _, ...result }) => glasses?.onResult(result),
  stt: ({ type: _, ...stt }) => glasses?.onStt(stt),
  error: ({ type: _, ...err }) => {
    if (err.code === 'unpaired') {
      void unpair(SIGNED_OUT)
      return
    }
    store.set({ notice: err.human })
    glasses?.onError(err)
  },
}

function startLink(): void {
  link?.stop()
  link = null
  const { token, base } = store.get()
  if (!token) return
  link = new Link(wsUrl(base), token, {
    message: msg => (onServer[msg.type] as (m: ServerMsg) => void)(msg),
    status: s => store.set({ link: s }),
    unpaired: () => void unpair(SIGNED_OUT),
  })
  link.start()
}

async function unpair(notice: string): Promise<void> {
  link?.stop()
  link = null
  await save(KEY_TOKEN, null)
  store.set({ token: null, session: null, link: 'offline', notice })
}

/** Runs an authenticated call; a 401 means the token is gone, so drop back to pairing. */
async function authed<T>(call: (base: string, token: string) => Promise<T>): Promise<T> {
  const { base, token } = store.get()
  if (!token) throw new Error('Not paired.')
  try {
    return await call(base, token)
  } catch (err) {
    if (err instanceof HttpError && err.status === 401) await unpair('This device is no longer paired. Pair again.')
    throw err
  }
}

function onInput(input: Input): void {
  if (input.kind === 'EXIT') link?.stop()
  glasses?.input(input)
}

mountCompanion(document.querySelector<HTMLElement>('#app')!, store, {
  async pair(baseInput, code) {
    const base = normaliseBase(baseInput)
    const { token } = await api.pair(base, code)
    if (!token) throw new Error('The server sent no token.')
    await save(KEY_BACKEND, base)
    await save(KEY_TOKEN, token)
    store.set({ token, base, session: null, notice: 'Paired.' })
    startLink()
  },
  unpair: () => unpair('Unpaired. This device no longer has a token.'),
  loadSettings: () => authed(api.getSettings),
  async saveSettings(body) {
    const { ok: _, ...saved } = await authed((base, token) => api.putSettings(base, token, body))
    return saved
  },
})

const bridge = await connectBridge(onInput)
useBridgeStorage(bridge)
const [token, base] = await Promise.all([load(KEY_TOKEN), load(KEY_BACKEND)])
store.set({ token, base: base ?? DEFAULT_BACKEND })

if (bridge) {
  const b = bridge
  glasses = new Glasses(new ScreenManager(b, notice => store.set({ notice })), {
    send: msg => link?.send(msg) ?? false,
    sendAudio: pcm => link?.sendBinary(pcm) ?? false,
    async mic(on) {
      if (!on) return b.audioControl(false).catch(() => false)
      // Glasses mic first; the phone mic if the glasses can't (or in some hosts).
      for (const source of [AudioInputSource.Glasses, AudioInputSource.Phone]) {
        try {
          if (await b.audioControl(true, source)) {
            console.log('[tapdesk] mic on:', source)
            return true
          }
        } catch (err) {
          console.warn('[tapdesk] audioControl failed', source, err)
        }
      }
      return false
    },
  })
  const g = glasses
  g.render(store.get())
  store.subscribe(s => g.render(s))
} else {
  store.set({ notice: 'No glasses connected: phone screen only.' })
}
startLink()
window.addEventListener('beforeunload', () => {
  glasses?.input({ kind: 'EXIT' }) // turns the mic off if a recording is running
  link?.stop()
})
