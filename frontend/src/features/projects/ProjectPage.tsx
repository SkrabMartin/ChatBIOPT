import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, Field, TextArea, TextInput } from '../../components/ui'
import { ApiError } from '../../lib/api/client'
import {
  DESCRIPTION_MAX_LENGTH,
  TITLE_MAX_LENGTH,
  useConversations,
  useCreateConversation,
  useDeleteProject,
  useProject,
  useUpdateProject,
  type Project,
} from '../../lib/api/workspace'
import { formatDateTime, plural } from '../../lib/format'
import { ProjectEntities } from '../entities/ProjectEntities'
import { EvidenceLibrary } from '../evidence/EvidenceLibrary'
import { EvidenceSearch } from '../evidence/EvidenceSearch'

function EditProjectForm({ project, onDone }: { project: Project; onDone: () => void }) {
  const [title, setTitle] = useState(project.title)
  const [description, setDescription] = useState(project.description ?? '')
  const update = useUpdateProject(project.id)

  function submit(event: FormEvent) {
    event.preventDefault()
    update.mutate({ title, description: description || null }, { onSuccess: onDone })
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <Field label="Title" htmlFor="edit-title">
        <TextInput
          id="edit-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={TITLE_MAX_LENGTH}
          required
        />
      </Field>
      <Field label="Description" htmlFor="edit-description">
        <TextArea
          id="edit-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          maxLength={DESCRIPTION_MAX_LENGTH}
          rows={4}
        />
      </Field>
      {update.error ? <ErrorNotice error={update.error} title="Could not save changes" /> : null}
      <div className="flex gap-2">
        <Button type="submit" disabled={update.isPending || !title.trim()}>
          {update.isPending ? 'Saving…' : 'Save'}
        </Button>
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

function Conversations({ projectId }: { projectId: string }) {
  const conversations = useConversations(projectId)
  const create = useCreateConversation(projectId)
  const [title, setTitle] = useState('')
  const navigate = useNavigate()

  function submit(event: FormEvent) {
    event.preventDefault()
    create.mutate(
      { title },
      { onSuccess: (conversation) => navigate(`/projects/${projectId}/conversations/${conversation.id}`) },
    )
  }

  return (
    <Card>
      <h2 className="mb-3 text-base font-semibold text-slate-900">Conversations</h2>
      <form onSubmit={submit} className="mb-4 flex gap-2">
        <label htmlFor="conversation-title" className="sr-only">
          New conversation title
        </label>
        <TextInput
          id="conversation-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={TITLE_MAX_LENGTH}
          placeholder="New conversation title"
          required
        />
        <Button type="submit" disabled={create.isPending || !title.trim()} className="shrink-0">
          Start
        </Button>
      </form>
      {create.error ? <ErrorNotice error={create.error} title="Could not start conversation" /> : null}
      {conversations.isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : conversations.error ? (
        <ErrorNotice error={conversations.error} title="Could not load conversations" />
      ) : conversations.data.items.length === 0 ? (
        <p className="text-sm text-slate-500">No conversations yet.</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {conversations.data.items.map((c) => (
            <li key={c.id}>
              <Link
                to={`/projects/${projectId}/conversations/${c.id}`}
                className="flex items-baseline justify-between gap-4 rounded-md px-2 py-2.5 hover:bg-slate-50"
              >
                <span className="font-medium text-slate-900">{c.title}</span>
                <span className="shrink-0 text-xs text-slate-400">
                  {plural(c.message_count, 'message')} · {formatDateTime(c.updated_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export function ProjectPage() {
  const { projectId = '' } = useParams()
  const project = useProject(projectId)
  const remove = useDeleteProject(projectId)
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)

  if (project.isPending) return <p className="text-sm text-slate-500">Loading…</p>
  if (project.error) {
    const missing = project.error instanceof ApiError && (project.error.status === 404 || project.error.status === 422)
    return (
      <div className="space-y-3">
        <ErrorNotice error={project.error} title={missing ? 'Project not found' : 'Could not load project'} />
        <Link to="/projects" className="text-sm text-teal-700 hover:underline">
          Back to projects
        </Link>
      </div>
    )
  }

  function handleDelete() {
    if (!window.confirm(`Delete "${project.data!.title}" and all of its conversations? This cannot be undone.`)) return
    remove.mutate(undefined, { onSuccess: () => navigate('/projects') })
  }

  return (
    <div className="space-y-6">
      <nav className="text-sm text-slate-500">
        <Link to="/projects" className="hover:underline">
          Projects
        </Link>{' '}
        / <span className="text-slate-700">{project.data.title}</span>
      </nav>
      <Card>
        {editing ? (
          <EditProjectForm project={project.data} onDone={() => setEditing(false)} />
        ) : (
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-semibold text-slate-900">{project.data.title}</h1>
              {project.data.description ? (
                <p className="mt-2 whitespace-pre-wrap text-sm text-slate-600">{project.data.description}</p>
              ) : null}
              <p className="mt-2 text-xs text-slate-400">
                Created {formatDateTime(project.data.created_at)} · updated {formatDateTime(project.data.updated_at)}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button variant="secondary" onClick={() => setEditing(true)}>
                Edit
              </Button>
              <Button variant="danger" onClick={handleDelete} disabled={remove.isPending}>
                Delete
              </Button>
            </div>
          </div>
        )}
        {remove.error ? <ErrorNotice error={remove.error} title="Could not delete project" /> : null}
      </Card>
      <EvidenceLibrary projectId={projectId} />
      <div className="flex justify-end">
        <Link to={`/projects/${projectId}/evidence-board`} className="text-sm text-teal-700 hover:underline">
          Open evidence board →
        </Link>
      </div>
      <EvidenceSearch projectId={projectId} />
      <ProjectEntities projectId={projectId} />
      <Conversations projectId={projectId} />
    </div>
  )
}
