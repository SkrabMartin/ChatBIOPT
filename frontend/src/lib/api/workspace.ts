import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { apiRequest, apiRequestNoContent } from './client'

export const TITLE_MAX_LENGTH = 200
export const DESCRIPTION_MAX_LENGTH = 5000
export const MESSAGE_MAX_LENGTH = 20000

const pageSchema = <T extends z.ZodType>(item: T) =>
  z.object({ items: z.array(item), total: z.number(), limit: z.number(), offset: z.number() })

export const PROJECT_ROLES = ['viewer', 'editor', 'owner'] as const
export const projectRoleSchema = z.enum(PROJECT_ROLES)
export type ProjectRole = z.infer<typeof projectRoleSchema>

/** Viewers can only read; editors and owners can change project content. */
export const canEdit = (role: ProjectRole) => role !== 'viewer'

export const projectSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string().nullable(),
  conversation_count: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
  /** The caller's membership role in this project. */
  role: projectRoleSchema,
})
export type Project = z.infer<typeof projectSchema>

export const conversationSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  title: z.string(),
  message_count: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
})
export type Conversation = z.infer<typeof conversationSchema>

export const messageSchema = z.object({
  id: z.string(),
  conversation_id: z.string(),
  sequence: z.number(),
  role: z.enum(['user', 'assistant', 'system']),
  content: z.string(),
  /** The AI run that produced an assistant message; null for user messages. */
  run_id: z.string().nullable(),
  /** Who posted a user message; null for assistant and legacy messages. */
  author_id: z.string().nullable(),
  author_name: z.string().nullable(),
  created_at: z.string(),
})
export type Message = z.infer<typeof messageSchema>

const projectPageSchema = pageSchema(projectSchema)
const conversationPageSchema = pageSchema(conversationSchema)
const messagePageSchema = z.object({ items: z.array(messageSchema), has_more: z.boolean() })

export const keys = {
  projects: ['projects'] as const,
  project: (id: string) => ['projects', id] as const,
  conversations: (projectId: string) => ['projects', projectId, 'conversations'] as const,
  conversation: (id: string) => ['conversations', id] as const,
  messages: (conversationId: string) => ['conversations', conversationId, 'messages'] as const,
}

// --- projects ---

export function useProjects(limit = 50) {
  return useQuery({
    queryKey: [...keys.projects, { limit }],
    queryFn: ({ signal }) => apiRequest('GET', `/projects?limit=${limit}`, projectPageSchema, { signal }),
  })
}

export function useProject(id: string) {
  return useQuery({
    queryKey: keys.project(id),
    queryFn: ({ signal }) => apiRequest('GET', `/projects/${id}`, projectSchema, { signal }),
  })
}

export function useCreateProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { title: string; description?: string | null }) =>
      apiRequest('POST', '/projects', projectSchema, { body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.projects }),
  })
}

export function useUpdateProject(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { title?: string; description?: string | null }) =>
      apiRequest('PATCH', `/projects/${id}`, projectSchema, { body: input }),
    onSuccess: (project) => {
      queryClient.setQueryData(keys.project(id), project)
      return queryClient.invalidateQueries({ queryKey: keys.projects, exact: false })
    },
  })
}

export function useDeleteProject(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiRequestNoContent('DELETE', `/projects/${id}`),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: keys.project(id) })
      return queryClient.invalidateQueries({ queryKey: keys.projects })
    },
  })
}

// --- conversations ---

export function useConversations(projectId: string) {
  return useQuery({
    queryKey: keys.conversations(projectId),
    queryFn: ({ signal }) =>
      apiRequest('GET', `/projects/${projectId}/conversations?limit=200`, conversationPageSchema, { signal }),
  })
}

export function useConversation(id: string) {
  return useQuery({
    queryKey: keys.conversation(id),
    queryFn: ({ signal }) => apiRequest('GET', `/conversations/${id}`, conversationSchema, { signal }),
  })
}

export function useCreateConversation(projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { title: string }) =>
      apiRequest('POST', `/projects/${projectId}/conversations`, conversationSchema, { body: input }),
    // Project keys prefix the conversation list, so this refreshes both the list and conversation_count.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.projects }),
  })
}

export function useUpdateConversation(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { title: string }) =>
      apiRequest('PATCH', `/conversations/${id}`, conversationSchema, { body: input }),
    onSuccess: (conversation) => {
      queryClient.setQueryData(keys.conversation(id), conversation)
      return queryClient.invalidateQueries({ queryKey: keys.conversations(conversation.project_id) })
    },
  })
}

export function useDeleteConversation(id: string, projectId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => apiRequestNoContent('DELETE', `/conversations/${id}`),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: keys.conversation(id) })
      return queryClient.invalidateQueries({ queryKey: keys.project(projectId) })
    },
  })
}

// --- messages ---

/** Full thread in sequence order, following the `after_sequence` cursor until the server reports no more pages. */
export async function fetchAllMessages(conversationId: string, signal?: AbortSignal): Promise<Message[]> {
  const messages: Message[] = []
  let after = 0
  for (;;) {
    const page = await apiRequest(
      'GET',
      `/conversations/${conversationId}/messages?after_sequence=${after}&limit=500`,
      messagePageSchema,
      { signal },
    )
    messages.push(...page.items)
    if (!page.has_more || page.items.length === 0) return messages
    after = page.items[page.items.length - 1].sequence
  }
}

export function useMessages(conversationId: string) {
  return useQuery({
    queryKey: keys.messages(conversationId),
    queryFn: ({ signal }) => fetchAllMessages(conversationId, signal),
  })
}

export function usePostMessage(conversationId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (content: string) =>
      apiRequest('POST', `/conversations/${conversationId}/messages`, messageSchema, {
        body: { role: 'user', content },
      }),
    onSuccess: (message) => {
      queryClient.setQueryData<Message[]>(keys.messages(conversationId), (old = []) =>
        old.some((m) => m.id === message.id) ? old : [...old, message].sort((a, b) => a.sequence - b.sequence),
      )
      return queryClient.invalidateQueries({ queryKey: keys.conversation(conversationId), exact: true })
    },
  })
}
