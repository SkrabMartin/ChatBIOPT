import { Link, useParams } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Card } from '../../components/ui'
import { ApiError } from '../../lib/api/client'
import { useDocument, useSourceDocuments, type DocumentDetail } from '../../lib/api/evidence'
import { formatDateTime } from '../../lib/format'

function Provenance({ doc }: { doc: DocumentDetail }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      <dt className="text-slate-500">Origin</dt>
      <dd className="break-all">
        {doc.origin === 'europepmc' ? 'Europe PMC full text' : 'PubMed record'} ·{' '}
        <span className="font-mono text-xs">{doc.origin_url}</span>
      </dd>
      <dt className="text-slate-500">Rights</dt>
      <dd>
        {doc.access === 'open_access' ? (
          <>
            Open access under {doc.license ?? 'unknown license'}
            {doc.license_url ? (
              <>
                {' '}
                ·{' '}
                <a href={doc.license_url} target="_blank" rel="noopener noreferrer" className="text-teal-700 hover:underline">
                  license
                </a>
              </>
            ) : null}
          </>
        ) : (
          'Bibliographic abstract (stored for retrieval; not redistributed)'
        )}
      </dd>
      <dt className="text-slate-500">Retrieved</dt>
      <dd>{formatDateTime(doc.retrieved_at)}</dd>
      <dt className="text-slate-500">Text SHA-256</dt>
      <dd className="break-all font-mono text-xs">{doc.text_sha256}</dd>
      <dt className="text-slate-500">Raw SHA-256</dt>
      <dd className="break-all font-mono text-xs">{doc.raw_sha256}</dd>
      <dt className="text-slate-500">Pipeline</dt>
      <dd className="font-mono text-xs">
        {doc.parser_version} · {doc.chunker_version}
      </dd>
    </dl>
  )
}

export function DocumentPage() {
  const { documentId = '' } = useParams()
  const doc = useDocument(documentId)
  const versions = useSourceDocuments(doc.data?.source_id)

  if (doc.isPending) return <p className="text-sm text-slate-500">Loading document…</p>
  if (doc.error) {
    const missing = doc.error instanceof ApiError && (doc.error.status === 404 || doc.error.status === 422)
    return <ErrorNotice error={doc.error} title={missing ? 'Document not found' : 'Could not load document'} />
  }
  const d = doc.data
  const sameKind = versions.data?.filter((v) => v.kind === d.kind) ?? []

  return (
    <div className="space-y-6">
      <nav className="text-sm text-slate-500">
        <Link to={`/literature/pubmed/${d.source.external_id}`} className="hover:underline">
          PMID {d.source.external_id}
        </Link>{' '}
        / {d.kind === 'fulltext' ? 'Full text' : 'Abstract'} v{d.version}
      </nav>
      <Card className="space-y-3">
        <div className="flex items-start justify-between gap-4">
          <h1 className="text-xl font-semibold text-slate-900">{d.source.title}</h1>
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${d.is_latest ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}
          >
            {d.is_latest ? 'Latest version' : 'Superseded version'}
          </span>
        </div>
        <Provenance doc={d} />
        {sameKind.length > 1 ? (
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-slate-500">Versions:</span>
            {sameKind.map((v) => (
              <Link
                key={v.id}
                to={`/documents/${v.id}`}
                aria-current={v.id === d.id ? 'page' : undefined}
                className={`rounded border px-1.5 py-0.5 ${v.id === d.id ? 'border-teal-600 text-teal-800' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}
              >
                v{v.version} · {formatDateTime(v.created_at)}
              </Link>
            ))}
          </div>
        ) : null}
      </Card>

      <Card>
        <h2 className="mb-1 text-base font-semibold text-slate-900">Chunks ({d.chunks.length})</h2>
        <p className="mb-3 text-xs text-slate-500">
          Addressable passages used for retrieval. Offsets index into the stored text of this exact version.
        </p>
        <ol className="space-y-3">
          {d.chunks.map((c) => (
            <li key={c.id} id={`chunk-${c.id}`} className="rounded-md border border-slate-200 p-3">
              <p className="mb-1 font-mono text-xs text-slate-400">
                #{c.ordinal} · {c.section}
                {c.section_title ? ` · ${c.section_title}` : ''} · [{c.start_offset}, {c.end_offset}) · ~{c.token_estimate}{' '}
                tokens
              </p>
              {/* Article text is untrusted: rendered as plain text only. */}
              <p className="whitespace-pre-wrap text-sm text-slate-800">{c.text}</p>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  )
}
