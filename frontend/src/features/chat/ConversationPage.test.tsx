import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { followRunEvents, type Run, type RunEvent } from '../../lib/api/runs'
import { fetchAllMessages, type Message } from '../../lib/api/workspace'
import { jsonResponse, mockApi, renderRoute, type Routes } from '../../test/utils'
import { ConversationPage } from './ConversationPage'

const P = 'p-1'
const C = 'c-1'
const now = '2026-10-23T10:00:00Z'

function message(sequence: number, content: string, role: Message['role'] = 'user', runId: string | null = null): Message {
  return { id: `m-${sequence}`, conversation_id: C, sequence, role, content, run_id: runId, created_at: now }
}

function run(overrides: Partial<Run> = {}): Run {
  return {
    id: 'r-1',
    conversation_id: C,
    kind: 'chat',
    status: 'queued',
    model_profile: 'local-small',
    provider: 'ollama',
    model_id: 'qwen2.5:1.5b',
    model_revision: null,
    prompt_version: 'research-assistant-v1',
    config_hash: 'h',
    context_until_sequence: 1,
    context_message_count: null,
    input_tokens: null,
    output_tokens: null,
    estimated_cost_usd: null,
    time_to_first_token_ms: null,
    latency_ms: null,
    finish_reason: null,
    error_code: null,
    error_message: null,
    grounding: 'none',
    retrieval_version: null,
    embedding_space: null,
    answer_class: null,
    citation_stats: null,
    created_at: now,
    started_at: null,
    finished_at: null,
    ...overrides,
  }
}

function sse(...events: [number, string, unknown][]): Response {
  const text = events.map(([id, type, data]) => `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`).join('')
  return new Response(text, { headers: { 'Content-Type': 'text/event-stream' } })
}

const models = {
  default_profile: 'local-small',
  allow_external_models: false,
  items: [],
}

function baseRoutes(state: { messages: Message[]; runs: Run[] }): Routes {
  return {
    [`GET /api/v1/projects/${P}`]: () =>
      jsonResponse({ id: P, title: 'TP53', description: null, conversation_count: 1, created_at: now, updated_at: now }),
    [`GET /api/v1/conversations/${C}`]: () =>
      jsonResponse({
        id: C,
        project_id: P,
        title: 'Scoping',
        message_count: state.messages.length,
        created_at: now,
        updated_at: now,
      }),
    [`GET /api/v1/conversations/${C}/messages`]: () => jsonResponse({ items: state.messages, has_more: false }),
    [`GET /api/v1/conversations/${C}/runs`]: () => jsonResponse(state.runs),
    'GET /api/v1/models': () => jsonResponse(models),
  }
}

function renderPage() {
  return renderRoute(<ConversationPage />, {
    path: '/projects/:projectId/conversations/:conversationId',
    url: `/projects/${P}/conversations/${C}`,
  })
}

