import { useState, type FormEvent } from 'react'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, Field, TextInput } from '../../components/ui'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, useChangePassword, useMe } from '../../lib/api/auth'

/** Client-side checks the server would also reject; returns the first problem or null. */
function passwordProblem(password: string, confirmation: string): string | null {
  if (password.length < PASSWORD_MIN_LENGTH) return `New password must be at least ${PASSWORD_MIN_LENGTH} characters.`
  if (password !== confirmation) return 'New password and confirmation do not match.'
  return null
}

function ChangePasswordForm() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const change = useChangePassword()

  function submit(event: FormEvent) {
    event.preventDefault()
    const found = passwordProblem(next, confirmation)
    setProblem(found)
    if (found) {
      change.reset()
      return
    }
    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          setCurrent('')
          setNext('')
          setConfirmation('')
        },
      },
    )
  }

  return (
    <form onSubmit={submit} className="max-w-sm space-y-3">
      <Field label="Current password" htmlFor="current-password">
        <TextInput
          id="current-password"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
        />
      </Field>
      <Field label="New password" htmlFor="new-password">
        <TextInput
          id="new-password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          maxLength={PASSWORD_MAX_LENGTH}
          required
        />
      </Field>
      <Field label="Confirm new password" htmlFor="confirm-password">
        <TextInput
          id="confirm-password"
          type="password"
          autoComplete="new-password"
          value={confirmation}
          onChange={(e) => setConfirmation(e.target.value)}
          maxLength={PASSWORD_MAX_LENGTH}
          required
        />
      </Field>
      <p className="text-xs text-slate-500">At least {PASSWORD_MIN_LENGTH} characters. Your other sessions will be signed out.</p>
      {problem ? (
        <p role="alert" className="text-sm text-rose-700">
          {problem}
        </p>
      ) : null}
      {change.error ? <ErrorNotice error={change.error} title="Could not change password" /> : null}
      {change.isSuccess ? (
        <p role="status" className="text-sm text-emerald-700">
          Password changed. Other sessions were signed out.
        </p>
      ) : null}
      <Button type="submit" disabled={change.isPending || !current || !next || !confirmation}>
        {change.isPending ? 'Saving…' : 'Change password'}
      </Button>
    </form>
  )
}

export function AccountPage() {
  const me = useMe()
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Account</h1>
        {me.data ? (
          <p className="mt-1 text-sm text-slate-500">
            Signed in as {me.data.display_name} ({me.data.email}){me.data.is_admin ? ' · administrator' : ''}
          </p>
        ) : null}
      </header>
      <Card>
        <h2 className="mb-3 text-base font-semibold text-slate-900">Change password</h2>
        <ChangePasswordForm />
      </Card>
    </div>
  )
}
