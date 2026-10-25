import type { ReactNode } from 'react'
import { ErrorNotice } from '../components/ErrorNotice'
import { Button } from '../components/ui'
import { LoginPage } from '../features/auth/LoginPage'
import { useMe } from '../lib/api/auth'

/** Render `children` only with a valid session; otherwise the login page. */
export function AuthGate({ children }: { children: ReactNode }) {
  const me = useMe()

  // Cached session state wins over a failed background refetch (e.g. a transient network error).
  if (me.data === null) return <LoginPage />
  if (me.data) return children
  if (me.error) {
    return (
      <div className="mx-auto max-w-md space-y-3 p-8">
        <ErrorNotice error={me.error} title="Could not check your session" />
        <Button variant="secondary" onClick={() => void me.refetch()} disabled={me.isFetching}>
          Retry
        </Button>
      </div>
    )
  }
  return <p className="p-8 text-sm text-slate-500">Loading…</p>
}
