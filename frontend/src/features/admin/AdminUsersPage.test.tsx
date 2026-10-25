import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { AdminUser } from '../../lib/api/admin'
import { jsonResponse, mockApi, renderRoute } from '../../test/utils'
import { AdminUsersPage } from './AdminUsersPage'

const now = '2026-10-23T10:00:00Z'
const admin = { id: 'u-1', email: 'admin@example.org', display_name: 'Admin', is_admin: true, created_at: now }
const adminRow: AdminUser = { ...admin, disabled: false, last_login_at: now }

function renderPage() {
  return renderRoute(<AdminUsersPage />, { path: '/admin/users', url: '/admin/users' })
}

describe('AdminUsersPage', () => {
  it('creates a user and lists it', async () => {
    let users = [adminRow]
    const created: unknown[] = []
    mockApi({
      'GET /api/v1/auth/me': () => jsonResponse(admin),
      'GET /api/v1/admin/users': () => jsonResponse(users),
      'POST /api/v1/admin/users': (body) => {
        created.push(body)
        const row = { id: 'u-2', email: 'bob@example.org', display_name: 'Bob', is_admin: true, disabled: false, created_at: now, last_login_at: null }
        users = [...users, row]
        return jsonResponse(row, 201)
      },
    })
    const user = userEvent.setup()
    renderPage()

    await user.type(screen.getByLabelText('Email'), 'bob@example.org')
    await user.type(screen.getByLabelText('Display name'), 'Bob')
    await user.type(screen.getByLabelText('Initial password'), 'twelve chars ok')
    await user.click(screen.getByLabelText('Administrator'))
    await user.click(screen.getByRole('button', { name: 'Create user' }))

    const row = (await screen.findByRole('cell', { name: 'bob@example.org' })).closest('tr')!
    expect(within(row).getByText('Never')).toBeInTheDocument()
    expect(created).toEqual([
      { email: 'bob@example.org', display_name: 'Bob', password: 'twelve chars ok', is_admin: true },
    ])
  })

  it('disables a user from its row, but not the admin themself', async () => {
    const bob = { id: 'u-2', email: 'bob@example.org', display_name: 'Bob', is_admin: false, disabled: false, created_at: now, last_login_at: null }
    const patches: unknown[] = []
    mockApi({
      'GET /api/v1/auth/me': () => jsonResponse(admin),
      'GET /api/v1/admin/users': () => jsonResponse([adminRow, bob]),
      'PATCH /api/v1/admin/users/u-2': (body) => {
        patches.push(body)
        return jsonResponse({ ...bob, disabled: true })
      },
    })
    const user = userEvent.setup()
    renderPage()

    const bobRow = (await screen.findByRole('cell', { name: 'bob@example.org' })).closest('tr')!
    const selfRow = screen.getByRole('cell', { name: /admin@example\.org/ }).closest('tr')!
    await waitFor(() => expect(within(selfRow).getByRole('button', { name: 'Disable' })).toBeDisabled())
    await user.click(within(bobRow).getByRole('button', { name: 'Disable' }))

    await waitFor(() => expect(patches).toEqual([{ disabled: true }]))
  })

  it('tells non-admins the page is for admins only', async () => {
    mockApi({
      'GET /api/v1/auth/me': () => jsonResponse({ ...admin, is_admin: false }),
      'GET /api/v1/admin/users': () =>
        jsonResponse({ error: { code: 'forbidden', message: 'Admin rights required', trace_id: 't' } }, 403),
    })
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Admins only')
    expect(screen.queryByRole('button', { name: 'Create user' })).not.toBeInTheDocument()
  })
})
