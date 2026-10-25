import { useState, type FormEvent } from 'react'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, Field, TextInput } from '../../components/ui'
import { useAdminUsers, useCreateUser, useUpdateUser, type AdminUser } from '../../lib/api/admin'
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, useMe } from '../../lib/api/auth'
import { ApiError } from '../../lib/api/client'
import { formatDateTime } from '../../lib/format'

function CreateUserForm() {
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  const create = useCreateUser()

  function submit(event: FormEvent) {
    event.preventDefault()
    create.mutate(
      { email: email.trim(), display_name: displayName.trim(), password, is_admin: isAdmin },
      {
        onSuccess: () => {
          setEmail('')
          setDisplayName('')
          setPassword('')
          setIsAdmin(false)
        },
      },
    )
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <Field label="Email" htmlFor="new-user-email">
        <TextInput id="new-user-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </Field>
      <Field label="Display name" htmlFor="new-user-name">
        <TextInput id="new-user-name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} required />
      </Field>
      <Field label="Initial password" htmlFor="new-user-password">
        <TextInput
          id="new-user-password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={PASSWORD_MIN_LENGTH}
          maxLength={PASSWORD_MAX_LENGTH}
          required
        />
      </Field>
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
        Administrator
      </label>
      <p className="text-xs text-slate-500">Password: at least {PASSWORD_MIN_LENGTH} characters.</p>
      {create.error ? <ErrorNotice error={create.error} title="Could not create user" /> : null}
      {create.data ? <p className="text-sm text-emerald-700">Created {create.data.email}.</p> : null}
      <Button
        type="submit"
        disabled={create.isPending || !email.trim() || !displayName.trim() || password.length < PASSWORD_MIN_LENGTH}
      >
        {create.isPending ? 'Creating…' : 'Create user'}
      </Button>
    </form>
  )
}

function UserRow({ user, isSelf }: { user: AdminUser; isSelf: boolean }) {
  const update = useUpdateUser()
  const [resetting, setResetting] = useState(false)
  const [password, setPassword] = useState('')

  function resetPassword(event: FormEvent) {
    event.preventDefault()
    update.mutate(
      { id: user.id, password },
      {
        onSuccess: () => {
          setPassword('')
          setResetting(false)
        },
      },
    )
  }

  return (
    <tr className="align-top">
      <td className="py-2 pr-3">
        {user.email}
        {isSelf ? <span className="ml-1 text-xs text-slate-400">(you)</span> : null}
      </td>
      <td className="py-2 pr-3">{user.display_name}</td>
      <td className="py-2 pr-3">{user.is_admin ? 'Yes' : 'No'}</td>
      <td className="py-2 pr-3">{user.disabled ? <span className="text-rose-700">Disabled</span> : 'Active'}</td>
      <td className="py-2 pr-3 text-xs text-slate-500">
        {user.last_login_at ? formatDateTime(user.last_login_at) : 'Never'}
      </td>
      <td className="space-y-2 py-2">
        <div className="flex flex-wrap gap-2">
          <Button
            variant={user.disabled ? 'secondary' : 'danger'}
            onClick={() => update.mutate({ id: user.id, disabled: !user.disabled })}
            disabled={update.isPending || isSelf}
          >
            {user.disabled ? 'Enable' : 'Disable'}
          </Button>
          <Button
            variant="secondary"
            onClick={() => update.mutate({ id: user.id, is_admin: !user.is_admin })}
            disabled={update.isPending || isSelf}
          >
            {user.is_admin ? 'Revoke admin' : 'Grant admin'}
          </Button>
          <Button variant="secondary" onClick={() => setResetting((v) => !v)} aria-expanded={resetting}>
            Reset password
          </Button>
        </div>
        {resetting ? (
          <form onSubmit={resetPassword} className="flex gap-2">
            <label htmlFor={`reset-${user.id}`} className="sr-only">
              New password for {user.email}
            </label>
            <TextInput
              id={`reset-${user.id}`}
              type="password"
              autoComplete="new-password"
              placeholder={`New password (min. ${PASSWORD_MIN_LENGTH})`}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={PASSWORD_MIN_LENGTH}
              maxLength={PASSWORD_MAX_LENGTH}
              required
            />
            <Button type="submit" disabled={update.isPending || password.length < PASSWORD_MIN_LENGTH} className="shrink-0">
              Set
            </Button>
          </form>
        ) : null}
        {update.error ? <ErrorNotice error={update.error} title="Could not update user" /> : null}
      </td>
    </tr>
  )
}

export function AdminUsersPage() {
  const users = useAdminUsers()
  const me = useMe()

  if (users.error instanceof ApiError && users.error.status === 403) {
    return (
      <div role="alert" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
        <p className="font-medium">Admins only</p>
        <p>You need administrator rights to manage users.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Users</h1>
        <p className="mt-1 text-sm text-slate-500">Accounts that can sign in. Disabled users are signed out immediately.</p>
      </header>
      <div className="grid gap-6 xl:grid-cols-[3fr_1fr]">
        <Card className="overflow-x-auto">
          {users.isPending ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : users.error ? (
            <ErrorNotice error={users.error} title="Could not load users" />
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr>
                  <th className="pb-2 pr-3 font-medium">Email</th>
                  <th className="pb-2 pr-3 font-medium">Name</th>
                  <th className="pb-2 pr-3 font-medium">Admin</th>
                  <th className="pb-2 pr-3 font-medium">Status</th>
                  <th className="pb-2 pr-3 font-medium">Last login</th>
                  <th className="pb-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {users.data.map((user) => (
                  <UserRow key={user.id} user={user} isSelf={user.id === me.data?.id} />
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card>
          <h2 className="mb-3 text-base font-semibold text-slate-900">New user</h2>
          <CreateUserForm />
        </Card>
      </div>
    </div>
  )
}
