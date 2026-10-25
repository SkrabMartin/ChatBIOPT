import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { ErrorNotice } from '../../components/ErrorNotice'
import { Button, Card, TextInput } from '../../components/ui'
import { useMe } from '../../lib/api/auth'
import { useAddMember, useLeaveProject, useMembers, useRemoveMember, useUpdateMember } from '../../lib/api/members'
import { PROJECT_ROLES, type ProjectRole } from '../../lib/api/workspace'

const selectClass = 'rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-700'

const roleHelp: Record<ProjectRole, string> = {
  viewer: 'Viewer: read only',
  editor: 'Editor: can change project content',
  owner: 'Owner: can also edit the project and manage members',
}

function RoleSelect({
  id,
  label,
  value,
  onChange,
  disabled,
}: {
  id: string
  label: string
  value: ProjectRole
  onChange: (role: ProjectRole) => void
  disabled?: boolean
}) {
  return (
    <>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value as ProjectRole)}
        disabled={disabled}
        className={selectClass}
      >
        {PROJECT_ROLES.map((role) => (
          <option key={role} value={role} title={roleHelp[role]}>
            {role}
          </option>
        ))}
      </select>
    </>
  )
}

function AddMemberForm({ projectId }: { projectId: string }) {
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<ProjectRole>('viewer')
  const add = useAddMember(projectId)

  function submit(event: FormEvent) {
    event.preventDefault()
    add.mutate({ email: email.trim(), role }, { onSuccess: () => setEmail('') })
  }

  return (
    <div className="space-y-2">
      <form onSubmit={submit} className="flex gap-2">
        <label htmlFor="member-email" className="sr-only">
          Member email
        </label>
        <TextInput
          id="member-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="colleague@example.org"
          required
        />
        <RoleSelect id="member-role" label="Role for new member" value={role} onChange={setRole} />
        <Button type="submit" disabled={add.isPending || !email.trim()} className="shrink-0">
          Add member
        </Button>
      </form>
      {add.error ? <ErrorNotice error={add.error} title="Could not add member" /> : null}
    </div>
  )
}

/** Project members; owners manage them, any member can leave. */
export function MembersCard({ projectId, isOwner }: { projectId: string; isOwner: boolean }) {
  const members = useMembers(projectId)
  const me = useMe()
  const update = useUpdateMember(projectId)
  const remove = useRemoveMember(projectId)
  const leave = useLeaveProject(projectId)
  const navigate = useNavigate()
  const busy = update.isPending || remove.isPending || leave.isPending
  const error = update.error ?? remove.error ?? leave.error

  function handleLeave(userId: string) {
    if (!window.confirm('Leave this project? You will lose access unless an owner adds you again.')) return
    remove.reset()
    update.reset()
    leave.mutate(userId, { onSuccess: () => navigate('/projects') })
  }

  function handleRemove(userId: string, email: string) {
    if (!window.confirm(`Remove ${email} from this project?`)) return
    update.reset()
    leave.reset()
    remove.mutate(userId)
  }

  function changeRole(userId: string, role: ProjectRole) {
    remove.reset()
    leave.reset()
    update.mutate({ userId, role })
  }

  return (
    <Card className="space-y-3">
      <h2 className="text-base font-semibold text-slate-900">Members</h2>
      {members.isPending ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : members.error ? (
        <ErrorNotice error={members.error} title="Could not load members" />
      ) : (
        <ul className="divide-y divide-slate-100">
          {members.data.map((member) => {
            const isSelf = member.user_id === me.data?.id
            return (
              <li key={member.user_id} className="flex flex-wrap items-center justify-between gap-3 py-2">
                <div className="min-w-0 text-sm">
                  <p className="font-medium text-slate-900">
                    {member.display_name}
                    {isSelf ? <span className="ml-1 text-xs font-normal text-slate-400">(you)</span> : null}
                  </p>
                  <p className="text-xs text-slate-500">{member.email}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {isOwner ? (
                    <RoleSelect
                      id={`role-${member.user_id}`}
                      label={`Role for ${member.email}`}
                      value={member.role}
                      onChange={(role) => changeRole(member.user_id, role)}
                      disabled={busy}
                    />
                  ) : (
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{member.role}</span>
                  )}
                  {isSelf ? (
                    <Button variant="danger" onClick={() => handleLeave(member.user_id)} disabled={busy}>
                      Leave project
                    </Button>
                  ) : isOwner ? (
                    <Button
                      variant="danger"
                      onClick={() => handleRemove(member.user_id, member.email)}
                      disabled={busy}
                      aria-label={`Remove ${member.email}`}
                    >
                      Remove
                    </Button>
                  ) : null}
                </div>
              </li>
            )
          })}
        </ul>
      )}
      {error ? <ErrorNotice error={error} title="Could not change membership" /> : null}
      {isOwner ? <AddMemberForm projectId={projectId} /> : null}
    </Card>
  )
}
