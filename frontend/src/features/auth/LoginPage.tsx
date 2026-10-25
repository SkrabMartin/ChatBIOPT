import { useState, type FormEvent } from 'react'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, Field, TextInput } from '../../components/ui'
import { useLogin } from '../../lib/api/auth'
import { ApiError } from '../../lib/api/client'

export function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const login = useLogin()

  function submit(event: FormEvent) {
    event.preventDefault()
    login.mutate({ email: email.trim(), password })
  }

  const rateLimited = login.error instanceof ApiError && login.error.code === 'rate_limited'

  return (
    <div className="flex h-full items-center justify-center bg-slate-50 px-4 font-sans">
      <Card className="w-full max-w-sm">
        <div className="mb-4 flex items-center gap-2">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <h1 className="text-lg font-semibold text-slate-900">Sign in to BioResearch AI</h1>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <Field label="Email" htmlFor="login-email">
            <TextInput
              id="login-email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </Field>
          <Field label="Password" htmlFor="login-password">
            <TextInput
              id="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </Field>
          {login.error ? (
            <ErrorNotice error={login.error} title={rateLimited ? 'Too many attempts' : 'Could not sign in'} />
          ) : null}
          <Button type="submit" disabled={login.isPending || !email.trim() || !password} className="w-full">
            {login.isPending ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
        <p className="mt-4 text-xs text-slate-400">Accounts are created by an administrator.</p>
      </Card>
    </div>
  )
}
