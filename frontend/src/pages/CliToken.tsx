import { useState } from 'react'
import { createCliToken } from '../api'
import { useAuth } from '../auth-context'

export default function CliToken() {
  const { user, loading, login } = useAuth()
  const [token, setToken] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const generate = async () => {
    setBusy(true)
    setError(null)
    try {
      setToken(await createCliToken())
    } catch {
      setError('could not create a token')
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    if (!token) return
    await navigator.clipboard.writeText(token)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-8 flex flex-col gap-5">
      <h1 className="text-xl font-semibold text-neutral-100">CLI token</h1>
      <p className="text-neutral-400 text-sm">
        Generate a token, then paste it into the{' '}
        <span className="font-mono">machineplay login</span> prompt in your
        terminal.
      </p>

      {loading ? (
        <p className="text-neutral-500 text-sm italic">…</p>
      ) : !user ? (
        <button
          onClick={login}
          className="self-start bg-neutral-100 text-neutral-900 rounded px-3 py-1 text-sm"
        >
          sign in with GitHub
        </button>
      ) : token ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-neutral-900 border border-neutral-800 rounded px-3 py-2 text-sm font-mono text-neutral-200 break-all">
              {token}
            </code>
            <button
              onClick={copy}
              className="bg-neutral-100 text-neutral-900 rounded px-3 py-2 text-sm shrink-0"
            >
              {copied ? 'copied' : 'copy'}
            </button>
          </div>
          <p className="text-amber-500/80 text-xs">
            Copy it now — for security it won't be shown again.
          </p>
        </div>
      ) : (
        <button
          onClick={generate}
          disabled={busy}
          className="self-start bg-neutral-100 text-neutral-900 rounded px-3 py-1 text-sm disabled:opacity-40"
        >
          {busy ? 'generating…' : 'generate token'}
        </button>
      )}
      {error && <p className="text-red-400 text-sm">{error}</p>}
    </div>
  )
}
