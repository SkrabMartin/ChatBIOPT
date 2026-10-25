import type { ProjectRole } from '../../lib/api/workspace'

const tone: Record<ProjectRole, string> = {
  owner: 'bg-teal-50 text-teal-800',
  editor: 'bg-sky-50 text-sky-800',
  viewer: 'bg-slate-100 text-slate-600',
}

/** The caller's role in a project. */
export function RoleBadge({ role }: { role: ProjectRole }) {
  return (
    <span title="Your role in this project" className={`rounded px-1.5 py-0.5 text-xs font-medium ${tone[role]}`}>
      {role}
    </span>
  )
}
