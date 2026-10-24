import { NavLink, Outlet } from 'react-router'

const navItems = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/projects', label: 'Projects', end: false },
  { to: '/literature', label: 'Literature', end: false },
  { to: '/settings', label: 'Settings', end: false },
]

export function Layout() {
  return (
    <div className="flex h-full bg-slate-50 font-sans">
      <aside className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2 px-5 py-4">
          <img src="/favicon.svg" alt="" className="h-7 w-7" />
          <span className="font-semibold text-slate-900">BioResearch AI</span>
        </div>
        <nav aria-label="Main" className="flex flex-col gap-1 px-3">
          {navItems.map((item) => (
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
        <p className="mt-auto px-5 py-4 text-xs text-slate-400">Research use only — not for clinical decisions.</p>
      </aside>
      <main className="flex-1 overflow-y-auto px-8 py-6">
        <Outlet />
      </main>
    </div>
  )
}
