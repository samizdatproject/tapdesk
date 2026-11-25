import type { EvenHubEvent } from '@evenrealities/even_hub_sdk'
import { describe, expect, it } from 'vitest'
import { normalise } from './bridge'

// Events are written as the host delivers them (protobuf omits zero values).
const ev = (e: Record<string, unknown>) => normalise(e as EvenHubEvent)

describe('normalise', () => {
  it('reads a sysEvent with no eventType as a tap', () => {
    expect(ev({ sysEvent: { eventSource: 1 } })).toEqual({ kind: 'CLICK' })
  })

  it('reads a list tap without index as item 0', () => {
    expect(ev({ listEvent: { containerID: 2, containerName: 'chats' } })).toEqual({ kind: 'LIST_SELECT', index: 0 })
  })

  it('keeps the index of other list items', () => {
    expect(ev({ listEvent: { currentSelectItemIndex: 3 } })).toEqual({ kind: 'LIST_SELECT', index: 3 })
  })

  it('maps double tap in any envelope', () => {
    expect(ev({ sysEvent: { eventType: 3 } })).toEqual({ kind: 'DOUBLE' })
    expect(ev({ textEvent: { eventType: 3 } })).toEqual({ kind: 'DOUBLE' })
    expect(ev({ listEvent: { eventType: 3 } })).toEqual({ kind: 'DOUBLE' })
  })

  it('maps long press start and release', () => {
    expect(ev({ sysEvent: { eventType: 9 } })).toEqual({ kind: 'LONG_START' })
    expect(ev({ sysEvent: { eventType: 10 } })).toEqual({ kind: 'LONG_END' })
  })

  it('maps text scroll events to swipes', () => {
    expect(ev({ textEvent: { eventType: 1 } })).toEqual({ kind: 'SWIPE_UP' })
    expect(ev({ textEvent: { eventType: 2 } })).toEqual({ kind: 'SWIPE_DOWN' })
  })

  it('reads a textEvent with no eventType as a tap', () => {
    expect(ev({ textEvent: { containerID: 1 } })).toEqual({ kind: 'CLICK' })
  })

  it('maps foreground and exit events', () => {
    expect(ev({ sysEvent: { eventType: 4 } })).toEqual({ kind: 'FG_ENTER' })
    expect(ev({ sysEvent: { eventType: 5 } })).toEqual({ kind: 'FG_EXIT' })
    expect(ev({ sysEvent: { eventType: 6 } })).toEqual({ kind: 'EXIT' })
    expect(ev({ sysEvent: { eventType: 7 } })).toEqual({ kind: 'EXIT' })
  })

  it('maps a menu selection', () => {
    expect(ev({ menuItemClickEvent: { itemID: 2 } })).toEqual({ kind: 'MENU', itemId: 2 })
    expect(ev({ menuItemClickEvent: {} })).toBeNull()
  })

  it('passes audio through and never reads it as a tap', () => {
    const pcm = new Uint8Array([1, 2, 3])
    expect(ev({ audioEvent: { audioPcm: pcm } })).toEqual({ kind: 'AUDIO', pcm })
  })

  it('ignores IMU reports, unknown types and empty events', () => {
    expect(ev({ sysEvent: { eventType: 8, imuData: null } })).toBeNull()
    expect(ev({ sysEvent: { eventType: 42 } })).toBeNull()
    expect(ev({})).toBeNull()
    expect(ev({ jsonData: { foo: 1 } })).toBeNull()
  })
})
