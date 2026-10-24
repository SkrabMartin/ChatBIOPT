import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { apiRequest, apiRequestNoContent } from './client'

const stepSchema = z
  .object({ step: z.string(), status: z.string(), detail: z.string(), at: z.string() })
  .passthrough()
export type IngestionStep = z.infer<typeof stepSchema>

export const ingestionJobSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  source_type: z.string(),
  external_id: z.string(),
  source_id: z.string().nullable(),
  status: z.enum(['queued', 'running', 'succeeded', 'failed']),
  attempts: z.number(),
  max_attempts: z.number(),
  run_after: z.string(),
  steps: z.array(stepSchema),
  error_code: z.string().nullable(),
  error_message: z.string().nullable(),
  created_at: z.string(),
  started_at: z.string().nullable(),
  finished_at: z.string().nullable(),
})
export type IngestionJob = z.infer<typeof ingestionJobSchema>

export const documentSummarySchema = z.object({
  id: z.string(),
  source_id: z.string(),
  kind: z.enum(['abstract', 'fulltext']),
  version: z.number(),
  access: z.enum(['metadata', 'open_access']),
  license: z.string().nullable(),
  license_url: z.string().nullable(),
  origin: z.string(),
  origin_url: z.string(),
  text_sha256: z.string(),
  text_length: z.number(),
  raw_sha256: z.string(),
  parser_version: z.string(),
  retrieved_at: z.string(),
  created_at: z.string(),
})
export type DocumentSummary = z.infer<typeof documentSummarySchema>

const sourceSummarySchema = z.object({
  id: z.string(),
  source_type: z.string(),
  external_id: z.string(),
  title: z.string(),
  url: z.string(),
  doi: z.string().nullable(),
  pmcid: z.string().nullable(),
  journal: z.string().nullable(),
  published_year: z.number().nullable(),
})

const libraryItemSchema = z.object({
  source: sourceSummarySchema,
  added_at: z.string(),
  documents: z.array(documentSummarySchema),
  chunk_counts: z.record(z.string(), z.number()),
  latest_job: ingestionJobSchema.nullable(),
})
export type LibraryItem = z.infer<typeof libraryItemSchema>

const librarySchema = z.object({ items: z.array(libraryItemSchema), pending_jobs: z.array(ingestionJobSchema) })
export type Library = z.infer<typeof librarySchema>

const addSourceResponseSchema = z.object({ job: ingestionJobSchema, created: z.boolean() })

const chunkSchema = z.object({
  id: z.string(),
  document_id: z.string(),
  chunker_version: z.string(),
  ordinal: z.number(),
  section: z.string(),
  section_title: z.string().nullable(),
  start_offset: z.number(),
  end_offset: z.number(),
  text: z.string(),
  token_estimate: z.number(),
})
export type Chunk = z.infer<typeof chunkSchema>

const documentSchema = documentSummarySchema.extend({
  text: z.string(),
  sections: z.array(
    z.object({ kind: z.string(), title: z.string().nullable(), start_offset: z.number(), end_offset: z.number() }),
  ),
  chunker_version: z.string(),
  chunks: z.array(chunkSchema),
  is_latest: z.boolean(),
  source: sourceSummarySchema,
})
export type DocumentDetail = z.infer<typeof documentSchema>

const ACTIVE: Record<IngestionJob['status'], boolean> = {
  queued: true,
  running: true,
  succeeded: false,
  failed: false,
}

export function hasActiveJobs(library: Library | undefined): boolean {
  if (!library) return false
  return (
    library.pending_jobs.some((j) => ACTIVE[j.status]) ||
    library.items.some((i) => i.latest_job !== null && ACTIVE[i.latest_job.status])
  )
}

const libraryKey = (projectId: string) => ['projects', projectId, 'library'] as const

export function useLibrary(projectId: string) {
  return useQuery({
    queryKey: libraryKey(projectId),
    queryFn: ({ signal }) => apiRequest('GET', `/projects/${projectId}/sources`, librarySchema, { signal }),
    // Poll only while the worker still has something to do for this project.
    refetchInterval: (query) => (hasActiveJobs(query.state.data) ? 2000 : false),
  })
}

export function useAddSource(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (pmid: string) =>
      apiRequest('POST', `/projects/${projectId}/sources`, addSourceResponseSchema, {
        body: { source: 'pubmed', identifier: pmid },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: libraryKey(projectId) }),
  })
}

export function useRefreshSource(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sourceId: string) =>
      apiRequest('POST', `/projects/${projectId}/sources/${sourceId}/refresh`, addSourceResponseSchema),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: libraryKey(projectId) }),
  })
}

export function useRemoveSource(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sourceId: string) => apiRequestNoContent('DELETE', `/projects/${projectId}/sources/${sourceId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: libraryKey(projectId) }),
  })
}

export function useDocument(documentId: string) {
  return useQuery({
    queryKey: ['documents', documentId],
    queryFn: ({ signal }) => apiRequest('GET', `/documents/${documentId}`, documentSchema, { signal }),
    staleTime: Infinity, // document versions are immutable
  })
}

export function useSourceDocuments(sourceId: string | undefined) {
  return useQuery({
    queryKey: ['sources', sourceId, 'documents'],
    queryFn: ({ signal }) =>
      apiRequest('GET', `/sources/${sourceId}/documents`, z.array(documentSummarySchema), { signal }),
    enabled: sourceId !== undefined,
  })
}
