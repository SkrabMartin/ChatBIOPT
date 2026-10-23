import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { vi } from 'vitest'

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'trace-test' },
  })
}

type Handler = (body: unknown, url: URL) => Response
export type Routes = Record<string, Handler>

/**
 * Stub `fetch` with handlers keyed by `"METHOD /api/v1/path"` (query string excluded).
 * Unmatched requests fail the test loudly instead of hanging. Returns the mock for call assertions.
 */
export function mockApi(routes: Routes) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost')
    const key = `${init?.method ?? 'GET'} ${url.pathname}`
    const handler = routes[key]
    if (!handler) throw new Error(`Unexpected request: ${key}`)
    return handler(init?.body ? JSON.parse(String(init.body)) : undefined, url)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

export function renderRoute(element: ReactElement, { path, url }: { path: string; url: string }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const router = createMemoryRouter(
    [
      { path, element },
      { path: '*', element: <p>navigated away</p> },
    ],
    { initialEntries: [url] },
  )
  return {
    router,
    ...render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  }
}
