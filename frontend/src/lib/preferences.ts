import { useCallback, useSyncExternalStore } from 'react'
import type { Grounding } from './api/runs'

const listeners = new Set<() => void>()

function subscribe(listener: () => void) {
  listeners.add(listener)
  window.addEventListener('storage', listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', listener)
  }
}

/** A per-browser UI preference in localStorage; null removes it. */
function usePreference(key: string): [string | null, (value: string | null) => void] {
  const value = useSyncExternalStore(subscribe, () => window.localStorage.getItem(key))
  const set = useCallback(
    (next: string | null) => {
      if (next) window.localStorage.setItem(key, next)
      else window.localStorage.removeItem(key)
      for (const listener of listeners) listener()
    },
    [key],
  )
  return [value, set]
}

/** The model profile this browser uses for new answers; null means "the server's default". */
export function useModelPreference(): [string | null, (profile: string | null) => void] {
  return usePreference('bioresearch.modelProfile')
}

const GROUNDINGS: Record<Grounding, true> = { auto: true, project: true, none: true }

function isGrounding(value: string | null): value is Grounding {
  return value !== null && value in GROUNDINGS
}

/** Whether new answers use the project's evidence library ("auto" = when it has passages). */
export function useGroundingPreference(): [Grounding, (grounding: Grounding) => void] {
  const [value, set] = usePreference('bioresearch.grounding')
  return [isGrounding(value) ? value : 'auto', (next) => set(next === 'auto' ? null : next)]
}
