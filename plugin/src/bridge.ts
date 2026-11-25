import {
  OsEventTypeList,
  waitForEvenAppBridge,
  type EvenAppBridge,
  type EvenHubEvent,
} from '@evenrealities/even_hub_sdk'

/** Everything the glasses can tell us, flattened into one union. */
export type Input =
  | { kind: 'CLICK' }
  | { kind: 'DOUBLE' }
  | { kind: 'LONG_START' }
  | { kind: 'LONG_END' }
  | { kind: 'SWIPE_UP' }
  | { kind: 'SWIPE_DOWN' }
  | { kind: 'LIST_SELECT'; index: number }
  | { kind: 'MENU'; itemId: number }
  | { kind: 'FG_ENTER' }
  | { kind: 'FG_EXIT' }
  | { kind: 'EXIT' }
  | { kind: 'AUDIO'; pcm: Uint8Array }

// CLICK_EVENT is 0 and protobuf omits zero values, so a tap arrives as an
// envelope with no `eventType`. The default is resolved only for an envelope
// that exists; otherwise every audio frame or menu event would read as a tap.
function eventTypeOf(envelope?: { eventType?: OsEventTypeList }): OsEventTypeList | null {
  if (!envelope) return null
  return envelope.eventType ?? OsEventTypeList.CLICK_EVENT
}

function fromType(type: OsEventTypeList): Input | null {
  switch (type) {
    case OsEventTypeList.CLICK_EVENT:
      return { kind: 'CLICK' }
    case OsEventTypeList.DOUBLE_CLICK_EVENT:
      return { kind: 'DOUBLE' }
    case OsEventTypeList.SCROLL_TOP_EVENT:
      return { kind: 'SWIPE_UP' }
    case OsEventTypeList.SCROLL_BOTTOM_EVENT:
      return { kind: 'SWIPE_DOWN' }
    case OsEventTypeList.LONG_PRESS_EVENT:
      return { kind: 'LONG_START' }
    case OsEventTypeList.LONG_PRESS_RELEASE_EVENT:
      return { kind: 'LONG_END' }
    case OsEventTypeList.FOREGROUND_ENTER_EVENT:
      return { kind: 'FG_ENTER' }
    case OsEventTypeList.FOREGROUND_EXIT_EVENT:
      return { kind: 'FG_EXIT' }
    case OsEventTypeList.ABNORMAL_EXIT_EVENT:
    case OsEventTypeList.SYSTEM_EXIT_EVENT:
      return { kind: 'EXIT' }
    default:
      return null // IMU reports and unknown values
  }
}

/** Pure: one host event in, at most one Input out. */
export function normalise(event: EvenHubEvent): Input | null {
  const pcm = event.audioEvent?.audioPcm
  if (pcm) return { kind: 'AUDIO', pcm }

  if (event.menuItemClickEvent) {
    const itemId = event.menuItemClickEvent.itemID
    return itemId ? { kind: 'MENU', itemId } : null
  }

  const listType = eventTypeOf(event.listEvent)
  if (listType === OsEventTypeList.CLICK_EVENT) {
    // Item 0 arrives without an index (zero is omitted on the wire).
    return { kind: 'LIST_SELECT', index: event.listEvent?.currentSelectItemIndex ?? 0 }
  }
  if (listType !== null) return fromType(listType)

  const textType = eventTypeOf(event.textEvent)
  if (textType !== null) return fromType(textType)

  const sysType = eventTypeOf(event.sysEvent)
  if (sysType !== null) return fromType(sysType)

  return null
}

let subscribed = false

/** Resolves with the bridge, or null when there is no Even host (plain browser). */
export async function connectBridge(
  onInput: (input: Input) => void,
  timeoutMs = 3000,
): Promise<EvenAppBridge | null> {
  const bridge = await Promise.race([
    waitForEvenAppBridge(),
    new Promise<null>(resolve => setTimeout(() => resolve(null), timeoutMs)),
  ])
  // The SDK resolves in any browser; only a host (Even App or simulator)
  // provides the Flutter channel that its calls go through.
  if (!bridge || !('flutter_inappwebview' in window)) return null
  if (subscribed) return bridge
  subscribed = true
  bridge.onEvenHubEvent(event => {
    const input = normalise(event)
    if (input && input.kind !== 'AUDIO') console.log('[tapdesk] input', input)
    if (input) onInput(input)
  })
  return bridge
}
