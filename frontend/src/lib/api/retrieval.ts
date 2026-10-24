import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { API_BASE, apiRequest } from './client'

const hitSchema = z.object({
  rank: z.number(),
  chunk_id: z.string(),
  text: z.string(),
  section: z.string(),
  section_title: z.string().nullable(),
  start_offset: z.number(),
  end_offset: z.number(),
  document_id: z.string(),
  document_kind: z.string(),
  document_version: z.number(),
  source_id: z.string(),
  external_id: z.string(),
  source_title: z.string(),
  source_url: z.string(),
  published_year: z.number().nullable(),
  journal: z.string().nullable(),
  keyword_rank: z.number().nullable(),
  keyword_score: z.number().nullable(),
  semantic_rank: z.number().nullable(),
  similarity: z.number().nullable(),
  fused_score: z.number(),
  counts_as_evidence: z.boolean(),
})
export type RetrievalHit = z.infer<typeof hitSchema>

export type RetrievalMode = 'keyword' | 'semantic' | 'hybrid'

const retrievalSchema = z.object({
  query: z.string(),
  mode: z.enum(['keyword', 'semantic', 'hybrid']),
  retrieval_version: z.string(),
  embedding_space: z.string().nullable(),
  min_similarity: z.number().nullable(),
  latency_ms: z.number(),
  warnings: z.array(z.string()),
  hits: z.array(hitSchema),
})
export type RetrievalResult = z.infer<typeof retrievalSchema>

export function useRetrieval(projectId: string) {
  return useMutation({
    mutationFn: ({ query, mode }: { query: string; mode: RetrievalMode }) =>
      apiRequest('POST', '/retrieval/query', retrievalSchema, {
        body: { project_id: projectId, query, mode, top_k: 10 },
      }),
  })
}

export const REVIEW_STATUSES = ['requires_review', 'verified', 'disputed', 'rejected'] as const
export type ReviewStatus = (typeof REVIEW_STATUSES)[number]

const claimSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  run_id: z.string(),
  message_id: z.string(),
  conversation_id: z.string(),
  conversation_title: z.string(),
  question: z.string().nullable(),
  ordinal: z.number(),
  claim_text: z.string(),
  support_state: z.enum(['cited', 'uncited']),
  review_status: z.enum(REVIEW_STATUSES),
  review_note: z.string().nullable(),
  answer_class: z.string().nullable(),
  model_id: z.string(),
  extraction_version: z.string(),
  created_at: z.string(),
  reviewed_at: z.string().nullable(),
  evidence: z.array(
    z.object({
      label: z.string(),
      stance: z.string(),
      chunk_id: z.string(),
      text: z.string(),
      section: z.string(),
      section_title: z.string().nullable(),
      document_id: z.string(),
      document_kind: z.string(),
      document_version: z.number(),
      external_id: z.string(),
      source_title: z.string(),
      published_year: z.number().nullable(),
    }),
  ),
})
export type Claim = z.infer<typeof claimSchema>

const claimsKey = (projectId: string) => ['projects', projectId, 'claims'] as const

export function useClaims(projectId: string, reviewStatus: ReviewStatus | null) {
  return useQuery({
    queryKey: [...claimsKey(projectId), reviewStatus],
    queryFn: ({ signal }) =>
      apiRequest(
        'GET',
        `/projects/${projectId}/claims${reviewStatus ? `?review_status=${reviewStatus}` : ''}`,
        z.array(claimSchema),
        { signal },
      ),
  })
}

export function claimsCsvUrl(projectId: string): string {
  return `${API_BASE}/projects/${projectId}/claims?format=csv`
}

export function useReviewClaim(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ claimId, status }: { claimId: string; status: ReviewStatus }) =>
      apiRequest('PATCH', `/claims/${claimId}`, claimSchema, { body: { review_status: status } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: claimsKey(projectId) }),
  })
}
