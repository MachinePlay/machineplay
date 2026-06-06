import { useEffect, useState } from 'react'
import { useParams } from 'react-router'
import { fetchEngine, type EngineDetail as EngineDetailT } from '../api'

function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${n} B`
}

export default function EngineDetail() {
  const { id } = useParams()
  const [engine, setEngine] = useState<EngineDetailT | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    let cancelled = false
    fetchEngine(id)
      .then((d) => {
        if (!cancelled) setEngine(d)
      })
      .catch(() => {
        if (!cancelled) setError('engine not found')
      })
    return () => {
      cancelled = true
    }
  }, [id])

  if (error) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-10 text-red-400 text-sm">
        {error}
      </div>
    )
  }
  if (!engine) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-10 text-neutral-500 text-sm italic">
        loading…
      </div>
    )
  }

  const label = engine.owner_login
    ? `${engine.owner_login}/${engine.name}`
    : engine.name

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold text-neutral-100">{label}</h1>
        {engine.description && (
          <p className="text-neutral-400 text-sm">{engine.description}</p>
        )}
      </div>

      <p className="text-xs text-amber-500/80">
        Uploaded engines aren't playable yet — running them in games is coming
        soon.
      </p>

      <section className="flex flex-col gap-2">
        <h2 className="text-sm uppercase tracking-wide text-neutral-500">
          versions
        </h2>
        {engine.versions.length === 0 ? (
          <p className="text-neutral-500 text-sm italic">no versions uploaded</p>
        ) : (
          <div className="flex flex-col gap-1">
            {engine.versions.map((v) => (
              <div
                key={v.id}
                className="flex items-center gap-3 border border-neutral-800 rounded px-3 py-2 text-sm"
              >
                <span className="font-mono text-neutral-100">{v.version}</span>
                <span className="ml-auto text-xs text-neutral-500">
                  {formatBytes(v.size_bytes)}
                </span>
                <span className="text-xs text-neutral-500">
                  {new Date(v.created_at).toLocaleDateString()}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
