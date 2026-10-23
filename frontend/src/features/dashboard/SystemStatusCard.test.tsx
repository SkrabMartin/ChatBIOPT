import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SystemStatusCard } from './SystemStatusCard'

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SystemStatusCard />
    </QueryClientProvider>,
  )
}

function jsonResponse(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'trace-123' },
  })
}

const healthy = {
  status: 'ok',
  version: '0.1.0',
  environment: 'test',
  database: { status: 'ok', migration_revision: '0001', latency_ms: 1.5 },
}

describe('SystemStatusCard', () => {
  it('shows operational API, connected database and schema revision', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(healthy, 200))
    vi.stubGlobal('fetch', fetchMock)

    renderCard()

    expect(await screen.findByText('Operational')).toBeInTheDocument()
    expect(screen.getByText('Connected')).toBeInTheDocument()
    expect(screen.getByText('0001')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/health', expect.anything())
  })

  it('renders a 503 degraded report instead of treating it as an outage', async () => {
    const degraded = {
      ...healthy,
      status: 'degraded',
      database: { status: 'unavailable', migration_revision: null, latency_ms: null },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(degraded, 503)))

    renderCard()

    expect(await screen.findByText('Degraded')).toBeInTheDocument()
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reports an unreachable API', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    renderCard()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('API unavailable')
    expect(alert).toHaveTextContent('API is unreachable')
  })

  it('rejects a response that violates the health contract and surfaces the trace id', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ status: 'ok' }, 200)))

    renderCard()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('did not match the expected contract')
    expect(alert).toHaveTextContent('trace-123')
  })

  it('treats an unexpected server error as an outage', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ error: {} }, 500)))

    renderCard()

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 500')
  })
})
