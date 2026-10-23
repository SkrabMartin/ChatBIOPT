import type { z } from 'zod'

export const API_BASE = '/api/v1'

/** Raised for transport failures, non-2xx responses without a usable body, and contract violations. */
export class ApiError extends Error {
  readonly status: number | null
  readonly traceId: string | null

  constructor(message: string, status: number | null, traceId: string | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.traceId = traceId
  }
}

export interface ApiGetOptions {
  signal?: AbortSignal
  /** HTTP statuses (besides 2xx) whose body still satisfies `schema`, e.g. 503 from /health. */
  acceptStatuses?: readonly number[]
}

/** GET `path` relative to the API base and validate the JSON body against `schema`. */
export async function apiGet<T>(path: string, schema: z.ZodType<T>, options: ApiGetOptions = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: 'application/json' },
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('API is unreachable', null, null)
  }

  const traceId = response.headers.get('X-Request-ID')
  if (!response.ok && !options.acceptStatuses?.includes(response.status)) {
    throw new ApiError(`API responded with HTTP ${response.status}`, response.status, traceId)
  }

  let body: unknown
  try {
    body = await response.json()
  } catch {
    throw new ApiError('API returned a non-JSON response', response.status, traceId)
  }

  const parsed = schema.safeParse(body)
  if (!parsed.success) {
    throw new ApiError('API response did not match the expected contract', response.status, traceId)
  }
  return parsed.data
}
