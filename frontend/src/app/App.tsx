import { QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { AccountPage } from '../features/account/AccountPage'
import { AdminUsersPage } from '../features/admin/AdminUsersPage'
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
import { AuthGate } from './AuthGate'
import { Layout } from './Layout'
import { NotFoundPage } from './NotFoundPage'
import { createQueryClient } from './queryClient'

const queryClient = createQueryClient()

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
      { path: 'account', element: <AccountPage /> },
      { path: 'admin/users', element: <AdminUsersPage /> },
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
      <AuthGate>
        <RouterProvider router={router} />
      </AuthGate>
    </QueryClientProvider>
  )
}
