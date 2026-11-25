import type { EvenAppBridge } from '@evenrealities/even_hub_sdk'

// Persisted values go to the Even App's storage (survives WebView kills) and
// are mirrored to window.localStorage, which is all a plain browser has.

export const KEY_TOKEN = 'tapdesk.token'
export const KEY_BACKEND = 'tapdesk.backend'

let bridge: EvenAppBridge | null = null

export function useBridgeStorage(b: EvenAppBridge | null): void {
  bridge = b
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) => setTimeout(() => reject(new Error('timeout')), ms)),
  ])
}

export async function load(key: string): Promise<string | null> {
  if (bridge) {
    try {
      const v = await withTimeout(bridge.getLocalStorage(key), 1000)
      if (v) return v
    } catch (err) {
      console.warn('[tapdesk] getLocalStorage failed', err)
    }
  }
  try {
    return window.localStorage.getItem(key) || null
  } catch {
    return null
  }
}

/** `null` removes the value. */
export async function save(key: string, value: string | null): Promise<void> {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // storage blocked: the Even App copy below is the one that matters
  }
  if (bridge) {
    try {
      await withTimeout(bridge.setLocalStorage(key, value ?? ''), 1000)
    } catch (err) {
      console.warn('[tapdesk] setLocalStorage failed', err)
    }
  }
}
