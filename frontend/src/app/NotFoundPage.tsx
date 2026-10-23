import { Link } from 'react-router'

export function NotFoundPage() {
  return (
    <div className="space-y-2">
      <h1 className="text-2xl font-semibold text-slate-900">Page not found</h1>
      <Link to="/" className="text-sm text-teal-700 hover:underline">
        Back to dashboard
      </Link>
    </div>
  )
}
