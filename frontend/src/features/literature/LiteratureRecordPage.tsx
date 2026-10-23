import { Link, useParams } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card } from '../../components/ui'
import { ApiError } from '../../lib/api/client'
import { useLiteratureRecord, useRefreshLiteratureRecord } from '../../lib/api/literature'
import { formatDateTime } from '../../lib/format'
import { IdentifierLinks } from './RecordCard'

export function LiteratureRecordPage() {
  const { pmid = '' } = useParams()
  const record = useLiteratureRecord(pmid)
  const refresh = useRefreshLiteratureRecord(pmid)

  if (record.isPending) return <p className="text-sm text-slate-500">Loading record…</p>
  if (record.error) {
    const missing = record.error instanceof ApiError && (record.error.status === 404 || record.error.status === 422)
    return (
      <div className="space-y-3">
        <ErrorNotice error={record.error} title={missing ? 'Record not found' : 'Could not load record'} />
        <Link to="/literature" className="text-sm text-teal-700 hover:underline">
          Back to literature search
        </Link>
      </div>
    )
  }

  const r = record.data
  return (
    <div className="space-y-6">
      <nav className="text-sm text-slate-500">
        <button type="button" onClick={() => window.history.back()} className="hover:underline">
          ← Back
        </button>
      </nav>
      <Card className="space-y-3">
        <h1 className="text-xl font-semibold text-slate-900">{r.title}</h1>
        <p className="text-sm text-slate-700">{r.authors.length ? r.authors.join(', ') : 'No authors listed'}</p>
        <p className="text-sm text-slate-500">
          {[r.journal, r.published_date ?? r.published_year?.toString()].filter(Boolean).join(' · ')}
        </p>
        {r.publication_types.length ? (
          <p className="text-xs text-slate-500">{r.publication_types.join(' · ')}</p>
        ) : null}
        <IdentifierLinks record={r} />
      </Card>

      <Card className="space-y-3">
        <h2 className="text-base font-semibold text-slate-900">Abstract</h2>
        {r.abstract_sections.length === 0 ? (
          <p className="text-sm text-slate-500">No abstract in PubMed record.</p>
        ) : (
          r.abstract_sections.map((section, i) => (
            <div key={i}>
              {section.label ? (
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{section.label}</h3>
              ) : null}
              <p className="whitespace-pre-wrap text-sm text-slate-800">{section.text}</p>
            </div>
          ))
        )}
      </Card>

      <Card>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">Provenance</h2>
          <Button variant="secondary" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            {refresh.isPending ? 'Refreshing…' : 'Re-fetch from PubMed'}
          </Button>
        </div>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-slate-500">Source</dt>
          <dd>PubMed (NCBI E-utilities)</dd>
          <dt className="text-slate-500">Retrieved</dt>
          <dd>{formatDateTime(r.retrieved_at)}</dd>
          <dt className="text-slate-500">Payload SHA-256</dt>
          <dd className="break-all font-mono text-xs">{r.raw_payload_sha256}</dd>
          <dt className="text-slate-500">Normalizer</dt>
          <dd className="font-mono text-xs">{r.normalizer_version}</dd>
        </dl>
        {refresh.error ? <ErrorNotice error={refresh.error} title="Re-fetch failed; showing stored snapshot" /> : null}
      </Card>
    </div>
  )
}
