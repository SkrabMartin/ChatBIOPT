import { useEffect, useState } from 'react'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button } from '../../components/ui'
import { ApiError } from '../../lib/api/client'
import {
  ACTIVE_STATUSES,
  followRunEvents,
  useCancelRun,
  useRunFinished,
  useStartRun,
  type Grounding,
  type Run,
} from '../../lib/api/runs'
import type { Message } from '../../lib/api/workspace'
import { MarkdownContent } from './MessageContent'

type StreamState =
  | { phase: 'streaming'; text: string; stage: 'waiting' | 'retrieving' | 'generating'; passages: number | null }
  | { phase: 'ended' }
  | { phase: 'lost'; message: string }

/** The grounded prompt asks for a first "STATUS: ..." line; it is metadata, not part of the visible answer. */
function visibleText(text: string): string {
  return text.replace(/^\s*\**\s*STATUS\s*:[^\n]*\n?/i, '')
}

/**
 * Follows one run's event stream. Any terminal event calls `onEnded`: the outcome (answer, failure, cancel)
 * is then read from the persisted run and messages, so it looks the same after a reload.
 */
function useRunStream(runId: string, onEnded: () => void): StreamState {
  const [state, setState] = useState<StreamState>({ phase: 'streaming', text: '', stage: 'waiting', passages: null })

  useEffect(() => {
    const controller = new AbortController()
    followRunEvents(
      runId,
      (event) => {
        if (event.type === 'delta') {
          setState((s) => (s.phase === 'streaming' ? { ...s, text: s.text + event.data.text } : s))
        } else if (event.type === 'status') {
          const stage = event.data.status === 'retrieving' ? 'retrieving' : 'generating'
          setState((s) => (s.phase === 'streaming' ? { ...s, stage } : s))
        } else if (event.type === 'evidence') {
          setState((s) => (s.phase === 'streaming' ? { ...s, passages: event.data.items.length } : s))
        } else {
          setState({ phase: 'ended' })
          onEnded()
        }
      },
      controller.signal,
    ).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setState({ phase: 'lost', message: error instanceof Error ? error.message : 'Lost the response stream' })
      }
    })
    return () => controller.abort()
    // onEnded is intentionally excluded: re-subscribing on every render would restart the stream.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  return state
}

function Streaming({ run, onEnded }: { run: Run; onEnded: () => void }) {
  const state = useRunStream(run.id, onEnded)
  const cancel = useCancelRun()

  if (state.phase === 'ended') return null
  if (state.phase === 'lost') {
    return <ErrorNotice error={new Error(state.message)} title="Lost connection to the response" />
  }
  return (
    <div className="flex justify-start" aria-live="polite">
      <div className="max-w-[75%] rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-900">
        <p className="mb-0.5 flex items-center gap-2 text-xs text-slate-400">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-teal-600" />
          {state.stage === 'retrieving'
            ? 'Searching the project evidence library…'
            : `Generating with ${run.model_profile}${state.passages !== null ? ` from ${state.passages} passage(s)` : ''}…`}
        </p>
        {visibleText(state.text) ? (
          <MarkdownContent text={visibleText(state.text)} />
        ) : (
          <p className="text-sm text-slate-400">Waiting for the model…</p>
        )}
        <div className="mt-2">
          <Button variant="secondary" onClick={() => cancel.mutate(run.id)} disabled={cancel.isPending}>
            Stop
          </Button>
        </div>
        {cancel.error ? <ErrorNotice error={cancel.error} title="Could not stop" /> : null}
      </div>
    </div>
  )
}

const failureTitle: Record<string, string> = {
  model_unavailable: 'Model unavailable',
  model_timeout: 'The model did not respond in time',
  run_timeout: 'The model did not finish in time',
  context_too_long: 'Conversation too long for this model',
  interrupted: 'Generation was interrupted',
}

/**
 * The answer slot below the latest user message: live stream, last failure with retry, or a start button.
 * Never shows text that the server did not stream or store.
 */
export function PendingAnswer({
  conversationId,
  lastMessage,
  runs,
  modelProfile,
  grounding,
}: {
  conversationId: string
  lastMessage: Message | undefined
  runs: Run[]
  modelProfile: string | null
  grounding: Grounding
}) {
  const start = useStartRun(conversationId)
  const finished = useRunFinished(conversationId)
  const latest = runs[0]

  if (latest && ACTIVE_STATUSES[latest.status]) {
    return <Streaming key={latest.id} run={latest} onEnded={() => void finished()} />
  }
  if (!lastMessage || lastMessage.role !== 'user') return null

  const lastAttempt = latest?.context_until_sequence === lastMessage.sequence ? latest : undefined
  return (
    <div className="space-y-2">
      {lastAttempt?.status === 'failed' ? (
        <ErrorNotice
          error={new ApiError(lastAttempt.error_message ?? 'Generation failed', null, lastAttempt.error_code, null)}
          title={failureTitle[lastAttempt.error_code ?? ''] ?? 'Generation failed'}
        />
      ) : null}
      {lastAttempt?.status === 'cancelled' ? <p className="text-xs text-slate-500">Generation stopped.</p> : null}
      {start.error ? <ErrorNotice error={start.error} title="Could not start generation" /> : null}
      <Button variant="secondary" onClick={() => start.mutate({ modelProfile, grounding })} disabled={start.isPending}>
        {lastAttempt ? 'Retry answer' : 'Generate answer'}
      </Button>
    </div>
  )
}