describe('ConversationPage', () => {
  it('renders user messages verbatim, never as HTML', async () => {
    mockApi(baseRoutes({ messages: [message(1, 'first'), message(2, '<b>not bold</b>')], runs: [] }))

    renderPage()

    const list = await screen.findByRole('list', { name: 'Messages' })
    const items = within(list).getAllByRole('listitem')
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining('first'),
      expect.stringContaining('<b>not bold</b>'),
    ])
    expect(list.querySelector('b')).toBeNull()
  })

  it('sends a question, streams the answer and then shows the stored message', async () => {
    const state = { messages: [] as Message[], runs: [] as Run[] }
    const fetchMock = mockApi({
      ...baseRoutes(state),
      [`POST /api/v1/conversations/${C}/messages`]: (body) => {
        const sent = message(1, z.object({ content: z.string() }).parse(body).content)
        state.messages = [sent]
        return jsonResponse(sent, 201)
      },
      [`POST /api/v1/chat/${C}/runs`]: () => {
        state.runs = [run()]
        return jsonResponse(run(), 202)
      },
      'GET /api/v1/runs/r-1/events': () => {
        state.messages = [...state.messages, message(2, 'TP53 encodes **p53**.', 'assistant', 'r-1')]
        state.runs = [run({ status: 'succeeded' })]
        return sse(
          [1, 'status', { status: 'running' }],
          [2, 'delta', { text: 'TP53 encodes ' }],
          [3, 'completed', { run: run({ status: 'succeeded' }), message: state.messages[1] }],
        )
      },
    })
    const user = userEvent.setup()
    renderPage()

    const composer = await screen.findByLabelText('Message')
    await user.type(composer, 'What does TP53 encode?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    const answer = await screen.findByText('p53')
    expect(answer.tagName).toBe('STRONG')
    expect(screen.getByText(/not grounded in retrieved sources/)).toBeInTheDocument()
    expect(composer).toHaveValue('')
    const runPost = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith('/runs') && init?.method === 'POST')
    expect(JSON.parse(String(runPost?.[1]?.body))).toEqual({ grounding: 'auto' })
  })

  it('shows a persisted failure with its reason and lets the user retry', async () => {
    const failed = run({ status: 'failed', error_code: 'model_unavailable', error_message: 'Model qwen2.5:1.5b is not pulled' })
    const state = { messages: [message(1, 'Q')], runs: [failed] }
    const fetchMock = mockApi({
      ...baseRoutes(state),
      [`POST /api/v1/chat/${C}/runs`]: () => jsonResponse(run({ id: 'r-2' }), 202),
      'GET /api/v1/runs/r-2/events': () => sse([1, 'cancelled', { run: run({ id: 'r-2', status: 'cancelled' }) }]),
    })
    const user = userEvent.setup()
    renderPage()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Model unavailable')
    expect(alert).toHaveTextContent('Model qwen2.5:1.5b is not pulled')
    expect(screen.queryByText(/not grounded/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry answer' }))

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/runs/r-2/events'))).toBe(true),
    )
  })

  it('renders model Markdown without raw HTML, images or clickable links', async () => {
    const content = 'See [this paper](https://evil.example/x) <script>alert(1)</script> ![img](https://x/y.png) <i>raw</i>'
    mockApi(baseRoutes({ messages: [message(1, 'Q'), message(2, content, 'assistant', 'r-1')], runs: [] }))

    renderPage()

    const answer = (await screen.findByText(/this paper/)).closest('li')!
    expect(answer.querySelector('a, script, img, i')).toBeNull()
    expect(within(answer).getByText('this paper')).toHaveAttribute(
      'title',
      'Unverified link from model output: https://evil.example/x',
    )
  })

  it('links validated citations to stored passages and labels the answer class', async () => {
    const grounded = run({ status: 'succeeded', grounding: 'project', answer_class: 'partially_supported' })
    const evidence = [
      {
        label: 'E1',
        rank: 1,
        cited: true,
        keyword_rank: 1,
        semantic_rank: 2,
        similarity: 0.71,
        fused_score: 0.03,
        chunk_id: 'ch-1',
        text: 'Two mutations, R110C and R175H, were identified, each in distinct clones.',
        section: 'results',
        section_title: 'Results',
        start_offset: 0,
        end_offset: 70,
        document_id: 'doc-1',
        document_kind: 'fulltext',
        document_version: 1,
        source_id: 's-1',
        external_id: '29416795',
        source_title: 'Treatment-associated TP53 mutations',
        source_url: 'https://pubmed.ncbi.nlm.nih.gov/29416795/',
      },
    ]
    mockApi({
      ...baseRoutes({
        messages: [message(1, 'Q'), message(2, 'R175H and R110C were in distinct clones [E1]. Unrelated [E7].', 'assistant', 'r-1')],
        runs: [grounded],
      }),
      'GET /api/v1/runs/r-1': () => jsonResponse(grounded),
      'GET /api/v1/runs/r-1/evidence': () => jsonResponse(evidence),
    })
    const user = userEvent.setup()

    renderPage()

    const chip = await screen.findByRole('link', { name: 'E1' })
    expect(chip).toHaveAttribute('href', '/documents/doc-1#chunk-ch-1')
    expect(screen.getByText(/Unrelated \[E7\]/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'E7' })).not.toBeInTheDocument()
    expect(screen.getByText('Partially supported')).toBeInTheDocument()
    expect(screen.getByText(/grounded in project passages/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Passages \(1 cited of 1 retrieved\)/ }))
    expect(screen.getByText(evidence[0].text)).toBeInTheDocument()
  })

  it('hides the status line while streaming a grounded answer', async () => {
    const state = { messages: [message(1, 'Q')], runs: [run({ status: 'running', grounding: 'project' })] }
    mockApi({
      ...baseRoutes(state),
      'GET /api/v1/runs/r-1/events': () =>
        new Response(
          new ReadableStream({
            start: (c) =>
              c.enqueue(
                new TextEncoder().encode(
                  'id: 1\nevent: status\ndata: {"status":"retrieving"}\n\n' +
                    'id: 2\nevent: evidence\ndata: {"items":[{"label":"E1"},{"label":"E2"}],"warnings":[]}\n\n' +
                    'id: 3\nevent: status\ndata: {"status":"running"}\n\n' +
                    'id: 4\nevent: delta\ndata: {"text":"STATUS: supported\\nThe answer"}\n\n',
                ),
              ),
          }),
        ),
    })

    renderPage()

    expect(await screen.findByText(/from 2 passage\(s\)/)).toBeInTheDocument()
    expect(await screen.findByText('The answer')).toBeInTheDocument()
    expect(screen.queryByText(/STATUS/)).not.toBeInTheDocument()
  })

  it('disables sending while an answer is being generated and can stop it', async () => {
    const state = { messages: [message(1, 'Q')], runs: [run({ status: 'running' })] }
    const fetchMock = mockApi({
      ...baseRoutes(state),
      // A stream that never ends: the UI must stay in the generating state.
      'GET /api/v1/runs/r-1/events': () =>
        new Response(new ReadableStream({ start: (c) => c.enqueue(new TextEncoder().encode(': keep-alive\n\n')) })),
      'POST /api/v1/runs/r-1/cancel': () => jsonResponse(run({ status: 'running' }), 202),
    })
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText(/Generating with local-small/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Message'), 'another question')
    expect(screen.getByRole('button', { name: 'Generating…' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith('/cancel') && init?.method === 'POST')).toBe(
        true,
      ),
    )
  })

  it('shows not-found for a deleted conversation', async () => {
    mockApi({
      ...baseRoutes({ messages: [], runs: [] }),
      [`GET /api/v1/conversations/${C}`]: () =>
        jsonResponse({ error: { code: 'not_found', message: 'Conversation not found', trace_id: 'x' } }, 404),
    })

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Conversation not found')
  })
})

describe('followRunEvents', () => {
  it('reconnects with Last-Event-ID when the stream drops before a terminal event', async () => {
    const fetchMock = mockApi({
      'GET /api/v1/runs/r-1/events': () => {
        const resumed = fetchMock.mock.calls.length > 1
        return resumed
          ? sse([3, 'completed', { run: null, message: null }])
          : sse([1, 'status', { status: 'running' }], [2, 'delta', { text: 'Hi' }])
      },
    })
    const seen: RunEvent['type'][] = []

    await followRunEvents('r-1', (event) => seen.push(event.type), new AbortController().signal, vi.fn(async () => {}))

    expect(seen).toEqual(['status', 'delta', 'completed'])
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('Last-Event-ID')).toBe('2')
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
    expect(String(fetchMock.mock.calls[1][0])).toContain('after_sequence=2')
  })
})
