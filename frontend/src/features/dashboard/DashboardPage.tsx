import { Link } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Card } from '../../components/ui'
import { useProjects } from '../../lib/api/workspace'
import { ProjectList } from '../projects/ProjectsPage'
import { SystemStatusCard } from './SystemStatusCard'

function RecentProjectsCard() {
  const projects = useProjects(5)

  return (
    <Card>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-base font-semibold text-slate-900">Recent projects</h2>
        <Link to="/projects" className="text-xs text-teal-700 hover:underline">
          All projects
        </Link>
      </div>
      {projects.isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : projects.error ? (
        <ErrorNotice error={projects.error} title="Could not load projects" />
      ) : (
        <ProjectList projects={projects.data.items} />
      )}
    </Card>
  )
}

export function DashboardPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Dashboard</h1>
        <p className="mt-1 text-sm text-slate-500">
          Evidence-based research workspace. Literature lookup and analyses arrive in upcoming milestones.
        </p>
      </header>
      <div className="grid gap-6 md:grid-cols-2">
        <RecentProjectsCard />
        <SystemStatusCard />
      </div>
    </div>
  )
}
