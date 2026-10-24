import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Card } from '../../components/ui'
import {
  REVIEW_STATUSES,
  claimsCsvUrl,
  useClaims,
  useReviewClaim,
  type Claim,
  type ReviewStatus,
} from '../../lib/api/retrieval'
import { useProject } from '../../lib/api/workspace'
import { formatDateTime } from '../../lib/format'

const reviewLabel: Record<ReviewStatus, string> = {
  requires_review: 'Needs review',
  verified: 'Verified',
  disputed: 'Disputed',
  rejected: 'Rejected',
}

const reviewTone: Record<ReviewStatus, string> = {
  requires_review: 'border-slate-200',
  verified: 'border-emerald-300 bg-emerald-50/40',
  disputed: 'border-amber-300 bg-amber-50/40',
  rejected: 'border-rose-300 bg-rose-50/40',
}

function ClaimRow({ claim, projectId }: { claim: Claim; projectId: string }) {
  const review = useReviewClaim(projectId)
  return (
    <li className={`space-y-2 rounded-md border p-3 ${reviewTone[claim.review_status]}`}>
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm text-slate-900">{claim.claim_text}</p>
        <label className="flex shrink-0 items-center gap-1.5 text-xs text-slate-500">
          <span className="sr-only">Review status</span>
          <select
            value={claim.review_status}
            onChange={(e) => {
              const status = REVIEW_STATUSES.find((s) => s === e.target.value)
              if (status) review.mutate({ claimId: claim.id, status })
            }}
            disabled={review.isPending}
            aria-label={`Review status for: ${claim.claim_text.slice(0, 60)}`}
            className="rounded border border-slate-200 bg-white px-1.5 py-1 text-xs text-slate-700"
          >
            {REVIEW_STATUSES.map((s) => (
              <option key={s} value={s}>
                {reviewLabel[s]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {claim.evidence.length === 0 ? (
        <p className="text-xs text-rose-700">No passage cited for this sentence.</p>
      ) : (
        <ul className="space-y-1">
          {claim.evidence.map((e) => (
            <li key={e.chunk_id} className="text-xs text-slate-600">
              <span className="font-mono text-teal-800">{e.label}</span> ·{' '}
              <Link to={`/documents/${e.document_id}#chunk-${e.chunk_id}`} className="text-teal-700 hover:underline">
                {e.source_title}
              </Link>{' '}
              · PMID {e.external_id}
              {e.published_year ? ` · ${e.published_year}` : ''} · {e.section_title ?? e.section}
              <details className="mt-0.5">
                <summary className="cursor-pointer text-slate-400">Passage</summary>
                <p className="whitespace-pre-wrap text-slate-700">{e.text}</p>
              </details>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-slate-400">
        From{' '}
        <Link to={`/projects/${projectId}/conversations/${claim.conversation_id}`} className="hover:underline">
          {claim.conversation_title}
        </Link>
        {claim.question ? ` · Q: “${claim.question.slice(0, 120)}”` : ''} · {claim.model_id} ·{' '}
        {formatDateTime(claim.created_at)}
      </p>
      {review.error ? <ErrorNotice error={review.error} title="Could not save review" /> : null}
    </li>
  )
}

export function EvidenceBoardPage() {
  const { projectId = '' } = useParams()
  const project = useProject(projectId)
  const [filter, setFilter] = useState<ReviewStatus | null>(null)
  const claims = useClaims(projectId, filter)

  return (
    <div className="space-y-6">
      <nav className="text-sm text-slate-500">
        <Link to="/projects" className="hover:underline">
          Projects
        </Link>{' '}
        /{' '}
        <Link to={`/projects/${projectId}`} className="hover:underline">
          {project.data?.title ?? 'Project'}
        </Link>{' '}
        / Evidence board
      </nav>
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Evidence board</h1>
          <p className="mt-1 text-sm text-slate-500">
            Every sentence of grounded answers with the passages it cites. A citation shows what the model pointed
            at; whether the passage really supports the claim is your review.
          </p>
        </div>
        <a
          href={claimsCsvUrl(projectId)}
          className="shrink-0 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          Export CSV
        </a>
      </header>
      <Card>
        <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Filter by review status">
          {[null, ...REVIEW_STATUSES].map((s) => (
            <button
              key={s ?? 'all'}
              type="button"
              onClick={() => setFilter(s)}
              aria-pressed={filter === s}
              className={`rounded-full border px-2.5 py-0.5 text-xs ${filter === s ? 'border-teal-600 bg-teal-50 text-teal-800' : 'border-slate-200 text-slate-600'}`}
            >
              {s ? reviewLabel[s] : 'All'}
            </button>
          ))}
        </div>
        {claims.isPending ? (
          <p className="text-sm text-slate-500">Loading…</p>
        ) : claims.error ? (
          <ErrorNotice error={claims.error} title="Could not load claims" />
        ) : claims.data.length === 0 ? (
          <p className="text-sm text-slate-500">No claims yet. Ask a question in a conversation of this project.</p>
        ) : (
          <ol className="space-y-3">
            {claims.data.map((claim) => (
              <ClaimRow key={claim.id} claim={claim} projectId={projectId} />
            ))}
          </ol>
        )}
      </Card>
    </div>
  )
}
