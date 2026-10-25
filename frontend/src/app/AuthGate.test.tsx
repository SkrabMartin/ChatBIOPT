import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { useProjects } from '../lib/api/workspace'
import { jsonResponse, mockApi } from '../test/utils'
import { AuthGate } from './AuthGate'
import { createQueryClient } from './queryClient'

const now = '2026-10-23T10:00:00Z'
const ada = { id: 'u-1', email: 'ada@example.org', display_name: 'Ada', is_admin: false, created_at: now }
const projectPage = {
  items: [
    { id: 'p-1', title: 'TP53', description: null, conversation_count: 0, created_at: now, updated_at: now, role: 'owner' },
  ],
  total: 1,
  limit: 50,
  offset: 0,
}
const unauthenticated = () =>
  jsonResponse({ error: { code: 'unauthenticated', message: 'Not authenticated', trace_id: 't' } }, 401)

function Projects() {
  const projects = useProjects()
  return <p>{projects.data ? `projects: ${projects.data.items.map((p) => p.title).join(', ')}` : 'loading projects'}</p>
}

function renderGate() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <AuthGate>
        <Projects />
      </AuthGate>
    </QueryClientProvider>,
  )
}

async function signIn(password: string) {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Email'), 'ada@example.org')
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('AuthGate', () => {
  it('shows the login page without a session and the app after signing in', async () => {
    const logins: unknown[] = []
    mockApi({
      'GET /api/v1/auth/me': unauthenticated,
      'POST /api/v1/auth/login': (body) => {
        logins.push(body)
        return jsonResponse(ada)
      },
      'GET /api/v1/projects': () => jsonResponse(projectPage),
    })
    renderGate()

    await signIn('correct horse battery')

    expect(await screen.findByText('projects: TP53')).toBeInTheDocument()
    expect(logins).toEqual([{ email: 'ada@example.org', password: 'correct horse battery' }])
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  })

  it('explains a rejected login and stays on the login page', async () => {
    mockApi({
      'GET /api/v1/auth/me': unauthenticated,
      'POST /api/v1/auth/login': () =>
        jsonResponse({ error: { code: 'invalid_credentials', message: 'Invalid email or password', trace_id: 't' } }, 401),
    })
    renderGate()

    await signIn('wrong')

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password')
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
  })

  it('returns to the login page when the session expires mid-use', async () => {
    mockApi({
      'GET /api/v1/auth/me': () => jsonResponse(ada),
      'GET /api/v1/projects': unauthenticated,
    })
    renderGate()

    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })
})
