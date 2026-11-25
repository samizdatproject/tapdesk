import type { ClientMsg, Command, SttMsg } from '../net'
import type { Page } from './manager'
import { wrapText } from './text'

export type DictPhase =
  | { phase: 'starting' }
  | { phase: 'listening'; elapsedMs: number }
  | { phase: 'transcribing' }
  | { phase: 'preview'; text: string; command: Command }
  | { phase: 'empty'; human: string; text?: string; examples?: string[] }
  | { phase: 'cancelled' }

export interface DictationDeps {
  /** Turns the microphone on or off; resolves false if it could not. */
  mic(on: boolean): Promise<boolean>
  send(msg: ClientMsg): boolean
  sendAudio(pcm: Uint8Array): boolean
  onChange(phase: DictPhase): void
  newId?: () => string
  maxMs?: number
  tickMs?: number
  sttTimeoutMs?: number
}

/**
 * One voice command: mic on → audio_begin → PCM frames → audio_end → stt.
 * The invariant that matters: whenever this is not listening, the mic is off.
 */
export class Dictation {
  private state: DictPhase = { phase: 'starting' }
  private micOn = false
  private stopWanted = false // a release that came while the mic was still starting
  private timer: ReturnType<typeof setTimeout> | null = null
  private startedAt = 0
  private id = '' // sent with audio_begin; the matching stt echoes it
  private readonly maxMs: number
  private readonly tickMs: number
  private readonly sttTimeoutMs: number

  constructor(private readonly deps: DictationDeps) {
    this.maxMs = deps.maxMs ?? 10_000
    this.tickMs = deps.tickMs ?? 500
    this.sttTimeoutMs = deps.sttTimeoutMs ?? 20_000
  }

  get phase(): DictPhase {
    return this.state
  }

  get active(): boolean {
    return this.state.phase === 'starting' || this.state.phase === 'listening'
  }

  async start(): Promise<void> {
    this.clear()
    this.stopWanted = false
    this.set({ phase: 'starting' })
    const ok = await this.deps.mic(true).catch(() => false)
    if (this.state.phase !== 'starting' || !ok) {
      // Cancelled while starting, or a start that "failed": the host may still
      // have opened the mic, so always switch it off.
      void this.deps.mic(false).catch(() => false)
      if (!ok && this.state.phase === 'starting') this.set({ phase: 'empty', human: 'The microphone is not available.' })
      return
    }
    this.micOn = true
    this.id = (this.deps.newId ?? (() => crypto.randomUUID()))()
    if (!this.deps.send({ type: 'audio_begin', id: this.id })) {
      this.micOff()
      this.set({ phase: 'empty', human: 'Offline. Try again in a moment.' })
      return
    }
    this.startedAt = Date.now()
    this.set({ phase: 'listening', elapsedMs: 0 })
    if (this.stopWanted) this.stop()
    else this.tick()
  }

  /** PCM from the glasses; forwarded only while listening. */
  frame(pcm: Uint8Array): void {
    if (this.state.phase === 'listening') this.deps.sendAudio(pcm)
  }

  /** Done speaking: send what we have for transcription. */
  stop(): void {
    if (this.state.phase === 'starting') {
      this.stopWanted = true
      return
    }
    if (this.state.phase !== 'listening') return
    this.clear()
    this.micOff()
    this.deps.send({ type: 'audio_end' })
    this.set({ phase: 'transcribing' })
    this.timer = setTimeout(
      () => this.set({ phase: 'empty', human: 'No answer from speech-to-text.' }),
      this.sttTimeoutMs,
    )
  }

  /** Throw the recording away. */
  cancel(): void {
    const wasListening = this.state.phase === 'listening'
    if (!this.active) return
    this.clear()
    this.micOff()
    if (wasListening) this.deps.send({ type: 'audio_cancel' })
    this.set({ phase: 'cancelled' })
  }

  onStt(msg: SttMsg): void {
    // A result for an earlier dictation must not land in this one.
    if (this.state.phase !== 'transcribing' || msg.id !== this.id) return
    this.clear()
    this.set(
      msg.command
        ? { phase: 'preview', text: msg.text, command: msg.command }
        : { phase: 'empty', human: msg.human || "Didn't catch that", text: msg.text, examples: msg.examples },
    )
  }

  /** A server error or a dropped connection: stop and say why. */
  fail(human: string): void {
    if (this.state.phase !== 'transcribing' && !this.active) return
    if (this.state.phase === 'listening') this.deps.send({ type: 'audio_cancel' })
    this.clear()
    this.micOff()
    this.set({ phase: 'empty', human })
  }

  /**
   * Leaving the page, disconnecting or exiting: never leave the mic on.
   * Silent on purpose: the page is already gone, so no onChange (which could
   * schedule UI for a page that no longer exists).
   */
  dispose(): void {
    if (this.state.phase === 'listening') this.deps.send({ type: 'audio_cancel' })
    if (this.active) this.state = { phase: 'cancelled' }
    this.clear()
    this.micOff()
  }

  private tick(): void {
    this.timer = setTimeout(() => {
      if (this.state.phase !== 'listening') return
      const elapsedMs = Date.now() - this.startedAt
      if (elapsedMs >= this.maxMs) {
        this.stop()
        return
      }
      this.set({ phase: 'listening', elapsedMs })
      this.tick()
    }, this.tickMs)
  }

  private micOff(): void {
    if (!this.micOn) return
    this.micOn = false
    void this.deps.mic(false).catch(() => false)
  }

  private clear(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
  }

  private set(phase: DictPhase): void {
    this.state = phase
    this.deps.onChange(phase)
  }
}

const BAR_CELLS = 24
const clock = (ms: number) => {
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function dictateText(phase: DictPhase, route: 'hold' | 'tap', maxMs = 10_000): string {
  switch (phase.phase) {
    case 'starting':
      return 'Starting the microphone…'
    case 'listening': {
      const done = Math.min(BAR_CELLS, Math.round((BAR_CELLS * phase.elapsedMs) / maxMs))
      return [
        `Listening… ${route === 'hold' ? 'release' : 'tap'} to stop  ${clock(phase.elapsedMs)}`,
        `${'━'.repeat(done)}${'─'.repeat(BAR_CELLS - done)} max ${clock(maxMs)}`,
        '',
        'Say: "alert me if BTC drops below 57k"',
        'or "buy 100 dollars of TON"',
        '(audio goes only to your own server)',
        '2x cancel',
      ].join('\n')
    }
    case 'transcribing':
      return 'Transcribing…'
    case 'preview':
      return [...wrapText(`"${phase.text}"`).slice(0, 3), '', `-> ${phase.command.label}`, '', 'tap confirm · 2x discard'].join('\n')
    case 'empty': {
      const heard = phase.text ? wrapText(`"${phase.text}"`).slice(0, 2) : []
      const tries = phase.examples?.length ? ['Try:', ...phase.examples.slice(0, 2)] : []
      return [...heard, ...wrapText(phase.human).slice(0, 2), ...tries, '', 'tap retry · 2x back'].join('\n')
    }
    case 'cancelled':
      return 'Cancelled'
  }
}

export function dictatePage(content: string): Page {
  return { id: 'dictate', texts: [{ name: 'msg', x: 0, y: 0, w: 576, h: 288, content, capture: true }] }
}
