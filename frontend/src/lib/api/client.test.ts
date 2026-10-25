import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import { jsonResponse, mockApi } from '../../test/utils'
import { apiRequest, apiRequestNoContent } from './client'

function sentHeaders(call: unknown[]): Record<string, string> {
  return (call[1] as RequestInit).headers as Record<string, string>
}

describe('apiRequest', () => {
  it('marks state-changing requests with the CSRF header, but not reads', async () => {
    const fetchMock = mockApi({
      'GET /api/v1/projects': () => jsonResponse({ ok: true }),
      'POST /api/v1/projects': () => jsonResponse({ ok: true }, 201),
      'DELETE /api/v1/projects/p-1': () => jsonResponse(null, 204),
    })
    const schema = z.object({ ok: z.boolean() })

    await apiRequest('GET', '/projects', schema)
    await apiRequest('POST', '/projects', schema, { body: { title: 'x' } })
    await apiRequestNoContent('DELETE', '/projects/p-1')

    const [get, post, del] = fetchMock.mock.calls
    expect(sentHeaders(get)).not.toHaveProperty('X-Requested-With')
    expect(sentHeaders(post)).toMatchObject({ 'X-Requested-With': 'BioResearchAI', 'Content-Type': 'application/json' })
    expect(sentHeaders(del)).toMatchObject({ 'X-Requested-With': 'BioResearchAI' })
  })
})
