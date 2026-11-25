# TapDesk plugin

The Even G2 side of TapDesk: a Vite + TypeScript web app that runs in the Even
Realities App WebView. It draws the glasses screens and also serves the phone
screen (the companion WebView), where you pair with your TapDesk backend, set the
TON address, the watchlist and alerts, and reset the paper account.

## Run it

Requires Node 22.13 or later.

```bash
npm install
npm run dev          # http://localhost:5174
```

In a second terminal, start the simulator. It is not a dependency of this
package; install `@evenrealities/evenhub-simulator` in any folder and run its
entry point (npm does not always link its binary):

```bash
npm install @evenrealities/evenhub-simulator
node node_modules/@evenrealities/evenhub-simulator/bin/index.js --automation-port 9898 http://localhost:5174
```

- The glasses window shows the Intro screen until you pair.
- The webview window is the phone screen. Enter the backend URL (default
  `http://localhost:8788`) and the 6-digit code the backend prints.
- Restart the simulator between runs. In one simulator session
  `createStartUpPageContainer` only succeeds once, and reloading the page does
  not reset it. The plugin logs the result to the console.
- With `--automation-port`, `GET /api/screenshot/glasses` returns a PNG of the
  display, and `POST /api/input {"action":"click"|"double_click"|"up"|"down"|"context_menu"|"long_press"|"long_press_release"}`
  simulates input. `scripts/demo.py` uses both.

Simulator facts the code relies on: a tap arrives with no `eventType` (it is 0,
and protobuf omits zeros); list item 0 arrives with no index; a double-tap on
the root page means exit (the OS asks to confirm); list items are capped at 63
UTF-8 bytes; and the font drops `✓`, emoji and the Unicode minus, so screens use
ASCII signs.

## Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server on port 5174 |
| `npm run build` | Type-check, then build to `dist/` |
| `npm test` | Unit tests (vitest) |
| `npm run pack` | Build and package `tapdesk.ehpk` with the Even Hub CLI |

## Layout

```
src/
  main.ts            wiring: state, backend link, input handling
  bridge.ts          Even bridge + normalise(event) → Input
  net.ts             backend contract: types, parseServerMsg, HTTP helpers, WebSocket with backoff reconnect
  storage.ts         token and backend URL (Even App storage + localStorage)
  state.ts           tiny observable store
  screens/
    manager.ts       the only code that creates, rebuilds, or updates glasses pages
    format.ts        money, price and quantity formatting (ASCII signs, never "0.0000 BTC")
    text.ts          width model for the proportional glasses font
    session.ts       Intro and Session pages (root)
    cards.ts         arrival card and its options, alert card, positions list
    confirm.ts       the 2 s undo window (ConfirmFlow) and its page
    dictate.ts       hold-to-talk state machine (mic, PCM frames, transcript) and its page
    index.ts         the glasses state machine: inputs and server messages → pages, card queue
  companion/         phone screen (plain DOM)
```

## Glasses behaviour

- **Intro** (until paired): the name, "paper trading · public market data · no
  financial advice", and how to pair. Double-tap opens the system exit dialog.
- **Session** (root): total, paper PnL, wallet, paper equity and cash, up to four
  watchlist rows (best ask, 24 h change, venue). Text-only, updated in place, so
  price ticks never rebuild the page. Tap: Positions. Hold: talk. Menu:
  Positions, Voice command, Refresh. Double-tap: exit.
- **Arrival card**: "Received 1.00 USDT on TON" with *Paper-buy BTC with it*,
  *Paper-buy $250 BTC*, *Ignore*. Tapping a buy starts the undo window at once.
  Double-tap ignores the card.
- **Confirm**: 2 s undo (double-tap, or opening the glasses menu, cancels). Then
  "Filled … @ … on <venue>" and back to Session (or the next queued card). A
  refusal shows the reason with *tap retry*; a retry reuses the order id.
- **Alert card**: tap to dismiss.
- **Hold to talk**: release to stop (10 s max). The transcript and the parsed
  command are shown; a tap confirms (then the undo window), a double-tap
  discards. The microphone is off whenever a recording ends or the page changes.
- **Positions**: tap a row to paper-sell all of it (through the undo window).
- Cards queue while a confirm or a recording is on screen and are shown from
  Session or Positions. Cards answered on these glasses are not shown again.
