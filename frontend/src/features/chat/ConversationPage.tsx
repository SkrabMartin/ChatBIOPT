import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, TextArea, TextInput } from '../../components/ui'
import { ApiError } from '../../lib/api/client'
import {
  MESSAGE_MAX_LENGTH,
  TITLE_MAX_LENGTH,
  useConversation,
  useDeleteConversation,
  useMessages,
  usePostMessage,
  useProject,
  useUpdateConversation,
  type Conversation,
  type Message,
} from '../../lib/api/workspace'
import { ACTIVE_STATUSES, useConversationRuns, useModels, useRun, useStartRun } from '../../lib/api/runs'
import { formatDateTime } from '../../lib/format'
import { useModelPreference } from '../../lib/preferences'
import { MarkdownContent } from './MessageContent'
import { PendingAnswer } from './PendingAnswer'

const roleLabel: Record<Message['role'], string> = { user: 'You', assistant: 'Assistant', system: 'System' }

function RunDetails({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false)
  const run = useRun(runId, open)
  return (
    <div className="mt-1">
      <button type="button" onClick={() => setOpen((v) => !v)} className="text-xs text-slate-400 hover:underline">
        {open ? 'Hide run details' : 'Run details'}
      </button>
      {open && run.data ? (
        <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 text-xs text-slate-500">
          <dt>Model</dt>
          <dd className="font-mono">
            {run.data.model_id} ({run.data.model_profile})
          </dd>
          <dt>Revision</dt>
          <dd className="break-all font-mono">{run.data.model_revision ?? '—'}</dd>
          <dt>Prompt</dt>
          <dd className="font-mono">{run.data.prompt_version}</dd>
          <dt>Tokens</dt>
          <dd>
            {run.data.input_tokens ?? '?'} in · {run.data.output_tokens ?? '?'} out · context{' '}
            {run.data.context_message_count ?? '?'} msgs
          </dd>
          <dt>Latency</dt>
          <dd>
            first token {run.data.time_to_first_token_ms ?? '?'} ms · total {run.data.latency_ms ?? '?'} ms
          </dd>
          <dt>Run</dt>
          <dd className="font-mono">{run.data.id}</dd>
        </dl>
      ) : open && run.error ? (
        <ErrorNotice error={run.error} title="Could not load run" />
      ) : null}
    </div>
  )
}

