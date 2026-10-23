import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'
import { apiRequest } from './client'

export const healthSchema = z.object({
  status: z.enum(['ok', 'degraded']),
  version: z.string(),
  environment: z.string(),
  database: z.object({
    status: z.enum(['ok', 'unavailable']),
    migration_revision: z.string().nullable(),
    latency_ms: z.number().nullable(),
  }),
})

export type Health = z.infer<typeof healthSchema>

export function fetchHealth(signal?: AbortSignal): Promise<Health> {
  // 503 carries a valid "degraded" report, which the UI should render rather than treat as an error.
  return apiRequest('GET', '/health', healthSchema, { signal, acceptStatuses: [503] })
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => fetchHealth(signal),
    refetchInterval: 15_000,
  })
}
