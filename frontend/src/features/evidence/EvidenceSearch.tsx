import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, TextInput } from '../../components/ui'
import { useRetrieval, type RetrievalMode } from '../../lib/api/retrieval'

const MODES: readonly RetrievalMode[] = ['hybrid', 'keyword', 'semantic']

/** Search the project's evidence library directly: the same retrieval grounded answers use. */
export function EvidenceSearch({ projectId }: { projectId: string }) {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<RetrievalMode>('hybrid')
  const search = useRetrieval(projectId)

  function submit(event: FormEvent) {
    event.preventDefault()
    if (query.trim()) search.mutate({ query: query.trim(), mode })
  }

  const result = search.data
  return (
    <Card>
      <h2 className="mb-3 text-base font-semibold text-slate-900">Search evidence</h2>
      <form onSubmit={submit} className="flex gap-2" role="search">
        <label htmlFor="evidence-query" className="sr-only">
          Evidence query
        </label>
        <TextInput
          id="evidence-query"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. R175H subclones, temozolomide resistance"
        />
        <label className="sr-only" htmlFor="evidence-mode">
          Retrieval mode
        </label>
        <select
          id="evidence-mode"
          value={mode}
          onChange={(e) => setMode(MODES.find((m) => m === e.target.value) ?? 'hybrid')}
          className="rounded-md border border-slate-300 bg-white px-2 text-sm"
        >
          {MODES.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <Button type="submit" disabled={!query.trim() || search.isPending} className="shrink-0">
          Search
        </Button>
      </form>
      {search.error ? <ErrorNotice error={search.error} title="Search failed" /> : null}
      {result ? (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-slate-400">
            {result.retrieval_version} · {result.embedding_space ?? 'no embeddings'} · {result.latency_ms} ms
          </p>
          {result.warnings.map((w) => (
            <p key={w} role="status" className="text-xs text-amber-700">
              {w}
            </p>
          ))}
          {result.hits.length === 0 ? (
            <p className="text-sm text-slate-500">No passages found.</p>
          ) : (
            <ol className="space-y-2">
              {result.hits.map((hit) => (
                <li
                  key={hit.chunk_id}
                  className={`rounded border p-2 text-xs ${hit.counts_as_evidence ? 'border-slate-200' : 'border-dashed border-slate-200 opacity-70'}`}
                >
                  <p className="mb-0.5 text-slate-500">
                    #{hit.rank} ·{' '}
                    <Link to={`/documents/${hit.document_id}#chunk-${hit.chunk_id}`} className="text-teal-700 hover:underline">
                      {hit.source_title}
                    </Link>{' '}
                    · {hit.section_title ?? hit.section} · keyword {hit.keyword_rank ?? '–'} · semantic{' '}
                    {hit.semantic_rank ?? '–'}
                    {hit.similarity !== null ? ` (sim ${hit.similarity.toFixed(2)})` : ''}
                    {hit.counts_as_evidence ? '' : ' · below evidence threshold'}
                  </p>
                  <p className="line-clamp-4 whitespace-pre-wrap text-slate-700">{hit.text}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : null}
    </Card>
  )
}
