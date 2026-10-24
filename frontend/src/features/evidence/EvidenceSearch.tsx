import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, TextInput } from '../../components/ui'
import { useRetrieval, type RetrievalMode } from '../../lib/api/retrieval'
import { useModels } from '../../lib/api/runs'

const MODES: readonly RetrievalMode[] = ['hybrid', 'keyword', 'semantic']

/** Search the project's evidence library directly: the same retrieval grounded answers use. */
export function EvidenceSearch({ projectId }: { projectId: string }) {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<RetrievalMode>('hybrid')
  const [rerankProfile, setRerankProfile] = useState('')
  const search = useRetrieval(projectId)
  const rerankProfiles = useModels().data?.rerank_profiles ?? []

  function submit(event: FormEvent) {
    event.preventDefault()
    if (query.trim()) search.mutate({ query: query.trim(), mode, rerankProfile: rerankProfile || undefined })
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
        {rerankProfiles.length ? (
          <>
            <label className="sr-only" htmlFor="evidence-rerank">
              Reranker
            </label>
            <select
              id="evidence-rerank"
              value={rerankProfile}
              onChange={(e) => setRerankProfile(e.target.value)}
              title="LLM reranking of the top candidates (slow; off by default, see Evaluations)"
              className="rounded-md border border-slate-300 bg-white px-2 text-sm"
            >
              <option value="">no rerank</option>
              {rerankProfiles.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
          </>
        ) : null}
        <Button type="submit" disabled={!query.trim() || search.isPending} className="shrink-0">
          Search
        </Button>
      </form>
      {search.error ? <ErrorNotice error={search.error} title="Search failed" /> : null}
      {result ? (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-slate-400">
            {result.retrieval_version} · {result.embedding_space ?? 'no embeddings'} · {result.latency_ms} ms
            {result.rerank_ms !== null ? ` (rerank ${result.rerank_ms} ms)` : ''}
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
                    {hit.rerank_score !== null ? ` · rerank ${hit.rerank_score}/3` : ''}
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
