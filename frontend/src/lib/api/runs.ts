import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { API_BASE, ApiError, apiRequest } from './client'
import { keys, messageSchema } from './workspace'

export const runSchema = z.object({
  id: z.string(),
  conversation_id: z.string(),
  kind: z.string(),
  status: z.enum(['queued', 'running', 'succeeded', 'failed', 'cancelled']),
  model_profile: z.string(),
  provider: z.string(),
  model_id: z.string(),
  model_revision: z.string().nullable(),
  prompt_version: z.string(),
  config_hash: z.string(),
  context_until_sequence: z.number(),
  context_message_count: z.number().nullable(),
  input_tokens: z.number().nullable(),
  output_tokens: z.number().nullable(),
  estimated_cost_usd: z.number().nullable(),
  time_to_first_token_ms: z.number().nullable(),
  latency_ms: z.number().nullable(),
  finish_reason: z.string().nullable(),
  error_code: z.string().nullable(),
  error_message: z.string().nullable(),
  created_at: z.string(),
  started_at: z.string().nullable(),
  finished_at: z.string().nullable(),
})
export type Run = z.infer<typeof runSchema>

export const modelProfileSchema = z.object({
  id: z.string(),
  label: z.string(),
  provider: z.string(),
  model: z.string(),
  deployment: z.string(),
  data_policy: z.string(),
  context_window: z.number(),
  max_output_tokens: z.number(),
  license: z.string(),
  price_input_per_mtok: z.number(),
  price_output_per_mtok: z.number(),
  notes: z.string().nullable(),
  is_default: z.boolean(),
  blocked_reason: z.string().nullable(),
  available: z.boolean(),
  unavailable_reason: z.string().nullable(),
  revision: z.string().nullable(),
})
export type ModelProfile = z.infer<typeof modelProfileSchema>

const modelListSchema = z.object({
  default_profile: z.string(),
  allow_external_models: z.boolean(),
  items: z.array(modelProfileSchema),
})

export const ACTIVE_STATUSES: Record<Run['status'], boolean> = {
  queued: true,
  running: true,
  succeeded: false,
  failed: false,
  cancelled: false,
}

const runKeys = {
  forConversation: (conversationId: string) => ['conversations', conversationId, 'runs'] as const,
  run: (id: string) => ['runs', id] as const,
}

export function useModels() {
  return useQuery({
    queryKey: ['models'],
    queryFn: ({ signal }) => apiRequest('GET', '/models', modelListSchema, { signal }),
    staleTime: 30_000,
  })
}

export function useConversationRuns(conversationId: string) {
  return useQuery({
    queryKey: runKeys.forConversation(conversationId),
    queryFn: ({ signal }) =>
      apiRequest('GET', `/conversations/${conversationId}/runs?limit=5`, z.array(runSchema), { signal }),
  })
}

export function useRun(runId: string, enabled: boolean) {
  return useQuery({
    queryKey: runKeys.run(runId),
    queryFn: ({ signal }) => apiRequest('GET', `/runs/${runId}`, runSchema, { signal }),
    enabled,
    staleTime: Infinity,
  })
}

export function useStartRun(conversationId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (modelProfile: string | null) =>
      apiRequest('POST', `/chat/${conversationId}/runs`, runSchema, {
        body: modelProfile ? { model_profile: modelProfile } : {},
      }),
    onSuccess: (run) => {
      queryClient.setQueryData<Run[]>(runKeys.forConversation(conversationId), (old = []) => [run, ...old])
    },
  })
}

export function useCancelRun() {
  return useMutation({
    mutationFn: (runId: string) => apiRequest('POST', `/runs/${runId}/cancel`, runSchema),
  })
}

/** Refresh everything a finished run changes: the thread, the conversation counters and the run list. */
export function useRunFinished(conversationId: string) {
  const queryClient = useQueryClient()
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.messages(conversationId) }),
      queryClient.invalidateQueries({ queryKey: keys.conversation(conversationId), exact: true }),
      queryClient.invalidateQueries({ queryKey: runKeys.forConversation(conversationId) }),
    ])
}

// --- server-sent events ---

const runEventSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('status'), data: z.object({ status: z.string() }).passthrough() }),
  z.object({ type: z.literal('delta'), data: z.object({ text: z.string() }) }),
  z.object({
    type: z.literal('completed'),
    data: z.object({ run: runSchema.nullable(), message: messageSchema.nullable() }),
  }),
  z.object({
    type: z.literal('failed'),
    data: z.object({ error_code: z.string(), message: z.string(), run: runSchema.nullable().optional() }),
  }),
  z.object({ type: z.literal('cancelled'), data: z.object({ run: runSchema.nullable() }) }),
])
export type RunEvent = z.infer<typeof runEventSchema>

const TERMINAL: Record<string, true> = { completed: true, failed: true, cancelled: true }
const MAX_RECONNECTS = 5

/**
 * Follow a run's SSE stream with fetch (testable, and lets us send Last-Event-ID on reconnect).
 * Resolves after a terminal event; reconnects from the last seen id if the connection drops first.
 */
export async function followRunEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
  signal: AbortSignal,
  sleep: (ms: number) => Promise<void> = (ms) => {
    const { promise, resolve } = Promise.withResolvers<void>()
    setTimeout(resolve, ms)
    return promise
  },
): Promise<void> {
  let lastId: string | null = null
  for (let attempt = 0; attempt <= MAX_RECONNECTS; attempt++) {
    if (attempt > 0) await sleep(1000 * attempt)
    let response: Response
    try {
      response = await fetch(`${API_BASE}/runs/${runId}/events`, {
        headers: { Accept: 'text/event-stream', ...(lastId ? { 'Last-Event-ID': lastId } : {}) },
        signal,
      })
    } catch (error) {
      if (signal.aborted) return
      if (attempt === MAX_RECONNECTS) throw error
      continue
    }
    if (!response.ok || !response.body) {
      throw new ApiError(`Run stream failed with HTTP ${response.status}`, response.status, null, null)
    }
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
    let buffer = ''
    try {
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += value
        let boundary: number
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          let type = ''
          let data = ''
          for (const line of block.split('\n')) {
            if (line.startsWith('id: ')) lastId = line.slice(4)
            else if (line.startsWith('event: ')) type = line.slice(7)
            else if (line.startsWith('data: ')) data += line.slice(6)
          }
          if (!type) continue // keep-alive comment
          const parsed = runEventSchema.safeParse({ type, data: JSON.parse(data) })
          if (!parsed.success) throw new ApiError('Run stream sent an unexpected event', null, null, null)
          onEvent(parsed.data)
          if (TERMINAL[type]) return
        }
      }
    } catch (error) {
      if (signal.aborted) return
      if (error instanceof ApiError) throw error
      // network drop mid-stream: reconnect below
    }
  }
  throw new ApiError('Lost connection to the response stream', null, null, null)
}
