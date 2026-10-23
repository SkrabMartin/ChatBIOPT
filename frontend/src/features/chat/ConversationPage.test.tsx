import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { z } from 'zod'
import { fetchAllMessages, type Message } from '../../lib/api/workspace'
import { jsonResponse, mockApi, renderRoute, type Routes } from '../../test/utils'
import { ConversationPage } from './ConversationPage'

const P = 'p-1'
const C = 'c-1'
const now = '2026-10-23T10:00:00Z'

function message(sequence: number, content: string): Message {
  return { id: `m-${sequence}`, conversation_id: C, sequence, role: 'user', content, created_at: now }
}

function baseRoutes(messages: Message[]): Routes {
  return {
    [`GET /api/v1/projects/${P}`]: () =>
      jsonResponse({ id: P, title: 'TP53', description: null, conversation_count: 1, created_at: now, updated_at: now }),
    [`GET /api/v1/conversations/${C}`]: () =>
      jsonResponse({
        id: C,
        project_id: P,
        title: 'Scoping',
        message_count: messages.length,
        created_at: now,
        updated_at: now,
      }),
    [`GET /api/v1/conversations/${C}/messages`]: () => jsonResponse({ items: messages, has_more: false }),
  }
}

function renderPage() {
  return renderRoute(<ConversationPage />, {
    path: '/projects/:projectId/conversations/:conversationId',
    url: `/projects/${P}/conversations/${C}`,
  })
}

describe('ConversationPage', () => {
  it('renders the thread in sequence order as plain text', async () => {
    mockApi(baseRoutes([message(1, 'first'), message(2, '<b>not bold</b>')]))

    renderPage()

    const list = await screen.findByRole('list', { name: 'Messages' })
    const items = within(list).getAllByRole('listitem')
    expect(items.map((li) => li.textContent)).toEqual([expect.stringContaining('first'), expect.stringContaining('<b>not bold</b>')])
    expect(list.querySelector('b')).toBeNull()
  })

  it('appends a sent message and clears the composer', async () => {
    const fetchMock = mockApi({
      ...baseRoutes([message(1, 'first')]),
      [`POST /api/v1/conversations/${C}/messages`]: (body) =>
        jsonResponse(message(2, z.object({ content: z.string() }).parse(body).content), 201),
    })
    const user = userEvent.setup()
    renderPage()

    const composer = await screen.findByLabelText('Message')
    await user.type(composer, 'Which TP53 hotspots?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('Which TP53 hotspots?')).toBeInTheDocument()
    expect(composer).toHaveValue('')
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ role: 'user', content: 'Which TP53 hotspots?' })
  })

  it('keeps the draft and shows the server reason when sending fails', async () => {
    mockApi({
      ...baseRoutes([]),
      [`POST /api/v1/conversations/${C}/messages`]: () =>
        jsonResponse(
          {
            error: {
              code: 'validation_error',
              message: 'Request validation failed',
              trace_id: 'trace-test',
              details: [{ loc: ['body', 'content'], msg: 'Value error, content cannot be blank', type: 'value_error' }],
            },
          },
          422,
        ),
    })
    const user = userEvent.setup()
    renderPage()

    const composer = await screen.findByLabelText('Message')
    await user.type(composer, 'draft text')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('content: Value error, content cannot be blank')
    expect(composer).toHaveValue('draft text')
  })

  it('shows not-found for a deleted conversation', async () => {
    mockApi({
      ...baseRoutes([]),
      [`GET /api/v1/conversations/${C}`]: () =>
        jsonResponse({ error: { code: 'not_found', message: 'Conversation not found', trace_id: 'x' } }, 404),
    })

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Conversation not found')
  })
})

describe('fetchAllMessages', () => {
  it('follows the after_sequence cursor until has_more is false', async () => {
    const fetchMock = mockApi({
      [`GET /api/v1/conversations/${C}/messages`]: (_body, url) => {
        const after = Number(url.searchParams.get('after_sequence'))
        return after === 0
          ? jsonResponse({ items: [message(1, 'a'), message(2, 'b')], has_more: true })
          : jsonResponse({ items: [message(3, 'c')], has_more: false })
      },
    })

    const all = await fetchAllMessages(C)

    expect(all.map((m) => m.sequence)).toEqual([1, 2, 3])
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(String(fetchMock.mock.calls[1][0])).toContain('after_sequence=2')
  })
})
