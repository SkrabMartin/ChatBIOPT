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
  role: 'owner',
}
const emptyPage = { items: [], total: 0, limit: 50, offset: 0 }
const me = { id: 'u-1', email: 'ada@example.org', display_name: 'Ada', is_admin: false, created_at: now }
const member = { user_id: 'u-1', email: 'ada@example.org', display_name: 'Ada', role: 'owner', added_at: now }

/** Everything the project page loads besides the project itself. */
const workspaceRoutes = {
  'GET /api/v1/auth/me': () => jsonResponse(me),
  'GET /api/v1/projects/p-1/conversations': () => jsonResponse(emptyPage),
  'GET /api/v1/projects/p-1/sources': () => jsonResponse({ items: [], pending_jobs: [] }),
  'GET /api/v1/projects/p-1/entities': () => jsonResponse([]),
  'GET /api/v1/entities/organisms': () => jsonResponse([]),
  'GET /api/v1/projects/p-1/members': () => jsonResponse([member]),
}

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
      ...workspaceRoutes,
      'GET /api/v1/projects/p-1': () => jsonResponse(project),
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

  it('hides project management and write controls from viewers', async () => {
    mockApi({
      ...workspaceRoutes,
      'GET /api/v1/projects/p-1': () => jsonResponse({ ...project, role: 'viewer' }),
      'GET /api/v1/projects/p-1/members': () => jsonResponse([{ ...member, role: 'viewer' }]),
    })

    renderRoute(<ProjectPage />, { path: '/projects/:projectId', url: '/projects/p-1' })

    expect(await screen.findByRole('button', { name: 'Leave project' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('New conversation title')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Member email')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Role for ada@example.org')).not.toBeInTheDocument()
  })
})
