import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { apiRequest, apiRequestNoContent } from './client'

const organismSchema = z.object({ taxon_id: z.number(), scientific_name: z.string(), common_name: z.string() })
export type Organism = z.infer<typeof organismSchema>

export const INPUT_KINDS = ['symbol', 'ncbigene', 'ensembl', 'hgnc', 'uniprot'] as const
export type InputKind = (typeof INPUT_KINDS)[number]

const matchTypeSchema = z.enum(['official_symbol', 'alias', 'identifier', 'replaced_identifier', 'cross_reference'])
export type MatchType = z.infer<typeof matchTypeSchema>

const candidateSchema = z.object({
  gene_id: z.string(),
  symbol: z.string(),
  name: z.string(),
  taxon_id: z.number(),
  organism: z.string(),
  match_type: matchTypeSchema,
  aliases: z.array(z.string()),
  chromosome: z.string().nullable(),
  map_location: z.string().nullable(),
  official_nomenclature: z.boolean(),
})
export type Candidate = z.infer<typeof candidateSchema>

const resolutionSchema = z.object({
  query: z.string(),
  taxon_id: z.number(),
  input_kind: z.enum(INPUT_KINDS),
  status: z.enum(['resolved', 'ambiguous', 'not_found', 'taxon_mismatch']),
  candidates: z.array(candidateSchema),
  found_elsewhere: z.array(
    z.object({
      namespace: z.string(),
      identifier: z.string(),
      label: z.string(),
      taxon_id: z.number(),
      organism: z.string(),
    }),
  ),
  warnings: z.array(z.string()),
  providers: z.array(z.object({ provider: z.string(), release: z.string().nullable() })),
  resolver_version: z.string(),
  retrieved_at: z.string(),
})
export type Resolution = z.infer<typeof resolutionSchema>

const xrefSchema = z.object({
  namespace: z.string(),
  id: z.string(),
  source: z.string(),
  release: z.string().nullable(),
  versions: z.array(z.string()),
  reciprocal: z.boolean().nullable(),
  checked_against: z.string().nullable(),
})
export type Xref = z.infer<typeof xrefSchema>

const entitySummarySchema = z.object({
  id: z.string(),
  kind: z.enum(['gene', 'protein', 'disease']),
  namespace: z.string(),
  accession: z.string(),
  taxon_id: z.number().nullable(),
  organism: z.string().nullable(),
  label: z.string(),
  name: z.string().nullable(),
  provider: z.string(),
  provider_release: z.string().nullable(),
  retrieved_at: z.string(),
})
export type EntitySummary = z.infer<typeof entitySummarySchema>

const entitySchema = entitySummarySchema.extend({
  xrefs: z.array(xrefSchema),
  attributes: z.record(z.string(), z.unknown()),
  raw_payload_sha256: z.string(),
})
export type Entity = z.infer<typeof entitySchema>

const edgeSchema = z.object({
  id: z.string(),
  predicate: z.enum(['encodes', 'associated_with_disease']),
  direction: z.enum(['out', 'in']),
  neighbor: entitySummarySchema,
  provider: z.string(),
  provider_release: z.string().nullable(),
  score: z.number().nullable(),
  evidence: z.record(z.string(), z.unknown()),
  retrieved_at: z.string(),
})
export type Edge = z.infer<typeof edgeSchema>

const entityDetailSchema = entitySchema.extend({ edges: z.array(edgeSchema) })
export type EntityDetail = z.infer<typeof entityDetailSchema>

const projectEntitySchema = z.object({
  entity: entitySchema,
  query: z.string().nullable(),
  match_type: matchTypeSchema.nullable(),
  warnings: z.array(z.string()),
  added_at: z.string(),
})
export type ProjectEntity = z.infer<typeof projectEntitySchema>

/** Disease-association status stored on a gene (`attributes.disease_associations`). */
export const associationStatusSchema = z
  .object({
    status: z.enum(['stored', 'not_covered', 'disabled', 'no_target', 'unavailable']),
    total: z.number().optional(),
    stored: z.number().optional(),
    data_version: z.string().optional(),
  })
  .passthrough()
export type AssociationStatus = z.infer<typeof associationStatusSchema>

export function useOrganisms() {
  return useQuery({
    queryKey: ['entities', 'organisms'],
    queryFn: ({ signal }) => apiRequest('GET', '/entities/organisms', z.array(organismSchema), { signal }),
    staleTime: Infinity,
  })
}

export interface ResolveInput {
  query: string
  taxon_id: number
  kind: 'auto' | InputKind
}

export function useResolveEntity() {
  return useMutation({
    mutationFn: (input: ResolveInput) => apiRequest('POST', '/entities/resolve', resolutionSchema, { body: input }),
  })
}

const projectEntitiesKey = (projectId: string) => ['projects', projectId, 'entities'] as const

export function useProjectEntities(projectId: string) {
  return useQuery({
    queryKey: projectEntitiesKey(projectId),
    queryFn: ({ signal }) =>
      apiRequest('GET', `/projects/${projectId}/entities`, z.array(projectEntitySchema), { signal }),
  })
}

export interface AddEntityInput {
  gene_id: string
  taxon_id: number
  query: string | null
  match_type: MatchType | null
}

export function useAddProjectEntity(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: AddEntityInput) =>
      apiRequest('POST', `/projects/${projectId}/entities`, projectEntitySchema, { body: input }),
    onSuccess: (pinned) => {
      void queryClient.invalidateQueries({ queryKey: projectEntitiesKey(projectId) })
      void queryClient.invalidateQueries({ queryKey: ['entities', pinned.entity.id] })
    },
  })
}

export function useRemoveProjectEntity(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (entityId: string) => apiRequestNoContent('DELETE', `/projects/${projectId}/entities/${entityId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: projectEntitiesKey(projectId) }),
  })
}

export function useEntity(entityId: string) {
  return useQuery({
    queryKey: ['entities', entityId],
    queryFn: ({ signal }) => apiRequest('GET', `/entities/${entityId}`, entityDetailSchema, { signal }),
  })
}

/** Link to the authoritative record of an identifier, or null if there is no stable public page. */
export function identifierUrl(namespace: string, id: string): string | null {
  switch (namespace) {
    case 'ncbigene':
      return `https://www.ncbi.nlm.nih.gov/gene/${id}`
    case 'uniprot':
      return `https://www.uniprot.org/uniprotkb/${id}/entry`
    case 'ensembl':
      return `https://www.ensembl.org/id/${id}`
    case 'hgnc':
      return `https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/${id}`
    case 'omim':
      return `https://omim.org/entry/${id}`
    case 'opentargets':
      return `https://platform.opentargets.org/disease/${id}`
    default:
      return null
  }
}

export function taxonomyUrl(taxonId: number): string {
  return `https://www.ncbi.nlm.nih.gov/Taxonomy/Browser/wwwtax.cgi?id=${taxonId}`
}
