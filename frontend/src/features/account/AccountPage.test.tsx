import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { jsonResponse, mockApi, renderRoute } from '../../test/utils'
import { AccountPage } from './AccountPage'

const me = { id: 'u-1', email: 'ada@example.org', display_name: 'Ada', is_admin: false, created_at: '2026-10-23T10:00:00Z' }

describe('AccountPage', () => {
  it('blocks a mismatched confirmation client-side, then changes the password', async () => {
    const changes: unknown[] = []
    mockApi({
      'GET /api/v1/auth/me': () => jsonResponse(me),
      'POST /api/v1/auth/password': (body) => {
        changes.push(body)
        return jsonResponse(null, 204)
      },
    })
    const user = userEvent.setup()
    renderRoute(<AccountPage />, { path: '/account', url: '/account' })

    await user.type(screen.getByLabelText('Current password'), 'old password 1')
    await user.type(screen.getByLabelText('New password'), 'a brand new secret')
    await user.type(screen.getByLabelText('Confirm new password'), 'a brand new secreT')
    await user.click(screen.getByRole('button', { name: 'Change password' }))

    expect(screen.getByRole('alert')).toHaveTextContent('do not match')
    expect(changes).toEqual([])

    await user.clear(screen.getByLabelText('Confirm new password'))
    await user.type(screen.getByLabelText('Confirm new password'), 'a brand new secret')
    await user.click(screen.getByRole('button', { name: 'Change password' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Password changed')
    expect(changes).toEqual([{ current_password: 'old password 1', new_password: 'a brand new secret' }])
  })

  it('rejects a too-short new password without contacting the server', async () => {
    const fetchMock = mockApi({ 'GET /api/v1/auth/me': () => jsonResponse(me) })
    const user = userEvent.setup()
    renderRoute(<AccountPage />, { path: '/account', url: '/account' })

    await user.type(screen.getByLabelText('Current password'), 'old password 1')
    await user.type(screen.getByLabelText('New password'), 'short')
    await user.type(screen.getByLabelText('Confirm new password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Change password' }))

    expect(screen.getByRole('alert')).toHaveTextContent('at least 12 characters')
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
})
