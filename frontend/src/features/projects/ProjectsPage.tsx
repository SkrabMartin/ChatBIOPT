import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, Field, TextArea, TextInput } from '../../components/ui'
import {
  DESCRIPTION_MAX_LENGTH,
  TITLE_MAX_LENGTH,
  useCreateProject,
  useProjects,
  type Project,
} from '../../lib/api/workspace'
import { formatDateTime, plural } from '../../lib/format'

function CreateProjectForm() {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const create = useCreateProject()

  function submit(event: FormEvent) {
    event.preventDefault()
    create.mutate(
      { title, description: description || null },
      {
        onSuccess: () => {
          setTitle('')
          setDescription('')
        },
      },
    )
  }

  return (
    <Card>
      <h2 className="mb-3 text-base font-semibold text-slate-900">New project</h2>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Title" htmlFor="project-title">
          <TextInput
            id="project-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={TITLE_MAX_LENGTH}
            placeholder="e.g. TP53 mutations in glioblastoma"
            required
          />
        </Field>
        <Field label="Description (optional)" htmlFor="project-description">
          <TextArea
            id="project-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={DESCRIPTION_MAX_LENGTH}
            rows={3}
            placeholder="Research question, organism, scope…"
          />
        </Field>
        {create.error ? <ErrorNotice error={create.error} title="Could not create project" /> : null}
        <Button type="submit" disabled={create.isPending || !title.trim()}>
          {create.isPending ? 'Creating…' : 'Create project'}
        </Button>
      </form>
    </Card>
  )
}

export function ProjectList({ projects }: { projects: Project[] }) {
  if (projects.length === 0) {
    return <p className="text-sm text-slate-500">No projects yet.</p>
  }
  return (
    <ul className="divide-y divide-slate-100">
      {projects.map((project) => (
        <li key={project.id}>
          <Link to={`/projects/${project.id}`} className="block rounded-md px-2 py-3 hover:bg-slate-50">
            <p className="font-medium text-slate-900">{project.title}</p>
            {project.description ? <p className="line-clamp-2 text-sm text-slate-600">{project.description}</p> : null}
            <p className="mt-1 text-xs text-slate-400">
              {plural(project.conversation_count, 'conversation')} · updated {formatDateTime(project.updated_at)}
            </p>
          </Link>
        </li>
      ))}
    </ul>
  )
}

export function ProjectsPage() {
  const projects = useProjects(200)

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Projects</h1>
        <p className="mt-1 text-sm text-slate-500">Each project is a research workspace with its own conversations.</p>
      </header>
      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <Card>
          <h2 className="mb-2 text-base font-semibold text-slate-900">
            All projects{projects.data ? ` (${projects.data.total})` : ''}
          </h2>
          {projects.isPending ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : projects.error ? (
            <ErrorNotice error={projects.error} title="Could not load projects" />
          ) : (
            <>
              <ProjectList projects={projects.data.items} />
              {projects.data.total > projects.data.items.length ? (
                <p className="mt-2 text-xs text-slate-400">
                  Showing the {projects.data.items.length} most recently updated of {projects.data.total}.
                </p>
              ) : null}
            </>
          )}
        </Card>
        <CreateProjectForm />
      </div>
    </div>
  )
}
