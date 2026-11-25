import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ClientMsg, Command } from '../net'
import { Dictation, dictateText, type DictPhase } from './dictate'

const BUY: Command = { type: 'order', side: 'buy', symbol: 'TON', usd: 100, label: 'Paper-buy $100 of TON' }

function setup(micOk = true, sendOk = true) {
  const sent: ClientMsg['type'][] = []
  const audio: Uint8Array[] = []
  const mic: boolean[] = []
  const phases: DictPhase[] = []
  const d = new Dictation({
    mic: async on => (mic.push(on), on ? micOk : true),
    send: m => (sent.push(m.type), sendOk),
    sendAudio: pcm => (audio.push(pcm), true),
    onChange: p => phases.push(p),
    newId: () => 'd1',
  })
  const micIsOn = () => mic.filter(Boolean).length > mic.filter(x => !x).length
  return { d, sent, audio, mic, phases, micIsOn, last: () => phases[phases.length - 1] }
}
const pcm = new Uint8Array([1, 2, 3, 4])

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe('Dictation', () => {
  it('mic on, audio_begin, frames, then mic off and audio_end on stop', async () => {
    const t = setup()
    t.d.frame(pcm) // before start: dropped
    await t.d.start()
    expect(t.sent).toEqual(['audio_begin'])
    expect(t.micIsOn()).toBe(true)
    t.d.frame(pcm)
    t.d.frame(pcm)
    t.d.stop()
    t.d.frame(pcm) // after stop: dropped
    expect(t.audio).toHaveLength(2)
    expect(t.sent).toEqual(['audio_begin', 'audio_end'])
    expect(t.micIsOn()).toBe(false)
    expect(t.last()).toEqual({ phase: 'transcribing' })
  })

  it('previews the transcript, or explains an empty one', async () => {
    const t = setup()
    await t.d.start()
    t.d.stop()
    t.d.onStt({ id: 'd1', text: 'buy 100 dollars of TON', human: '', command: BUY, examples: [] })
    expect(t.last()).toEqual({ phase: 'preview', text: 'buy 100 dollars of TON', command: BUY })
    const u = setup()
    await u.d.start()
    u.d.stop()
    u.d.onStt({ id: 'd1', text: '', human: 'Too short. Hold while you speak.', command: null, examples: [] })
    expect(u.last()).toEqual({ phase: 'empty', human: 'Too short. Hold while you speak.', text: '', examples: [] })
  })

  it('ignores a transcript from an earlier dictation', async () => {
    let n = 0
    const phases: DictPhase[] = []
    const begins: string[] = []
    const d = new Dictation({
      mic: async () => true,
      send: m => (m.type === 'audio_begin' && begins.push(m.id), true),
      sendAudio: () => true,
      onChange: p => phases.push(p),
      newId: () => `d${++n}`,
    })
    await d.start()
    d.stop()
    await d.start() // the user retried before the first result came back
    d.stop()
    expect(begins).toEqual(['d1', 'd2'])
    d.onStt({ id: 'd1', text: 'old words', human: '', command: BUY, examples: [] })
    expect(d.phase).toEqual({ phase: 'transcribing' })
    d.onStt({ id: 'd2', text: 'new words', human: '', command: BUY, examples: [] })
    expect(d.phase).toEqual({ phase: 'preview', text: 'new words', command: BUY })
  })

  it('dispose is silent: it schedules no page change', async () => {
    const t = setup()
    await t.d.start()
    const before = t.phases.length
    t.d.dispose()
    expect(t.phases.length).toBe(before)
    expect(t.micIsOn()).toBe(false)
  })

  it('cancel (double-tap or menu) turns the mic off and sends audio_cancel', async () => {
    const t = setup()
    await t.d.start()
    t.d.frame(pcm)
    t.d.cancel()
    t.d.frame(pcm)
    expect(t.sent).toEqual(['audio_begin', 'audio_cancel'])
    expect(t.audio).toHaveLength(1)
    expect(t.micIsOn()).toBe(false)
    expect(t.last()).toEqual({ phase: 'cancelled' })
  })

  it('cancel while the mic is still starting leaves it off', async () => {
    const t = setup()
    const started = t.d.start()
    t.d.cancel()
    await started
    expect(t.micIsOn()).toBe(false)
    expect(t.sent).toEqual([])
  })

  it('a release while starting stops right after the mic came on', async () => {
    const t = setup()
    const started = t.d.start()
    t.d.stop()
    await started
    expect(t.sent).toEqual(['audio_begin', 'audio_end'])
    expect(t.micIsOn()).toBe(false)
  })

  it('stops by itself at 10 s', async () => {
    const t = setup()
    await t.d.start()
    vi.advanceTimersByTime(9_000)
    expect(t.last()).toMatchObject({ phase: 'listening' })
    vi.advanceTimersByTime(1_500)
    expect(t.sent).toContain('audio_end')
    expect(t.micIsOn()).toBe(false)
  })

  it('a server error or lost connection stops the mic', async () => {
    const t = setup()
    await t.d.start()
    t.d.fail('Connection lost.')
    expect(t.micIsOn()).toBe(false)
    expect(t.sent).toEqual(['audio_begin', 'audio_cancel'])
    expect(t.last()).toEqual({ phase: 'empty', human: 'Connection lost.' })
  })

  it('dispose (page change, exit) always leaves the mic off', async () => {
    const t = setup()
    await t.d.start()
    t.d.dispose()
    expect(t.micIsOn()).toBe(false)
    vi.advanceTimersByTime(30_000)
    expect(t.sent).toEqual(['audio_begin', 'audio_cancel'])
  })

  it('reports a missing mic and an offline socket without leaving the mic on', async () => {
    const noMic = setup(false)
    await noMic.d.start()
    expect(noMic.last()).toEqual({ phase: 'empty', human: 'The microphone is not available.' })
    expect(noMic.sent).toEqual([])
    expect(noMic.mic).toEqual([true, false]) // a failed start may still have opened it
    const offline = setup(true, false)
    await offline.d.start()
    expect(offline.micIsOn()).toBe(false)
    expect(offline.last().phase).toBe('empty')
  })

  it('gives up waiting for speech-to-text after 20 s', async () => {
    const t = setup()
    await t.d.start()
    t.d.stop()
    vi.advanceTimersByTime(20_000)
    expect(t.last()).toEqual({ phase: 'empty', human: 'No answer from speech-to-text.' })
  })
})


describe('dictateText', () => {
  it('shows the transcript and what it will do, then asks for a tap', () => {
    expect(dictateText({ phase: 'preview', text: 'buy 100 dollars of TON', command: BUY }, 'hold')).toBe(
      '"buy 100 dollars of TON"\n\n-> Paper-buy $100 of TON\n\ntap confirm · 2x discard',
    )
  })

  it('explains what was not understood, with examples', () => {
    const text = dictateText(
      { phase: 'empty', human: "Didn't catch that", text: 'la la', examples: ['"buy 0.01 bitcoin"', '"pnl"', '"x"'] },
      'hold',
    )
    expect(text).toBe('"la la"\nDidn\'t catch that\nTry:\n"buy 0.01 bitcoin"\n"pnl"\n\ntap retry · 2x back')
  })

  it('counts down the 10 s recording', () => {
    const text = dictateText({ phase: 'listening', elapsedMs: 5000 }, 'hold')
    expect(text.split('\n')[0]).toBe('Listening… release to stop  0:05')
    expect(text).toContain('max 0:10')
    expect(dictateText({ phase: 'listening', elapsedMs: 0 }, 'tap')).toContain('tap to stop')
  })
})
