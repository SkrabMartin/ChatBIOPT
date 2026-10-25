import { NavLink, Outlet } from 'react-router'
import { ErrorNotice } from '../components/ErrorNotice'
import { Button } from '../components/ui'
import { useLogout, useMe } from '../lib/api/auth'

const navItems = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/projects', label: 'Projects', end: false },
  { to: '/literature', label: 'Literature', end: false },
  { to: '/evaluations', label: 'Evaluations', end: false },
  { to: '/settings', label: 'Settings', end: false },
  { to: '/account', label: 'Account', end: false },
]
const adminNavItems = [{ to: '/admin/users', label: 'Users', end: false }]

function CurrentUser() {
  const me = useMe()
  const logout = useLogout()
  if (!me.data) return null
  return (
    <div className="space-y-2 border-t border-slate-100 px-5 py-3">
      <div className="min-w-0 text-sm">
        <p className="truncate font-medium text-slate-900">{me.data.display_name}</p>
        <p className="truncate text-xs text-slate-500">{me.data.email}</p>
      </div>
      <Button variant="secondary" onClick={() => logout.mutate()} disabled={logout.isPending} className="w-full">
        {logout.isPending ? 'Signing out…' : 'Log out'}
      </Button>
      {logout.error ? <ErrorNotice error={logout.error} title="Could not log out" /> : null}
    </div>
  )
}

export function Layout() {
  const me = useMe()
  const items = me.data?.is_admin ? [...navItems, ...adminNavItems] : navItems
  return (
    <div className="flex h-full bg-slate-50 font-sans">
      <aside className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2 px-5 py-4">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="font-semibold text-slate-900">BioResearch AI</span>
        </div>
        <nav aria-label="Main" className="flex flex-col gap-1 px-3">
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `rounded-md px-3 py-2 text-sm ${isActive ? 'bg-teal-50 font-medium text-teal-800' : 'text-slate-600 hover:bg-slate-100'}`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto">
          <CurrentUser />
          <p className="px-5 py-4 text-xs text-slate-400">Research use only — not for clinical decisions.</p>
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto px-8 py-6">
        <Outlet />
      </main>
    </div>
  )
}
