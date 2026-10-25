import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import type { Claim } from '../../lib/api/retrieval'
import { jsonResponse, mockApi, renderRoute } from '../../test/utils'
import { EvidenceBoardPage } from './EvidenceBoardPage'
import { EvidenceSearch } from './EvidenceSearch'

const now = '2026-10-24T10:00:00Z'

function claim(overrides: Partial<Claim> = {}): Claim {
  return {
    id: 'cl-1',
    project_id: 'p-1',
    run_id: 'r-1',
    message_id: 'm-2',
    conversation_id: 'c-1',
    conversation_title: 'Scoping',
    question: 'Which TP53 mutations were found?',
    ordinal: 0,
    claim_text: 'SGS1 carried distinct R175H and R110C subclones.',
    support_state: 'cited',
    review_status: 'requires_review',
    review_note: null,
    answer_class: 'supported',
    model_id: 'qwen2.5:7b',
    extraction_version: 'citation-markers-v1',
    created_at: now,
    reviewed_at: null,
    evidence: [
      {
        label: 'E1',
        stance: 'cited',
        chunk_id: 'ch-1',
        text: 'Two mutations, R110C and R175H, were identified.',
        section: 'results',
        section_title: 'Results',
        document_id: 'doc-1',
        document_kind: 'fulltext',
        document_version: 1,
        external_id: '29416795',
        source_title: 'Treatment-associated TP53 mutations',
        published_year: 2018,
      },
    ],
    ...overrides,
  }
}

describe('EvidenceBoardPage', () => {
  it('shows claims with their passages, flags uncited ones and saves a review', async () => {
    const claims = [claim(), claim({ id: 'cl-2', claim_text: 'They respond to a new drug.', support_state: 'uncited', evidence: [] })]
    const fetchMock = mockApi({
      'GET /api/v1/projects/p-1': () =>
        jsonResponse({
          id: 'p-1',
          title: 'TP53',
          description: null,
          conversation_count: 1,
          created_at: now,
          updated_at: now,
          role: 'editor',
        }),
      'GET /api/v1/projects/p-1/claims': () => jsonResponse(claims),
      'PATCH /api/v1/claims/cl-1': (body) => {
        const { review_status } = z.object({ review_status: z.string() }).parse(body)
        return jsonResponse(claim({ review_status: z.enum(['verified']).parse(review_status) }))
      },
    })
    const user = userEvent.setup()
    renderRoute(<EvidenceBoardPage />, { path: '/projects/:projectId/evidence-board', url: '/projects/p-1/evidence-board' })

    expect(await screen.findByText(claims[0].claim_text)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Treatment-associated TP53 mutations' })).toHaveAttribute(
      'href',
      '/documents/doc-1#chunk-ch-1',
    )
    expect(screen.getByText('No passage cited for this sentence.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Export CSV' })).toHaveAttribute('href', '/api/v1/projects/p-1/claims?format=csv')

    await user.selectOptions(screen.getByLabelText(/Review status for: SGS1/), 'verified')

    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true))
  })
})

describe('EvidenceSearch', () => {
  it('shows ranks per retriever, rerank grades and degraded-mode warnings, and sends the chosen reranker', async () => {
    const fetchMock = mockApi({
      'GET /api/v1/models': () =>
        jsonResponse({
          default_profile: 'local-small',
          allow_external_models: false,
          items: [],
          rerank_profiles: [
            { id: 'llm-listwise-general', label: 'LLM rerank (7B)', generation_profile: 'local-general', is_default: false },
          ],
        }),
      'POST /api/v1/retrieval/query': () =>
        jsonResponse({
          query: 'R175H',
          mode: 'keyword',
          retrieval_version: 'keyword-v1',
          embedding_space: null,
          min_similarity: null,
          latency_ms: 12,
          reranker: 'llm-listwise-v1',
          rerank_ms: 2100,
          warnings: ['Semantic retrieval unavailable: Model nomic-embed-text is not pulled'],
          hits: [
            {
              rank: 1,
              chunk_id: 'ch-1',
              text: 'R175H subclones expanded.',
              section: 'results',
              section_title: 'Results',
              start_offset: 0,
              end_offset: 25,
              document_id: 'doc-1',
              document_kind: 'fulltext',
              document_version: 1,
              source_id: 's-1',
              external_id: '29416795',
              source_title: 'Paper',
              source_url: 'https://pubmed.ncbi.nlm.nih.gov/29416795/',
              published_year: 2018,
              journal: 'Oncotarget',
              keyword_rank: 1,
              keyword_score: 0.4,
              semantic_rank: null,
              similarity: null,
              fused_score: 0.016,
              rerank_score: 3,
              counts_as_evidence: true,
            },
          ],
        }),
    })
    const user = userEvent.setup()
    renderRoute(<EvidenceSearch projectId="p-1" />, { path: '/', url: '/' })

    await user.selectOptions(await screen.findByLabelText('Reranker'), 'llm-listwise-general')
    await user.type(screen.getByLabelText('Evidence query'), 'R175H')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Semantic retrieval unavailable')
    expect(screen.getByText(/keyword 1 · semantic –.*rerank 3\/3/)).toBeInTheDocument()
    expect(screen.getByText(/rerank 2100 ms/)).toBeInTheDocument()
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/retrieval/query'))
    const body = z
      .object({ project_id: z.string(), mode: z.string(), rerank_profile: z.string() })
      .parse(JSON.parse(String(call?.[1]?.body)))
    expect(body).toEqual({ project_id: 'p-1', mode: 'hybrid', rerank_profile: 'llm-listwise-general' })
  })
})
