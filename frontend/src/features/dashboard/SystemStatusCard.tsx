import type { ReactNode } from 'react'
import { ApiError } from '../../lib/api/client'
import { useHealth } from '../../lib/api/health'

type Tone = 'ok' | 'warn' | 'error' | 'pending'

const toneClass: Record<Tone, string> = {
  ok: 'bg-emerald-100 text-emerald-800',
  warn: 'bg-amber-100 text-amber-800',
  error: 'bg-rose-100 text-rose-800',
  pending: 'bg-slate-100 text-slate-600',
}

function Badge({ tone, children }: { tone: Tone; children: string }) {
  return <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${toneClass[tone]}`}>{children}</span>
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between py-2">
      <dt className="text-sm text-slate-500">{label}</dt>
      <dd className="text-sm text-slate-900">{children}</dd>
    </div>
  )
}

export function SystemStatusCard() {
  const { data, error, isPending, isFetching, refetch } = useHealth()

  return (
    <section aria-labelledby="system-status" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h2 id="system-status" className="text-base font-semibold text-slate-900">
          System status
        </h2>
        <button
          type="button"
          onClick={() => void refetch()}
          disabled={isFetching}
          className="rounded-md border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          {isFetching ? 'Checking…' : 'Recheck'}
        </button>
      </div>

      {isPending ? (
        <p className="text-sm text-slate-500">Checking API…</p>
      ) : error ? (
        <div role="alert" className="space-y-1 text-sm">
          <Badge tone="error">API unavailable</Badge>
          <p className="text-slate-600">{error.message}</p>
          {error instanceof ApiError && error.traceId ? (
            <p className="font-mono text-xs text-slate-400">trace {error.traceId}</p>
          ) : null}
        </div>
      ) : (
        <dl className="divide-y divide-slate-100">
          <Row label="API">
            <Badge tone={data.status === 'ok' ? 'ok' : 'warn'}>{data.status === 'ok' ? 'Operational' : 'Degraded'}</Badge>
          </Row>
          <Row label="Database">
            <Badge tone={data.database.status === 'ok' ? 'ok' : 'error'}>
              {data.database.status === 'ok' ? 'Connected' : 'Unavailable'}
            </Badge>
          </Row>
          <Row label="Schema revision">
            <span className="font-mono">{data.database.migration_revision ?? '—'}</span>
          </Row>
          <Row label="Version">
            <span className="font-mono">
              {data.version} · {data.environment}
            </span>
          </Row>
        </dl>
      )}
    </section>
  )
}
