import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, renderRoute } from '../../test/utils'
import { ProjectPage } from './ProjectPage'
import { ProjectsPage } from './ProjectsPage'

const now = '2026-10-23T10:00:00Z'
const project = {
  id: 'p-1',
  title: 'TP53 in glioblastoma',
  description: 'Functional effects',
  conversation_count: 0,
  created_at: now,
  updated_at: now,
}
const emptyPage = { items: [], total: 0, limit: 50, offset: 0 }

describe('ProjectsPage', () => {
  it('shows the validation reason returned by the server', async () => {
    mockApi({
      'GET /api/v1/projects': () => jsonResponse(emptyPage),
      'POST /api/v1/projects': () =>
        jsonResponse(
          {
            error: {
              code: 'validation_error',
              message: 'Request validation failed',
              trace_id: 'trace-test',
              details: [{ loc: ['body', 'title'], msg: 'String should have at least 1 character', type: 'x' }],
            },
          },
          422,
        ),
    })
    const user = userEvent.setup()
    renderRoute(<ProjectsPage />, { path: '/projects', url: '/projects' })

    await user.type(await screen.findByLabelText('Title'), 'x')
    await user.click(screen.getByRole('button', { name: 'Create project' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('title: String should have at least 1 character')
  })
})

describe('ProjectPage', () => {
  it('reports a missing project instead of rendering an empty workspace', async () => {
    mockApi({
      'GET /api/v1/projects/p-1': () =>
        jsonResponse({ error: { code: 'not_found', message: 'Project not found', trace_id: 't' } }, 404),
      'GET /api/v1/projects/p-1/conversations': () =>
        jsonResponse({ error: { code: 'not_found', message: 'Project not found', trace_id: 't' } }, 404),
    })

    renderRoute(<ProjectPage />, { path: '/projects/:projectId', url: '/projects/p-1' })

    expect(await screen.findByText('Project not found', { selector: 'p.font-medium' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
  })

  it('deletes only after confirmation, then leaves the page', async () => {
    const fetchMock = mockApi({
      'GET /api/v1/projects/p-1': () => jsonResponse(project),
      'GET /api/v1/projects/p-1/conversations': () => jsonResponse(emptyPage),
      'GET /api/v1/projects/p-1/sources': () => jsonResponse({ items: [], pending_jobs: [] }),
      'GET /api/v1/projects/p-1/entities': () => jsonResponse([]),
      'GET /api/v1/entities/organisms': () => jsonResponse([]),
      'DELETE /api/v1/projects/p-1': () => jsonResponse(null, 204),
    })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    const user = userEvent.setup()
    const { router } = renderRoute(<ProjectPage />, { path: '/projects/:projectId', url: '/projects/p-1' })

    const del = await screen.findByRole('button', { name: 'Delete' })
    await user.click(del)
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)

    await user.click(del)
    expect(await screen.findByText('navigated away')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/projects')
    expect(confirm).toHaveBeenCalledTimes(2)
  })
})
