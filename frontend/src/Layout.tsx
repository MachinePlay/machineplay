import { NavLink, Outlet } from 'react-router'
import { useAuth } from './auth-context'

function navLinkClass({ isActive }: { isActive: boolean }) {
  return [
    'px-2 py-1 rounded transition-colors',
    isActive
      ? 'text-neutral-100 bg-neutral-800'
      : 'text-neutral-400 hover:text-neutral-100',
  ].join(' ')
}

function AuthSlot() {
  const { user, loading, login, logout } = useAuth()

  if (loading) {
    return <div className="ml-auto text-neutral-600 text-xs">…</div>
  }

  if (!user) {
    return (
      <button
        onClick={login}
        className="ml-auto inline-flex items-center gap-1.5 rounded bg-neutral-100 px-3 py-1 text-neutral-900 hover:bg-white transition-colors"
      >
        <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden>
          <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
        </svg>
        sign in
      </button>
    )
  }

  return (
    <div className="ml-auto flex items-center gap-2">
      <NavLink to={`/u/${user.login}`} className="flex items-center gap-2">
        {user.avatar_url && (
          <img
            src={user.avatar_url}
            alt={user.login}
            className="h-6 w-6 rounded-full border border-neutral-700"
          />
        )}
        <span className="text-neutral-200">{user.login}</span>
      </NavLink>
      <button
        onClick={logout}
        className="text-neutral-500 hover:text-neutral-100 transition-colors"
      >
        logout
      </button>
    </div>
  )
}

export default function Layout() {
  return (
    <div className="min-h-dvh flex flex-col bg-neutral-950 text-neutral-100">
      <header className="border-b border-neutral-800">
        <nav className="max-w-5xl mx-auto px-4 py-3 flex items-center gap-4 text-sm">
          <NavLink to="/" className="font-semibold text-neutral-100">
            MachinePlay
          </NavLink>
          <NavLink to="/engine" className={navLinkClass}>
            engines
          </NavLink>
          <NavLink to="/tournament" className={navLinkClass}>
            tournaments
          </NavLink>
          <NavLink to="/about" className={navLinkClass}>
            about
          </NavLink>
          <AuthSlot />
        </nav>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  )
}
