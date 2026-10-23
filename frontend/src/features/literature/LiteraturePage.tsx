import { useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, TextInput } from '../../components/ui'
import { ApiError } from '../../lib/api/client'
import { PAGE_SIZE, QUERY_MAX_LENGTH, useLiteratureSearch } from '../../lib/api/literature'
import { formatDateTime } from '../../lib/format'
import { RecordCard } from './RecordCard'

// PubMed's ESearch cannot page past the first 10,000 hits.
const PUBMED_MAX_WINDOW = 10_000

function upstreamTitle(error: Error): string {
  if (!(error instanceof ApiError)) return 'Search failed'
  if (error.code === 'upstream_timeout') return 'PubMed did not respond in time'
  if (error.code?.startsWith('upstream_')) return 'PubMed is unavailable'
  return 'Search failed'
}

export function LiteraturePage() {
  const [params, setParams] = useSearchParams()
  const query = params.get('q')?.trim() ?? ''
  const offset = Math.max(0, Number.parseInt(params.get('offset') ?? '0', 10) || 0)
  const [draft, setDraft] = useState(query)
  const search = useLiteratureSearch(query, offset)

  function submit(event: FormEvent) {
    event.preventDefault()
    const q = draft.trim()
    if (q) setParams({ q })
  }

  const reachable = search.data ? Math.min(search.data.total_count, PUBMED_MAX_WINDOW) : 0

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Literature</h1>
        <p className="mt-1 text-sm text-slate-500">
          Read-only PubMed search. Results are the records PubMed returned, with links to the originals; nothing is
          generated.
        </p>
      </header>

      <form onSubmit={submit} className="flex gap-2" role="search">
        <label htmlFor="literature-query" className="sr-only">
          PubMed query
        </label>
        <TextInput
          id="literature-query"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={QUERY_MAX_LENGTH}
          placeholder='e.g. TP53 glioblastoma, "R175H"[tiab], BRCA1 AND breast cancer'
        />
        <Button type="submit" disabled={!draft.trim() || search.isFetching} className="shrink-0">
          {search.isFetching ? 'Searching…' : 'Search'}
        </Button>
      </form>

      {!query ? null : search.isPending ? (
        <p className="text-sm text-slate-500">Searching PubMed…</p>
      ) : search.error ? (
        <ErrorNotice error={search.error} title={upstreamTitle(search.error)} />
      ) : (
        <Card>
          <div className="space-y-1 border-b border-slate-100 pb-3">
            <p className="text-sm text-slate-700">
              {search.data.total_count === 0
                ? 'No PubMed records match this query.'
                : `${search.data.total_count.toLocaleString()} records · showing ${offset + 1}–${offset + search.data.items.length}`}
            </p>
            <p className="text-xs text-slate-400">
              Retrieved {formatDateTime(search.data.retrieved_at)} · search {search.data.search_id.slice(0, 8)}
            </p>
            {search.data.query_translation ? (
              <details className="text-xs text-slate-500">
                <summary className="cursor-pointer">How PubMed interpreted the query</summary>
                <p className="mt-1 break-words font-mono">{search.data.query_translation}</p>
              </details>
            ) : null}
          </div>
          <div className="divide-y divide-slate-100">
            {search.data.items.map((record) => (
              <RecordCard key={record.pmid} record={record} />
            ))}
          </div>
          {search.data.total_count > PAGE_SIZE ? (
            <div className="flex items-center justify-between border-t border-slate-100 pt-3">
              <Button
                variant="secondary"
                disabled={offset === 0}
                onClick={() => setParams({ q: query, offset: String(Math.max(0, offset - PAGE_SIZE)) })}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                disabled={offset + PAGE_SIZE >= reachable}
                onClick={() => setParams({ q: query, offset: String(offset + PAGE_SIZE) })}
              >
                Next
              </Button>
            </div>
          ) : null}
        </Card>
      )}
    </div>
  )
}
