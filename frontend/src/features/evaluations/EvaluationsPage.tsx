import { useState } from 'react'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Card } from '../../components/ui'
import { useEvaluation, useEvaluations, type Evaluation, type MetricValue } from '../../lib/api/evaluations'
import { formatDateTime } from '../../lib/format'

const LOWER_IS_BETTER: Record<string, true> = {
  false_abstention_rate: true,
  uncited_claim_rate: true,
  wrong_organism_rate: true,
  false_resolution_rate: true,
}

function Metric({ value }: { value: MetricValue | undefined }) {
  if (value === undefined || value === null) return <span className="text-slate-300">—</span>
  if (typeof value === 'number') return <span>{value.toFixed(3)}</span>
  if (!value.n && value.n !== undefined) return <span className="text-slate-300">—</span>
  return (
    <span title={value.n ? `n = ${value.n}` : undefined}>
      {value.mean.toFixed(3)}{' '}
      <span className="text-xs text-slate-400">
        [{value.ci95_low.toFixed(2)}–{value.ci95_high.toFixed(2)}]
      </span>
    </span>
  )
}

function meanOf(value: MetricValue | undefined): number | null {
  if (value === undefined || value === null) return null
  return typeof value === 'number' ? value : value.mean
}

// Baselines first, then increasingly complex pipelines (JSONB storage does not preserve key order).
const CONFIG_ORDER = ['no-rag', 'keyword', 'semantic', 'hybrid']
const configRank = (name: string) => {
  const index = CONFIG_ORDER.indexOf(name)
  return index === -1 ? CONFIG_ORDER.length : index
}

function ResultsTable({ evaluation }: { evaluation: Evaluation }) {
  const configs = Object.entries(evaluation.report.results).sort(
    ([a], [b]) => configRank(a) - configRank(b) || a.localeCompare(b),
  )
  const metrics = [...new Set(configs.flatMap(([, r]) => Object.keys(r.metrics)))]
  // Highlight the best configuration per metric (direction-aware).
  const best: Record<string, number | null> = {}
  for (const metric of metrics) {
    const values = configs.map(([, r]) => meanOf(r.metrics[metric])).filter((v): v is number => v !== null)
    best[metric] = values.length ? (LOWER_IS_BETTER[metric] ? Math.min(...values) : Math.max(...values)) : null
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
            <th className="py-2 pr-4">Configuration</th>
            {metrics.map((m) => (
              <th key={m} className="py-2 pr-4 font-medium">
                {m}
                {LOWER_IS_BETTER[m] ? ' ↓' : ''}
              </th>
            ))}
            <th className="py-2 pr-4 font-medium">p50 / p95 ms</th>
          </tr>
        </thead>
        <tbody>
          {configs.map(([name, result]) => (
            <tr key={name} className="border-b border-slate-100 align-top">
              <td className="py-2 pr-4 font-mono text-xs">
                {name}
                {result.failed_runs?.length ? (
                  <span className="block text-rose-700">{result.failed_runs.length} failed run(s)</span>
                ) : null}
                {result.warnings?.map((w) => (
                  <span key={w} className="block text-amber-700">
                    {w}
                  </span>
                ))}
              </td>
              {metrics.map((m) => (
                <td
                  key={m}
                  className={`py-2 pr-4 ${meanOf(result.metrics[m]) !== null && meanOf(result.metrics[m]) === best[m] && configs.length > 1 ? 'font-semibold text-teal-800' : ''}`}
                >
                  <Metric value={result.metrics[m]} />
                </td>
              ))}
              <td className="py-2 pr-4 text-xs text-slate-600">
                {result.latency_ms ? `${result.latency_ms.p50} / ${result.latency_ms.p95}` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Detail({ id }: { id: string }) {
  const evaluation = useEvaluation(id)
  if (evaluation.isPending) return <p className="text-sm text-slate-500">Loading report…</p>
  if (evaluation.error) return <ErrorNotice error={evaluation.error} title="Could not load report" />
  const e = evaluation.data
  const run = e.report.evaluation_run
  return (
    <Card className="min-w-0 space-y-3">
      <div>
        <h2 className="text-base font-semibold text-slate-900">
          {e.dataset} v{e.dataset_version} · {e.split === 'test' ? 'held-out test set' : 'development set'}
        </h2>
        <p className="font-mono text-xs text-slate-400">
          {e.kind} · commit {e.git_commit ?? '?'} · dataset sha256 {e.dataset_sha256.slice(0, 12)}…
          {typeof run.embedding_space === 'string' ? ` · ${run.embedding_space.slice(0, 32)}` : ''}
          {typeof run.answer_model === 'string' ? ` · answers ${run.answer_model}` : ''}
        </p>
        {typeof run.judge === 'string' ? (
          <p className="text-xs text-amber-700">
            Judge: {run.judge} — automated, not an independent expert review.
          </p>
        ) : null}
      </div>
      <ResultsTable evaluation={e} />
      {e.report.injection ? (
        <p className="text-sm text-slate-700">
          Prompt-injection resistance: <strong>{e.report.injection.resistance ?? '—'}</strong>
          {e.report.injection.model_resistance != null ? (
            <span className="text-slate-500">
              {' '}
              (after server-side validation; model output alone {e.report.injection.model_resistance})
            </span>
          ) : null}
        </p>
      ) : null}
      <p className="text-xs text-slate-400">
        Values are means with 95% bootstrap intervals over items. Small sets give wide intervals; compare
        configurations with <code className="font-mono">python -m app.evals.compare</code> (paired test).
      </p>
    </Card>
  )
}

export function EvaluationsPage() {
  const evaluations = useEvaluations()
  const [selected, setSelected] = useState<string | null>(null)
  const current = selected ?? evaluations.data?.[0]?.id ?? null

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Evaluations</h1>
        <p className="mt-1 text-sm text-slate-500">
          Recorded retrieval, answer-quality and entity-resolution runs. Produced by the eval runners in{' '}
          <code>backend/app/evals</code>.
        </p>
      </header>
      {evaluations.isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : evaluations.error ? (
        <ErrorNotice error={evaluations.error} title="Could not load evaluations" />
      ) : evaluations.data.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-500">No evaluations recorded yet.</p>
        </Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <Card className="p-2">
            <ul className="space-y-1">
              {evaluations.data.map((e) => (
                <li key={e.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(e.id)}
                    aria-current={e.id === current ? 'true' : undefined}
                    className={`w-full rounded-md px-2 py-1.5 text-left text-sm ${e.id === current ? 'bg-teal-50 text-teal-900' : 'hover:bg-slate-50'}`}
                  >
                    <span className="block font-medium">
                      {e.kind} · {e.split}
                    </span>
                    <span className="block text-xs text-slate-500">
                      {e.dataset} v{e.dataset_version} · {formatDateTime(e.created_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          {current ? <Detail id={current} /> : null}
        </div>
      )}
    </div>
  )
}
