import { useCallback, useSyncExternalStore } from 'react'

const MODEL_PROFILE_KEY = 'bioresearch.modelProfile'
const listeners = new Set<() => void>()

function subscribe(listener: () => void) {
  listeners.add(listener)
  window.addEventListener('storage', listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', listener)
  }
}

/**
 * The model profile this browser uses for new answers; null means "the server's default".
 * Stored locally: it is a per-user UI preference, not shared configuration.
 */
export function useModelPreference(): [string | null, (profile: string | null) => void] {
  const value = useSyncExternalStore(subscribe, () => window.localStorage.getItem(MODEL_PROFILE_KEY))
  const set = useCallback((profile: string | null) => {
    if (profile) window.localStorage.setItem(MODEL_PROFILE_KEY, profile)
    else window.localStorage.removeItem(MODEL_PROFILE_KEY)
    for (const listener of listeners) listener()
  }, [])
  return [value, set]
}
