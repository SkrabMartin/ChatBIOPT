import { Link } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card } from '../../components/ui'
import {
  useLibrary,
  useRefreshSource,
  useRemoveSource,
  type IngestionJob,
  type LibraryItem,
} from '../../lib/api/evidence'
import { formatDateTime, plural } from '../../lib/format'

const statusTone: Record<IngestionJob['status'], string> = {
  queued: 'bg-slate-100 text-slate-600',
  running: 'bg-sky-100 text-sky-800',
  succeeded: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-rose-100 text-rose-800',
}

function JobStatus({ job }: { job: IngestionJob }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusTone[job.status]}`}>
      {job.status === 'queued' && job.attempts > 0 ? `retrying (${job.attempts}/${job.max_attempts})` : job.status}
    </span>
  )
}

function DocumentBadges({ item }: { item: LibraryItem }) {
  const fulltextStep = item.latest_job?.steps.find((s) => s.step === 'fulltext')
  const hasFulltext = item.documents.some((d) => d.kind === 'fulltext')
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {item.documents.map((d) => (
        <Link
          key={d.id}
          to={`/documents/${d.id}`}
          className="rounded border border-slate-200 px-1.5 py-0.5 text-xs text-teal-800 hover:bg-teal-50"
        >
          {d.kind === 'fulltext' ? 'Full text' : 'Abstract'} v{d.version}
          {d.license ? ` · ${d.license}` : ''} · {plural(item.chunk_counts[d.id] ?? 0, 'chunk')}
        </Link>
      ))}
      {!hasFulltext && fulltextStep?.status === 'skipped' ? (
        <span className="text-xs text-slate-500">{fulltextStep.detail}</span>
      ) : null}
    </div>
  )
}

function LibraryRow({ item, projectId }: { item: LibraryItem; projectId: string }) {
  const refresh = useRefreshSource(projectId)
  const remove = useRemoveSource(projectId)
  const job = item.latest_job
  const busy = job !== null && (job.status === 'queued' || job.status === 'running')
  return (
    <li className="space-y-1.5 py-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link
            to={`/literature/pubmed/${item.source.external_id}`}
            className="font-medium text-slate-900 hover:text-teal-800 hover:underline"
          >
            {item.source.title}
          </Link>
          <p className="text-xs text-slate-500">
            {[item.source.journal, item.source.published_year, `PMID ${item.source.external_id}`]
              .filter(Boolean)
              .join(' · ')}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {job ? <JobStatus job={job} /> : null}
          <Button variant="secondary" onClick={() => refresh.mutate(item.source.id)} disabled={busy || refresh.isPending}>
            Refresh
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              if (window.confirm('Remove this source from the project? Stored documents are kept.'))
                remove.mutate(item.source.id)
            }}
            disabled={remove.isPending}
          >
            Remove
          </Button>
        </div>
      </div>
      <DocumentBadges item={item} />
      {job?.status === 'failed' || (job?.status === 'queued' && job.error_message) ? (
        <p role="alert" className="text-xs text-rose-700">
          {job.error_message}
        </p>
      ) : null}
      {refresh.error ? <ErrorNotice error={refresh.error} title="Could not refresh" /> : null}
      {remove.error ? <ErrorNotice error={remove.error} title="Could not remove" /> : null}
    </li>
  )
}

export function EvidenceLibrary({ projectId }: { projectId: string }) {
  const library = useLibrary(projectId)

  return (
    <Card>
      <h2 className="mb-1 text-base font-semibold text-slate-900">Evidence library</h2>
      <p className="mb-3 text-xs text-slate-500">
        Add publications from{' '}
        <Link to="/literature" className="text-teal-700 hover:underline">
          Literature
        </Link>
        . Abstracts are always stored; full text only when it is open access under an allowed license.
      </p>
      {library.isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : library.error ? (
        <ErrorNotice error={library.error} title="Could not load the library" />
      ) : library.data.items.length === 0 && library.data.pending_jobs.length === 0 ? (
        <p className="text-sm text-slate-500">No sources yet.</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {library.data.pending_jobs.map((job) => (
            <li key={job.id} className="flex items-center justify-between gap-4 py-3">
              <span className="text-sm text-slate-700">
                PMID {job.external_id}
                <span className="ml-2 text-xs text-slate-400">added {formatDateTime(job.created_at)}</span>
                {job.error_message ? <span className="block text-xs text-rose-700">{job.error_message}</span> : null}
              </span>
              <JobStatus job={job} />
            </li>
          ))}
          {library.data.items.map((item) => (
            <LibraryRow key={item.source.id} item={item} projectId={projectId} />
          ))}
        </ul>
      )}
    </Card>
  )
}
