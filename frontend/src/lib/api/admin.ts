import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { apiRequest } from './client'

export const adminUserSchema = z.object({
  id: z.string(),
  email: z.string(),
  display_name: z.string(),
  is_admin: z.boolean(),
  disabled: z.boolean(),
  created_at: z.string(),
  last_login_at: z.string().nullable(),
})
export type AdminUser = z.infer<typeof adminUserSchema>

const usersKey = ['admin', 'users'] as const

export function useAdminUsers() {
  return useQuery({
    queryKey: usersKey,
    queryFn: ({ signal }) => apiRequest('GET', '/admin/users', z.array(adminUserSchema), { signal }),
  })
}

export function useCreateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { email: string; display_name: string; password: string; is_admin: boolean }) =>
      apiRequest('POST', '/admin/users', adminUserSchema, { body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: usersKey }),
  })
}

export interface UserUpdate {
  display_name?: string
  password?: string
  is_admin?: boolean
  disabled?: boolean
}

export function useUpdateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...input }: UserUpdate & { id: string }) =>
      apiRequest('PATCH', `/admin/users/${id}`, adminUserSchema, { body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: usersKey }),
  })
}