function MessageItem({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  return (
    <li className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[75%] rounded-lg px-3 py-2 ${isUser ? 'bg-teal-700 text-white' : 'border border-slate-200 bg-white text-slate-900'}`}
      >
        <p className={`mb-0.5 text-xs ${isUser ? 'text-teal-100' : 'text-slate-400'}`}>
          {roleLabel[message.role]} · #{message.sequence} · {formatDateTime(message.created_at)}
        </p>
        {isUser ? (
          // User text is shown verbatim, never interpreted as HTML or Markdown.
          <p className="whitespace-pre-wrap break-words text-sm">{message.content}</p>
        ) : (
          <>
            <MarkdownContent text={message.content} />
            <p className="mt-2 border-t border-slate-100 pt-1 text-xs text-amber-700">
              Model output · not grounded in retrieved sources · verify before use
            </p>
            {message.run_id ? <RunDetails runId={message.run_id} /> : null}
          </>
        )}
      </div>
    </li>
  )
}

function ModelSelect({ value, onChange }: { value: string | null; onChange: (profile: string | null) => void }) {
  const models = useModels()
  if (!models.data) return null
  return (
    <label className="flex items-center gap-1.5 text-xs text-slate-500">
      Model
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || null)}
        className="rounded border border-slate-200 bg-white px-1.5 py-1 text-xs text-slate-700"
      >
        <option value="">Default ({models.data.default_profile})</option>
        {models.data.items.map((m) => (
          <option key={m.id} value={m.id} disabled={!m.available}>
            {m.label}
            {m.available ? '' : ' — unavailable'}
          </option>
        ))}
      </select>
    </label>
  )
}

function Composer({ conversationId, busy }: { conversationId: string; busy: boolean }) {
  const [content, setContent] = useState('')
  const [modelProfile, setModelProfile] = useModelPreference()
  const post = usePostMessage(conversationId)
  const start = useStartRun(conversationId)
  const canSend = content.trim().length > 0 && !post.isPending && !busy

  function send() {
    if (!canSend) return
    post.mutate(content, {
      onSuccess: () => {
        setContent('')
        start.mutate(modelProfile)
      },
    })
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    send()
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      send()
    }
  }

  return (
    <form onSubmit={submit} className="space-y-2">
      <label htmlFor="message-content" className="sr-only">
        Message
      </label>
      <TextArea
        id="message-content"
        value={content}
        onChange={(e) => setContent(e.target.value)}
        onKeyDown={onKeyDown}
        maxLength={MESSAGE_MAX_LENGTH}
        rows={3}
        placeholder="Ask a research question… (Ctrl+Enter to send)"
      />
      {post.error ? <ErrorNotice error={post.error} title="Message not saved" /> : null}
      {start.error ? <ErrorNotice error={start.error} title="Could not start generation" /> : null}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <ModelSelect value={modelProfile} onChange={setModelProfile} />
          <p className="text-xs text-slate-400">No literature retrieval yet: answers come from the model alone.</p>
        </div>
        <Button type="submit" disabled={!canSend}>
          {post.isPending ? 'Sending…' : busy ? 'Generating…' : 'Send'}
        </Button>
      </div>
    </form>
  )
}

function ConversationHeader({ conversation, projectTitle }: { conversation: Conversation; projectTitle?: string }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(conversation.title)
  const update = useUpdateConversation(conversation.id)
  const remove = useDeleteConversation(conversation.id, conversation.project_id)
  const navigate = useNavigate()
  const projectPath = `/projects/${conversation.project_id}`

  function rename(event: FormEvent) {
    event.preventDefault()
    update.mutate({ title }, { onSuccess: () => setEditing(false) })
  }

  function handleDelete() {
    if (!window.confirm(`Delete conversation "${conversation.title}" and its messages? This cannot be undone.`)) return
    remove.mutate(undefined, { onSuccess: () => navigate(projectPath) })
  }

  return (
    <div className="space-y-2">
      <nav className="text-sm text-slate-500">
        <Link to="/projects" className="hover:underline">
          Projects
        </Link>{' '}
        /{' '}
        <Link to={projectPath} className="hover:underline">
          {projectTitle ?? 'Project'}
        </Link>{' '}
        / <span className="text-slate-700">{conversation.title}</span>
      </nav>
      {editing ? (
        <form onSubmit={rename} className="flex gap-2">
          <label htmlFor="rename-conversation" className="sr-only">
            Conversation title
          </label>
          <TextInput
            id="rename-conversation"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={TITLE_MAX_LENGTH}
            required
          />
          <Button type="submit" disabled={update.isPending || !title.trim()}>
            Save
          </Button>
          <Button
            variant="secondary"
            onClick={() => {
              setTitle(conversation.title)
              setEditing(false)
            }}
          >
            Cancel
          </Button>
        </form>
      ) : (
        <div className="flex items-start justify-between gap-4">
          <h1 className="text-2xl font-semibold text-slate-900">{conversation.title}</h1>
          <div className="flex shrink-0 gap-2">
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Rename
            </Button>
            <Button variant="danger" onClick={handleDelete} disabled={remove.isPending}>
              Delete
            </Button>
          </div>
        </div>
      )}
      {update.error ? <ErrorNotice error={update.error} title="Could not rename" /> : null}
      {remove.error ? <ErrorNotice error={remove.error} title="Could not delete" /> : null}
    </div>
  )
}

export function ConversationPage() {
  const { projectId = '', conversationId = '' } = useParams()
  const conversation = useConversation(conversationId)
  const project = useProject(projectId)
  const messages = useMessages(conversationId)
  const runs = useConversationRuns(conversationId)
  const [modelProfile] = useModelPreference()
  const bottomRef = useRef<HTMLDivElement>(null)
  const messageCount = messages.data?.length ?? 0
  const latestRun = runs.data?.[0]
  const busy = latestRun !== undefined && ACTIVE_STATUSES[latestRun.status]

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [messageCount, latestRun?.id])

  if (conversation.isPending) return <p className="text-sm text-slate-500">Loading…</p>
  if (conversation.error) {
    const missing =
      conversation.error instanceof ApiError && (conversation.error.status === 404 || conversation.error.status === 422)
    return (
      <div className="space-y-3">
        <ErrorNotice error={conversation.error} title={missing ? 'Conversation not found' : 'Could not load conversation'} />
        <Link to={`/projects/${projectId}`} className="text-sm text-teal-700 hover:underline">
          Back to project
        </Link>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <ConversationHeader conversation={conversation.data} projectTitle={project.data?.title} />
      <Card className="flex-1 overflow-y-auto bg-slate-50">
        {messages.isPending ? (
          <p className="text-sm text-slate-500">Loading messages…</p>
        ) : messages.error ? (
          <ErrorNotice error={messages.error} title="Could not load messages" />
        ) : messages.data.length === 0 ? (
          <p className="text-sm text-slate-500">No messages yet. Start with your research question.</p>
        ) : (
          <ol aria-label="Messages" className="space-y-3">
            {messages.data.map((m) => (
              <MessageItem key={m.id} message={m} />
            ))}
          </ol>
        )}
        {messages.data && runs.data ? (
          <div className="mt-3">
            <PendingAnswer
              conversationId={conversationId}
              lastMessage={messages.data.at(-1)}
              runs={runs.data}
              modelProfile={modelProfile}
            />
          </div>
        ) : null}
        <div ref={bottomRef} />
      </Card>
      <Composer conversationId={conversationId} busy={busy} />
    </div>
  )
}
