import { z } from 'zod'

export const API_BASE = '/api/v1'

/** Raised for transport failures, non-2xx responses and contract violations. */
export class ApiError extends Error {
  readonly status: number | null
  readonly code: string | null
  readonly traceId: string | null

  constructor(message: string, status: number | null, code: string | null, traceId: string | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.traceId = traceId
  }
}

const errorEnvelopeSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    trace_id: z.string().nullable().optional(),
    details: z.array(z.object({ loc: z.array(z.union([z.string(), z.number()])), msg: z.string() })).optional(),
  }),
})

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE'

/** Header the backend requires on every state-changing request (CSRF defence for the session cookie). */
export const CSRF_HEADER = 'X-Requested-With'
export const CSRF_VALUE = 'BioResearchAI'

export interface RequestOptions {
  body?: unknown
  signal?: AbortSignal
  /** HTTP statuses (besides 2xx) whose body still satisfies the schema, e.g. 503 from /health. */
  acceptStatuses?: readonly number[]
}

function describeValidation(details: { loc: (string | number)[]; msg: string }[]): string {
  // Drop the leading "body"/"query" segment: users care about the field, not the transport location.
  return details.map((d) => `${d.loc.slice(1).join('.') || d.loc.join('.')}: ${d.msg}`).join('; ')
}

async function send(method: Method, path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers[CSRF_HEADER] = CSRF_VALUE
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('API is unreachable', null, null, null)
  }

  if (!response.ok && !options.acceptStatuses?.includes(response.status)) {
    const traceId = response.headers.get('X-Request-ID')
    const envelope = errorEnvelopeSchema.safeParse(await response.json().catch(() => null))
    if (envelope.success) {
      const { code, message, details } = envelope.data.error
      const text = details?.length ? `${message}: ${describeValidation(details)}` : message
      throw new ApiError(text, response.status, code, traceId)
    }
    throw new ApiError(`API responded with HTTP ${response.status}`, response.status, null, traceId)
  }
  return response
}

/** Send a request and validate the JSON response body against `schema`. */
export async function apiRequest<T>(
  method: Method,
  path: string,
  schema: z.ZodType<T>,
  options: RequestOptions = {},
): Promise<T> {
  const response = await send(method, path, options)
  const traceId = response.headers.get('X-Request-ID')
  let body: unknown
  try {
    body = await response.json()
  } catch {
    throw new ApiError('API returned a non-JSON response', response.status, null, traceId)
  }
  const parsed = schema.safeParse(body)
  if (!parsed.success) {
    throw new ApiError('API response did not match the expected contract', response.status, null, traceId)
  }
  return parsed.data
}

/** Send a request whose success response has no body (204). */
export async function apiRequestNoContent(method: Method, path: string, options: RequestOptions = {}): Promise<void> {
  await send(method, path, options)
}
