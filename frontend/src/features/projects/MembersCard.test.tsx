import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, renderRoute } from '../../test/utils'
import { MembersCard } from './MembersCard'

const now = '2026-10-23T10:00:00Z'
const me = { id: 'u-1', email: 'ada@example.org', display_name: 'Ada', is_admin: false, created_at: now }
const owner = { user_id: 'u-1', email: 'ada@example.org', display_name: 'Ada', role: 'owner', added_at: now }
const colleague = { user_id: 'u-2', email: 'bob@example.org', display_name: 'Bob', role: 'editor', added_at: now }

function renderCard(isOwner = true) {
  return renderRoute(<MembersCard projectId="p-1" isOwner={isOwner} />, {
    path: '/projects/:projectId',
    url: '/projects/p-1',
  })
}

describe('MembersCard', () => {
  it('lets an owner add a member by email with a role', async () => {
    let members = [owner]
    const posted: unknown[] = []
    mockApi({
      'GET /api/v1/auth/me': () => jsonResponse(me),
      'GET /api/v1/projects/p-1/members': () => jsonResponse(members),
      'POST /api/v1/projects/p-1/members': (body) => {
        posted.push(body)
        members = [...members, colleague]
        return jsonResponse(colleague, 201)
      },
    })
    const user = userEvent.setup()
    renderCard()

    await user.type(await screen.findByLabelText('Member email'), 'bob@example.org')
    await user.selectOptions(screen.getByLabelText('Role for new member'), 'editor')
    await user.click(screen.getByRole('button', { name: 'Add member' }))

    expect(await screen.findByText('bob@example.org')).toBeInTheDocument()
    expect(posted).toEqual([{ email: 'bob@example.org', role: 'editor' }])
    expect(screen.getByLabelText('Member email')).toHaveValue('')
  })

  it('shows the server refusal when the last owner would be demoted', async () => {
    mockApi({
      'GET /api/v1/auth/me': () => jsonResponse(me),
      'GET /api/v1/projects/p-1/members': () => jsonResponse([owner, colleague]),
      'PATCH /api/v1/projects/p-1/members/u-1': () =>
        jsonResponse(
          { error: { code: 'last_owner', message: 'A project must keep at least one owner', trace_id: 't' } },
          409,
        ),
    })
    const user = userEvent.setup()
    renderCard()

    const role = await screen.findByLabelText('Role for ada@example.org')
    await user.selectOptions(role, 'viewer')

    expect(await screen.findByRole('alert')).toHaveTextContent('A project must keep at least one owner')
    expect(role).toHaveValue('owner')
  })

  it('lets any member leave and returns to the project list', async () => {
    const fetchMock = mockApi({
      'GET /api/v1/auth/me': () => jsonResponse(me),
      'GET /api/v1/projects/p-1/members': () => jsonResponse([{ ...owner, role: 'viewer' }, colleague]),
      'DELETE /api/v1/projects/p-1/members/u-1': () => jsonResponse(null, 204),
    })
    vi.spyOn(window, 'confirm').mockReturnValueOnce(true)
    const user = userEvent.setup()
    const { router } = renderCard(false)

    expect(await screen.findByText('bob@example.org')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove bob@example.org' })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Leave project' }))

    expect(await screen.findByText('navigated away')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/projects')
    expect(fetchMock.mock.calls.some(([input, init]) => init?.method === 'DELETE' && String(input).endsWith('/u-1'))).toBe(
      true,
    )
  })
})
