import type { LinkStatus, Session } from './net'

export interface State {
  token: string | null
  base: string
  link: LinkStatus
  session: Session | null
  /** Last human-readable status line for the phone screen. */
  notice: string
}

export type Store = ReturnType<typeof createStore>

export function createStore(initial: State) {
  let state = initial
  const subscribers = new Set<(s: State) => void>()
  return {
    get: (): State => state,
    set(patch: Partial<State>): void {
      state = { ...state, ...patch }
      for (const fn of subscribers) fn(state)
    },
    subscribe(fn: (s: State) => void): () => void {
      subscribers.add(fn)
      return () => subscribers.delete(fn)
    },
  }
}
