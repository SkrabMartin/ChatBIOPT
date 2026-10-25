import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'
import { isUnauthenticated, markLoggedOut } from '../lib/api/auth'
import { ApiError } from '../lib/api/client'

/**
 * Shared client for the app. Any request answered with 401 (session missing or expired) flips the cached session to
 * logged-out, which makes the auth gate show the login page instead of a broken workspace.
 */
export function createQueryClient(): QueryClient {
  const onError = (error: Error) => {
    if (isUnauthenticated(error)) markLoggedOut(queryClient)
  }
  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({ onError }),
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        // Retry transient failures once; a 4xx (e.g. 404 for a deleted project) will not change on retry.
        retry: (failureCount, error) =>
          failureCount < 1 && !(error instanceof ApiError && error.status !== null && error.status < 500),
      },
    },
  })
  return queryClient
}
