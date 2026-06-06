import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { fetchEngines, type Engine } from '../api'

function engineLabel(e: Engine): string {
  return e.owner_login ? `${e.owner_login}/${e.name}` : e.name
}

export default function Engines() {
  const [engines, setEngines] = useState<Engine[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchEngines()
      .then((d) => {
        if (!cancelled) setEngines(d)
      })
      .catch(() => {
        if (!cancelled) setError('failed to load engines')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold text-neutral-100">engines</h1>
        <Link
          to="/engine/upload"
          className="ml-auto text-sm text-neutral-400 hover:text-neutral-100"
        >
          upload an engine →
        </Link>
      </div>
      {error ? (
        <p className="text-red-400 text-sm">{error}</p>
      ) : engines === null ? (
        <p className="text-neutral-500 text-sm italic">loading…</p>
      ) : engines.length === 0 ? (
        <p className="text-neutral-500 text-sm italic">no engines yet</p>
      ) : (
        <div className="flex flex-col gap-2">
          {engines.map((e) => (
            <Link
              key={e.id}
              to={`/engine/${e.id}`}
              className="block border border-neutral-800 hover:border-neutral-600 rounded px-3 py-2 transition-colors"
            >
              <div className="flex items-center gap-2 text-sm">
                <span className="font-medium text-neutral-100">
                  {engineLabel(e)}
                </span>
                <span className="ml-auto text-xs text-neutral-500">
                  {e.version_count}{' '}
                  {e.version_count === 1 ? 'version' : 'versions'}
                </span>
              </div>
              {e.description && (
                <div className="text-xs text-neutral-500 mt-0.5">
                  {e.description}
                </div>
              )}
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
