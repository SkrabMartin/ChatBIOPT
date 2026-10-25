import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { apiRequest, apiRequestNoContent } from './client'
import { keys, projectRoleSchema, type ProjectRole } from './workspace'

export const memberSchema = z.object({
  user_id: z.string(),
  email: z.string(),
  display_name: z.string(),
  role: projectRoleSchema,
  added_at: z.string(),
})
export type Member = z.infer<typeof memberSchema>

const membersKey = (projectId: string) => [...keys.project(projectId), 'members'] as const

export function useMembers(projectId: string) {
  return useQuery({
    queryKey: membersKey(projectId),
    queryFn: ({ signal }) => apiRequest('GET', `/projects/${projectId}/members`, z.array(memberSchema), { signal }),
  })
}

// Member changes can alter the caller's own role, so refresh the project (which prefixes the member list) too.

export function useAddMember(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { email: string; role: ProjectRole }) =>
      apiRequest('POST', `/projects/${projectId}/members`, memberSchema, { body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: membersKey(projectId) }),
  })
}

export function useUpdateMember(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: ProjectRole }) =>
      apiRequest('PATCH', `/projects/${projectId}/members/${userId}`, memberSchema, { body: { role } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.project(projectId) }),
  })
}

export function useRemoveMember(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (userId: string) => apiRequestNoContent('DELETE', `/projects/${projectId}/members/${userId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.project(projectId) }),
  })
}

/** Remove the caller's own membership; the project then disappears for them. */
export function useLeaveProject(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (userId: string) => apiRequestNoContent('DELETE', `/projects/${projectId}/members/${userId}`),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: keys.project(projectId) })
      return queryClient.invalidateQueries({ queryKey: keys.projects })
    },
  })
}
