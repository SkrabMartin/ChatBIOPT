import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { ConversationPage } from '../features/chat/ConversationPage'
import { LiteraturePage } from '../features/literature/LiteraturePage'
import { LiteratureRecordPage } from '../features/literature/LiteratureRecordPage'
import { DashboardPage } from '../features/dashboard/DashboardPage'
import { EntityPage } from '../features/entities/EntityPage'
import { DocumentPage } from '../features/evidence/DocumentPage'
import { EvaluationsPage } from '../features/evaluations/EvaluationsPage'
import { EvidenceBoardPage } from '../features/evidence/EvidenceBoardPage'
import { ProjectPage } from '../features/projects/ProjectPage'
import { ProjectsPage } from '../features/projects/ProjectsPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { ApiError } from '../lib/api/client'
import { Layout } from './Layout'
import { NotFoundPage } from './NotFoundPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      // Retry transient failures once; a 4xx (e.g. 404 for a deleted project) will not change on retry.
      retry: (failureCount, error) =>
        failureCount < 1 && !(error instanceof ApiError && error.status !== null && error.status < 500),
    },
  },
})

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: 'projects', element: <ProjectsPage /> },
      { path: 'projects/:projectId', element: <ProjectPage /> },
      { path: 'projects/:projectId/conversations/:conversationId', element: <ConversationPage /> },
      { path: 'literature', element: <LiteraturePage /> },
      { path: 'literature/pubmed/:pmid', element: <LiteratureRecordPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'evaluations', element: <EvaluationsPage /> },
      { path: 'documents/:documentId', element: <DocumentPage /> },
      { path: 'projects/:projectId/evidence-board', element: <EvidenceBoardPage /> },
      { path: 'entities/:entityId', element: <EntityPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
