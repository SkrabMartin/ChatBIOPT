import { ApiError } from '../lib/api/client'

export function ErrorNotice({ error, title = 'Something went wrong' }: { error: Error; title?: string }) {
  return (
    <div role="alert" className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
      <p className="font-medium">{title}</p>
      <p>{error.message}</p>
      {error instanceof ApiError && error.traceId ? (
        <p className="font-mono text-xs text-rose-500">trace {error.traceId}</p>
      ) : null}
    </div>
  )
}
