import { SystemStatusCard } from './SystemStatusCard'

export function DashboardPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Dashboard</h1>
        <p className="mt-1 text-sm text-slate-500">
          Evidence-based research workspace. Projects, literature lookup and analyses arrive in upcoming milestones.
        </p>
      </header>
      <div className="grid gap-6 md:grid-cols-2">
        <SystemStatusCard />
      </div>
    </div>
  )
}
