import { useState } from 'react'
import { Link } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button } from '../../components/ui'
import { useAddSource } from '../../lib/api/evidence'
import { useProjects } from '../../lib/api/workspace'

/** Queue ingestion of a PubMed record into a project's evidence library. */
export function AddToProject({ pmid }: { pmid: string }) {
  const projects = useProjects(200)
  const [projectId, setProjectId] = useState('')
  const selected = projectId || projects.data?.items[0]?.id || ''
  const add = useAddSource(selected)

  if (!projects.data || projects.data.items.length === 0) {
    return projects.data ? (
      <p className="text-xs text-slate-500">
        <Link to="/projects" className="text-teal-700 hover:underline">
          Create a project
        </Link>{' '}
        to collect evidence.
      </p>
    ) : null
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <label className="sr-only" htmlFor={`add-${pmid}`}>
          Project
        </label>
        <select
          id={`add-${pmid}`}
          value={selected}
          onChange={(e) => {
            setProjectId(e.target.value)
            add.reset()
          }}
          className="max-w-56 rounded border border-slate-200 bg-white px-1.5 py-1 text-xs text-slate-700"
        >
          {projects.data.items.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title}
            </option>
          ))}
        </select>
        <Button variant="secondary" onClick={() => add.mutate(pmid)} disabled={add.isPending || !selected}>
          {add.isPending ? 'Adding…' : 'Add to project'}
        </Button>
      </div>
      {add.data ? (
        <p className="text-xs text-emerald-700">
          {add.data.created ? 'Queued for ingestion' : 'Already being ingested'} ·{' '}
          <Link to={`/projects/${selected}`} className="hover:underline">
            open project
          </Link>
        </p>
      ) : null}
      {add.error ? <ErrorNotice error={add.error} title="Could not add" /> : null}
    </div>
  )
}
