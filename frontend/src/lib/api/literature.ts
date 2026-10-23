import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { z } from 'zod'
import { apiRequest } from './client'

export const QUERY_MAX_LENGTH = 500
export const PAGE_SIZE = 20

export const literatureRecordSchema = z.object({
  source: z.literal('pubmed'),
  source_id: z.string(),
  pmid: z.string(),
  pmcid: z.string().nullable(),
  doi: z.string().nullable(),
  title: z.string(),
  abstract: z.string().nullable(),
  abstract_sections: z.array(z.object({ label: z.string().nullable(), text: z.string() })),
  authors: z.array(z.string()),
  journal: z.string().nullable(),
  published_year: z.number().nullable(),
  published_date: z.string().nullable(),
  publication_types: z.array(z.string()),
  source_url: z.string(),
  links: z.object({ pubmed: z.string(), doi: z.string().nullable(), pmc: z.string().nullable() }),
  retrieved_at: z.string(),
  raw_payload_sha256: z.string(),
  normalizer_version: z.string(),
})
export type LiteratureRecord = z.infer<typeof literatureRecordSchema>

export const literatureSearchSchema = z.object({
  search_id: z.string(),
  source: z.literal('pubmed'),
  query: z.string(),
  query_translation: z.string().nullable(),
  total_count: z.number(),
  limit: z.number(),
  offset: z.number(),
  retrieved_at: z.string(),
  items: z.array(literatureRecordSchema),
})
export type LiteratureSearch = z.infer<typeof literatureSearchSchema>

const recordKey = (pmid: string) => ['literature', 'pubmed', pmid] as const

/**
 * Each search is a provider call that is also written to the audit log, so results are fetched exactly once
 * per (query, offset) and never silently re-run on focus/reconnect. Upstream retries happen server-side.
 */
export function useLiteratureSearch(query: string, offset: number) {
  return useQuery({
    queryKey: ['literature', 'search', { query, offset }],
    queryFn: ({ signal }) =>
      apiRequest('POST', '/literature/search', literatureSearchSchema, {
        body: { query, limit: PAGE_SIZE, offset },
        signal,
      }),
    enabled: query.length > 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  })
}

export function useLiteratureRecord(pmid: string) {
  return useQuery({
    queryKey: recordKey(pmid),
    queryFn: ({ signal }) => apiRequest('GET', `/literature/pubmed/${pmid}`, literatureRecordSchema, { signal }),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  })
}

export function useRefreshLiteratureRecord(pmid: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiRequest('GET', `/literature/pubmed/${pmid}?refresh=true`, literatureRecordSchema),
    onSuccess: (record) => queryClient.setQueryData(recordKey(pmid), record),
  })
}
