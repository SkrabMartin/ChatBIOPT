import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { ApiError, apiRequest, apiRequestNoContent } from './client'

export const PASSWORD_MIN_LENGTH = 12
export const PASSWORD_MAX_LENGTH = 256

export const userSchema = z.object({
  id: z.string(),
  email: z.string(),
  display_name: z.string(),
  is_admin: z.boolean(),
  created_at: z.string(),
})
export type User = z.infer<typeof userSchema>

export const meKey = ['auth', 'me'] as const

/** True for errors meaning "no valid session" (as opposed to a wrong password on login/password change). */
export function isUnauthenticated(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401 && error.code !== 'invalid_credentials'
}

/** Mark the session as gone: the auth gate then replaces the app with the login page. */
export function markLoggedOut(queryClient: QueryClient) {
  queryClient.setQueryData<User | null>(meKey, null)
}

/** Drop every cached query except the session itself, so no data from a previous session survives. */
function clearWorkspaceCache(queryClient: QueryClient) {
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== meKey[0] })
}

/** The signed-in user, or null when there is no session (401 is a state here, not an error). */
export function useMe() {
  return useQuery({
    queryKey: meKey,
    queryFn: async ({ signal }) => {
      try {
        return await apiRequest('GET', '/auth/me', userSchema, { signal })
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null
        throw error
      }
    },
  })
}

export function useLogin() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { email: string; password: string }) =>
      apiRequest('POST', '/auth/login', userSchema, { body: input }),
    onSuccess: (user) => {
      clearWorkspaceCache(queryClient)
      queryClient.setQueryData<User | null>(meKey, user)
    },
  })
}

export function useLogout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiRequestNoContent('POST', '/auth/logout'),
    onSuccess: () => {
      markLoggedOut(queryClient)
      clearWorkspaceCache(queryClient)
    },
  })
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (input: { current_password: string; new_password: string }) =>
      apiRequestNoContent('POST', '/auth/password', { body: input }),
  })
}
