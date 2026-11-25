import { HttpError, type Settings, type SettingsView } from '../net'
import type { State, Store } from '../state'
import { el, message } from './dom'
import { addAlert, addSymbol, tonAddressError, type AlertRow } from './settings'
import './style.css'

export interface CompanionActions {
  pair(base: string, code: string): Promise<void>
  unpair(): Promise<void>
  loadSettings(): Promise<SettingsView>
  saveSettings(body: Partial<Settings> & { resetPaper?: boolean }): Promise<SettingsView>
}

const LINK_LABEL = { online: 'Online', connecting: 'Connecting…', offline: 'Offline · retrying' }

const money = (x: number) => `${x < 0 ? '-' : ''}$${Math.abs(x).toLocaleString('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`

/** The phone screen: pairing, wallet, watchlist, alerts, paper account. Text only ever goes in via textContent. */
export function mountCompanion(root: HTMLElement, store: Store, actions: CompanionActions): void {
  const status = el('p', { className: 'status', role: 'status' })
  const chip = el('span', { className: 'chip' })
  const say = (text: string) => store.set({ notice: text })

  // ---- pair form ----
  const baseInput = el('input', { type: 'url', id: 'base', autocomplete: 'off', spellcheck: false, value: store.get().base })
  const codeInput = el('input', {
    type: 'text',
    id: 'code',
    inputMode: 'numeric',
    autocomplete: 'one-time-code',
    maxLength: 6,
    pattern: '[0-9]{6}',
    placeholder: '123456',
  })
  const pairButton = el('button', { type: 'submit', className: 'primary' }, 'Pair')
  const pairForm = el(
    'form',
    { className: 'card' },
    el('h2', {}, 'Pair'),
    el('p', { className: 'hint' }, 'Start the TapDesk backend, then type the 6-digit code it prints.'),
    el('label', { htmlFor: 'base' }, 'Backend URL'),
    baseInput,
    el('label', { htmlFor: 'code' }, 'Pairing code'),
    codeInput,
    pairButton,
  )

  // ---- settings model (saved together) ----
  let model: Settings | null = null
  let alertsEdited = false // only an edited list is sent, so alerts set by voice meanwhile survive a Save
  const fieldError = () => el('p', { className: 'error', role: 'alert', hidden: true })
  const show = (node: HTMLElement, text: string | null) => {
    node.textContent = text ?? ''
    node.hidden = !text
  }

  // wallet
  const tonInput = el('input', { type: 'text', id: 'ton', autocomplete: 'off', spellcheck: false, placeholder: 'UQ… or EQ…' })
  const tonError = fieldError()
  const walletStatus = el('p', { className: 'hint stat' })
  const walletCard = el(
    'section',
    { className: 'card' },
    el('h2', {}, 'TON wallet (read-only)'),
    el('p', { className: 'hint' }, 'The address to watch. TapDesk only reads public chain data: it never holds keys and never sends anything.'),
    el('label', { htmlFor: 'ton' }, 'TON address'),
    tonInput,
    tonError,
    walletStatus,
  )
  tonInput.addEventListener('input', () => show(tonError, tonAddressError(tonInput.value)))

  // watchlist
  const chips = el('div', { className: 'chips' })
  const symbolInput = el('input', { type: 'text', placeholder: 'e.g. DOGE', autocomplete: 'off', spellcheck: false })
  symbolInput.setAttribute('aria-label', 'Add a coin to the watchlist')
  const addSymbolButton = el('button', { type: 'button' }, 'Add')
  const watchError = fieldError()
  const watchCard = el(
    'section',
    { className: 'card' },
    el('h2', {}, 'Watchlist'),
    el('p', { className: 'hint' }, 'Best price across Binance, Bybit, Coinbase and OKX. The first four show on the glasses.'),
    chips,
    el('div', { className: 'add' }, symbolInput, addSymbolButton),
    watchError,
  )
  function renderChips(): void {
    chips.replaceChildren()
    for (const symbol of model?.watchlist ?? []) {
      const remove = el('button', { type: 'button', className: 'chip-x', textContent: '×' })
      remove.setAttribute('aria-label', `Remove ${symbol}`)
      remove.addEventListener('click', () => {
        if (!model || model.watchlist.length <= 1) return show(watchError, 'Watch at least one coin.')
        model = { ...model, watchlist: model.watchlist.filter(s => s !== symbol) }
        show(watchError, null)
        renderChips()
      })
      chips.append(el('span', { className: 'tag' }, el('span', { textContent: symbol }), remove))
    }
  }
  const doAddSymbol = () => {
    if (!model) return
    const added = addSymbol(model.watchlist, symbolInput.value)
    show(watchError, added.error)
    if (added.error) return
    model = { ...model, watchlist: added.list }
    symbolInput.value = ''
    renderChips()
  }
  addSymbolButton.addEventListener('click', doAddSymbol)
  symbolInput.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') doAddSymbol()
  })

  // alerts
  const alertList = el('ul', { className: 'privacy' })
  const alertSymbol = el('input', { type: 'text', placeholder: 'BTC', autocomplete: 'off', spellcheck: false })
  alertSymbol.setAttribute('aria-label', 'Alert coin')
  const alertOp = el('select', {}, el('option', { value: 'below' }, 'below'), el('option', { value: 'above' }, 'above'))
  alertOp.setAttribute('aria-label', 'Above or below')
  const alertLevel = el('input', { type: 'text', inputMode: 'decimal', placeholder: '57000', autocomplete: 'off' })
  alertLevel.setAttribute('aria-label', 'Alert level')
  const addAlertButton = el('button', { type: 'button' }, 'Add')
  const alertError = fieldError()
  const alertsCard = el(
    'section',
    { className: 'card' },
    el('h2', {}, 'Price alerts'),
    el('p', { className: 'hint' }, 'Each fires once, as a card on the glasses. Cards that fire while the app is closed show when you open it.'),
    alertList,
    el('div', { className: 'alert-add' }, alertSymbol, alertOp, alertLevel, addAlertButton),
    alertError,
  )
  function renderAlerts(): void {
    alertList.replaceChildren()
    const alerts = model?.alerts ?? []
    if (alerts.length === 0) alertList.append(el('li', { className: 'hint' }, 'No alerts. You can also say "alert me if BTC drops below 57k".'))
    for (const a of alerts) {
      const remove = el('button', { type: 'button', className: 'chip-x', textContent: '×' })
      remove.setAttribute('aria-label', `Remove alert ${a.symbol} ${a.op} ${a.level}`)
      remove.addEventListener('click', () => {
        if (!model) return
        model = { ...model, alerts: model.alerts.filter(x => x !== a) }
        alertsEdited = true
        renderAlerts()
      })
      alertList.append(el('li', {}, el('span', { textContent: `${a.symbol} ${a.op} ${a.level.toLocaleString('en-US')}` }), remove))
    }
  }
  addAlertButton.addEventListener('click', () => {
    if (!model) return
    const added = addAlert(model.alerts, { symbol: alertSymbol.value, op: alertOp.value, level: alertLevel.value })
    show(alertError, added.error)
    if (added.error) return
    model = { ...model, alerts: added.list as AlertRow[] }
    alertsEdited = true
    alertSymbol.value = ''
    alertLevel.value = ''
    renderAlerts()
  })

  const saveButton = el('button', { type: 'button', className: 'primary' }, 'Save')
  const saveError = fieldError()
  const saveRow = el('section', { className: 'stack' }, el('div', { className: 'row' }, saveButton), saveError)

  // paper account
  const paperStatus = el('p', { className: 'hint stat' })
  const resetButton = el('button', { type: 'button', className: 'danger' }, 'Reset paper account')
  const resetHint = el('p', { className: 'hint', hidden: true })
  const paperCard = el(
    'section',
    { className: 'card' },
    el('h2', {}, 'Paper account'),
    el('p', { className: 'hint' }, 'Starts with 10,000 paper USDT. Orders fill at the best price across the venues. Nothing is ever sent to an exchange.'),
    paperStatus,
    el('div', { className: 'row start' }, resetButton),
    resetHint,
  )

  const pairedWith = el('p', { className: 'hint' })
  const unpairButton = el('button', { type: 'button', className: 'danger' }, 'Unpair')
  const deviceCard = el('section', { className: 'card' }, el('h2', {}, 'This device'), pairedWith, el('div', { className: 'row start' }, unpairButton))

  const pairedView = el('div', { className: 'stack' }, walletCard, watchCard, alertsCard, saveRow, paperCard, deviceCard)
  const about = el(
    'section',
    { className: 'card' },
    el('h2', {}, 'About'),
    el(
      'ul',
      { className: 'privacy' },
      ...[
        'Paper trading only. No real orders, no user funds, no API keys.',
        'Unofficial; not affiliated with Binance, Bybit, Coinbase, OKX, TON or any wallet.',
        'Prices are public market data and may be delayed or wrong. Nothing here is financial advice.',
        'Voice commands go only to your own server; nothing runs before you tap.',
        'G2 cannot show cards while the app is closed; they wait until you open it.',
      ].map(line => el('li', {}, line)),
    ),
  )

  root.replaceChildren(
    el(
      'main',
      { className: 'panel' },
      el(
        'header',
        {},
        el('div', {}, el('h1', {}, 'TapDesk'), el('p', { className: 'tagline' }, 'Your TON wallet and the best price across exchanges, at a glance.')),
        chip,
      ),
      el('p', { className: 'legal' }, 'Paper trading · public market data · unofficial · no financial advice'),
      status,
      pairForm,
      pairedView,
      about,
    ),
  )

  async function busy(button: HTMLButtonElement, work: () => Promise<void>): Promise<void> {
    button.disabled = true
    try {
      await work()
    } catch (err) {
      say(message(err))
    } finally {
      button.disabled = false
    }
  }

  function apply(view: SettingsView): void {
    model = { tonAddress: view.tonAddress, watchlist: [...view.watchlist], alerts: view.alerts.map(a => ({ symbol: a.symbol, op: a.op, level: a.level })) }
    alertsEdited = false
    tonInput.value = view.tonAddress
    show(tonError, null)
    const w = view.wallet
    walletStatus.textContent = !w
      ? 'Not watching an address yet.'
      : w.ton === null
        ? `Watching ${w.address} · ${w.error || 'loading…'}`
        : `Watching ${w.address} · ${w.ton.toLocaleString('en-US', { maximumFractionDigits: 4 })} TON + ${(w.usdt ?? 0).toFixed(2)} USDT${w.valueUsd === null ? '' : ` ≈ ${money(w.valueUsd)}`}`
    paperStatus.textContent = `Cash ${money(view.paper.cash)} · equity ${money(view.paper.equity)} · PnL ${view.paper.pnl >= 0 ? '+' : ''}${money(view.paper.pnl)}`
    renderChips()
    renderAlerts()
  }

  let loaded: 'idle' | 'loading' | 'loaded' | 'failed' = 'idle'
  async function load(): Promise<void> {
    loaded = 'loading'
    try {
      apply(await actions.loadSettings())
      loaded = 'loaded'
    } catch (err) {
      loaded = 'failed'
      throw err
    }
  }

  pairForm.addEventListener('submit', ev => {
    ev.preventDefault()
    const code = codeInput.value.trim()
    if (!/^\d{6}$/.test(code)) {
      say('The pairing code is 6 digits.')
      return
    }
    void busy(pairButton, async () => {
      say('Pairing…')
      try {
        await actions.pair(baseInput.value, code)
      } catch (err) {
        if (err instanceof HttpError && err.status === 401) throw new Error('Wrong or expired code. Get a new one from the backend.')
        throw err
      }
      codeInput.value = ''
    })
  })

  saveButton.addEventListener('click', () =>
    void busy(saveButton, async () => {
      if (!model) return
      const addressError = tonAddressError(tonInput.value)
      show(tonError, addressError)
      if (addressError) return
      show(saveError, null)
      try {
        const { alerts, ...rest } = model
        apply(await actions.saveSettings({ ...rest, tonAddress: tonInput.value.trim(), ...(alertsEdited ? { alerts } : {}) }))
      } catch (err) {
        if (err instanceof HttpError && err.status === 400) return show(saveError, err.message)
        throw err
      }
      say('Saved.')
    }),
  )

  // Destructive: the first tap only arms it for 5 s.
  let armed: ReturnType<typeof setTimeout> | null = null
  const disarm = () => {
    if (armed) clearTimeout(armed)
    armed = null
    resetHint.hidden = true
    resetButton.textContent = 'Reset paper account'
  }
  resetButton.addEventListener('click', () => {
    if (!armed) {
      resetHint.textContent = 'This clears all paper positions and PnL and starts again at 10,000 paper USDT. Tap again to confirm.'
      resetHint.hidden = false
      resetButton.textContent = 'Tap again to confirm'
      armed = setTimeout(disarm, 5000)
      return
    }
    disarm()
    void busy(resetButton, async () => {
      apply(await actions.saveSettings({ resetPaper: true }))
      say('Paper account reset to 10,000 USDT.')
    })
  })

  unpairButton.addEventListener('click', () => void busy(unpairButton, actions.unpair))

  let wasPaired: boolean | null = null
  let wasOnline: boolean | null = null
  let shownBase = ''
  function render(state: State): void {
    const paired = state.token !== null
    pairForm.hidden = paired
    pairedView.hidden = !paired
    chip.hidden = !paired
    chip.textContent = LINK_LABEL[state.link]
    chip.className = `chip chip-${state.link}`
    status.textContent = state.notice
    pairedWith.textContent = `Paired with ${state.base}`
    if (state.base !== shownBase) baseInput.value = shownBase = state.base
    // Load on pairing, and again once the server is back if that failed.
    const retry = state.link === 'online' && wasOnline === false && loaded === 'failed'
    if (paired && (wasPaired !== true || retry)) void load().catch(err => say(message(err)))
    wasPaired = paired
    wasOnline = state.link === 'online'
  }

  store.subscribe(render)
  render(store.get())

  // Local development only: http://localhost:5174/?pair=123456 fills in and submits the pairing form, so a
  // simulator can be paired from a script. The code is the backend's single-use 6-digit code (5-minute TTL).
  const devCode = new URLSearchParams(window.location.search).get('pair')
  if (devCode && /^\d{6}$/.test(devCode) && store.get().token === null && /^(localhost|127\.0\.0\.1)$/.test(window.location.hostname)) {
    codeInput.value = devCode
    pairForm.requestSubmit()
  }
}
