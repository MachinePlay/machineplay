import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { Chessground } from '../Chessground'
import { useAuth } from '../auth-context'
import {
  API_URL,
  liveStreamUrl,
  START_FEN,
  type Engine,
  type Game,
  type LiveStreamEvent,
  type Runner,
} from '../api'

const LIVE_DISPLAY_LIMIT = 8

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime()
  const diff = Date.now() - t
  const m = Math.round(diff / 60_000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.round(h / 24)
  return `${d}d ago`
}

function LiveGameCard({ game }: { game: Game }) {
  const moveNo = Math.max(1, Math.ceil(game.moves.length / 2))
  return (
    <Link
      to={`/game/${game.id}`}
      className="group flex flex-col gap-2 rounded-lg border border-neutral-800 hover:border-neutral-600 bg-neutral-900/60 p-3 transition-colors"
    >
      <Chessground
        className="!w-full"
        config={{
          fen: game.fen,
          viewOnly: true,
          coordinates: false,
          drawable: { enabled: false },
        }}
      />
      <div className="flex items-center gap-2 text-sm">
        <span className="font-medium truncate">{game.white_name}</span>
        <span className="text-neutral-500">vs</span>
        <span className="font-medium truncate">{game.black_name}</span>
      </div>
      <div className="flex items-center gap-2 text-xs text-neutral-500">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
          live
        </span>
        <span className="ml-auto">move {moveNo}</span>
      </div>
    </Link>
  )
}

function RecentGameRow({ game }: { game: Game }) {
  return (
    <Link
      to={`/game/${game.id}`}
      className="block border border-neutral-800 hover:border-neutral-600 rounded px-3 py-2 transition-colors"
    >
      <div className="flex items-center gap-2 text-sm">
        <span className="font-medium">{game.white_name}</span>
        <span className="text-neutral-500">vs</span>
        <span className="font-medium">{game.black_name}</span>
        <span className="ml-auto font-mono text-xs text-neutral-400">
          {game.result ?? '*'}
        </span>
      </div>
      <div className="text-xs text-neutral-500 mt-0.5">
        {game.ended_at
          ? relativeTime(game.ended_at)
          : relativeTime(game.created_at)}
      </div>
    </Link>
  )
}

export default function Home() {
  const navigate = useNavigate()
  const { user, login } = useAuth()
  const [engines, setEngines] = useState<Engine[]>([])
  const [runners, setRunners] = useState<Runner[]>([])
  const [whiteId, setWhiteId] = useState('')
  const [blackId, setBlackId] = useState('')
  const [runnerId, setRunnerId] = useState('')
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const [games, setGames] = useState<Game[] | null>(null)
  const [gamesError, setGamesError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${API_URL}/engine`)
      .then((r) => r.json())
      .then((data: Engine[]) => {
        setEngines(data)
        if (data.length > 0) {
          setWhiteId(data[0].id)
          setBlackId(data[Math.min(1, data.length - 1)].id)
        }
      })
      .catch(() => setStartError('failed to load engines'))
  }, [])

  useEffect(() => {
    fetch(`${API_URL}/runners`)
      .then((r) => r.json())
      .then((data: Runner[]) => {
        setRunners(data)
        if (data.length > 0) setRunnerId(data[0].runner_id)
      })
      .catch(() => setStartError('failed to load runners'))
  }, [])

  useEffect(() => {
    let cancelled = false
    fetch(`${API_URL}/game`)
      .then((r) => r.json())
      .then((data: Game[]) => {
        if (!cancelled) setGames(data)
      })
      .catch(() => {
        if (!cancelled) setGamesError('failed to load games')
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const es = new EventSource(liveStreamUrl())
    es.onmessage = (e) => {
      const { game_id, event }: LiveStreamEvent = JSON.parse(e.data)
      setGames((prev) => {
        if (!prev) return prev
        const idx = prev.findIndex((g) => g.id === game_id)
        if (event.type === 'game_start') {
          if (idx < 0) {
            const fresh: Game = {
              id: game_id,
              white_id: '',
              black_id: '',
              white_name: event.white_name ?? '',
              black_name: event.black_name ?? '',
              status: 'playing',
              result: null,
              moves: [],
              fen: START_FEN,
              pgn: null,
              white_clock: 0,
              black_clock: 0,
              created_at: new Date().toISOString(),
              ended_at: null,
            }
            return [fresh, ...prev]
          }
          const next = [...prev]
          next[idx] = {
            ...prev[idx],
            white_name: event.white_name ?? prev[idx].white_name,
            black_name: event.black_name ?? prev[idx].black_name,
            status: 'playing',
            result: null,
            ended_at: null,
          }
          return next
        }
        if (event.type === 'fen') {
          const merged: Game = idx < 0
            ? {
                id: game_id,
                white_id: '',
                black_id: '',
                white_name: event.white_name ?? '',
                black_name: event.black_name ?? '',
                status: event.status === 'ended' ? 'ended' : 'playing',
                result: event.result,
                moves: event.moves,
                fen: event.fen,
                pgn: null,
                white_clock: event.white_clock,
                black_clock: event.black_clock,
                created_at: new Date().toISOString(),
                ended_at: null,
              }
            : {
                ...prev[idx],
                white_name: event.white_name ?? prev[idx].white_name,
                black_name: event.black_name ?? prev[idx].black_name,
                fen: event.fen,
                moves: event.moves,
                status: event.status === 'ended' ? 'ended' : 'playing',
                result: event.result,
                white_clock: event.white_clock,
                black_clock: event.black_clock,
              }
          if (idx < 0) return [merged, ...prev]
          const next = [...prev]
          next[idx] = merged
          return next
        }
        if (idx < 0) return prev
        if (event.type === 'move') {
          const next = [...prev]
          next[idx] = {
            ...prev[idx],
            fen: event.fen,
            moves: [...prev[idx].moves, event.san],
            white_clock: event.white_clock,
            black_clock: event.black_clock,
          }
          return next
        }
        if (event.type === 'game_end') {
          const next = [...prev]
          next[idx] = {
            ...prev[idx],
            status: 'ended',
            result: event.result,
            ended_at: new Date().toISOString(),
          }
          return next
        }
        return prev
      })
    }
    return () => es.close()
  }, [])

  const startGame = async () => {
    if (!whiteId || !blackId || !runnerId) return
    setStarting(true)
    setStartError(null)
    try {
      const r = await fetch(`${API_URL}/game`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          white_engine_id: whiteId,
          black_engine_id: blackId,
          runner_id: runnerId,
        }),
      })
      if (!r.ok) {
        const t = await r.text()
        setStartError(t || `error ${r.status}`)
        return
      }
      const data: { id: string } = await r.json()
      navigate(`/game/${data.id}`)
    } catch (e) {
      setStartError(String(e))
    } finally {
      setStarting(false)
    }
  }

  const allLive = (games ?? []).filter((g) => g.status === 'playing')
  const live = allLive.slice(0, LIVE_DISPLAY_LIMIT)
  const recent = (games ?? []).filter((g) => g.status === 'ended').slice(0, 20)

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <h2 className="text-sm uppercase tracking-wide text-neutral-500">
          new game
        </h2>
        <div className="flex flex-col sm:flex-row gap-2 sm:gap-3 items-stretch sm:items-center text-sm">
          <label className="flex items-center gap-2">
            <span className="text-neutral-400 w-12">white</span>
            <select
              className="bg-neutral-900 border border-neutral-800 rounded px-2 py-1 flex-1"
              value={whiteId}
              onChange={(e) => setWhiteId(e.target.value)}
            >
              {engines.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="text-neutral-400 w-12">black</span>
            <select
              className="bg-neutral-900 border border-neutral-800 rounded px-2 py-1 flex-1"
              value={blackId}
              onChange={(e) => setBlackId(e.target.value)}
            >
              {engines.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="text-neutral-400 w-12">runner</span>
            <select
              className="bg-neutral-900 border border-neutral-800 rounded px-2 py-1 flex-1"
              value={runnerId}
              onChange={(e) => setRunnerId(e.target.value)}
              disabled={runners.length === 0}
            >
              {runners.length === 0 ? (
                <option value="">no runners connected</option>
              ) : (
                runners.map((r) => (
                  <option key={r.runner_id} value={r.runner_id}>
                    {r.name}
                  </option>
                ))
              )}
            </select>
          </label>
          {user ? (
            <button
              onClick={startGame}
              disabled={
                starting ||
                engines.length === 0 ||
                !whiteId ||
                !blackId ||
                !runnerId
              }
              className="bg-neutral-100 text-neutral-900 rounded px-3 py-1 disabled:opacity-40"
            >
              {starting ? 'starting…' : 'start game'}
            </button>
          ) : (
            <button
              onClick={login}
              className="bg-neutral-100 text-neutral-900 rounded px-3 py-1"
            >
              sign in to start a game
            </button>
          )}
          {startError && (
            <span className="text-red-400 text-xs self-center">{startError}</span>
          )}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm uppercase tracking-wide text-neutral-500">
          live
          {allLive.length > 0 && (
            <span className="ml-2 text-neutral-400 normal-case">
              ({allLive.length})
            </span>
          )}
        </h2>
        {games === null ? (
          <p className="text-neutral-500 text-sm italic">loading…</p>
        ) : live.length === 0 ? (
          <p className="text-neutral-500 text-sm italic">no live games</p>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 sm:gap-4">
            {live.map((g) => (
              <LiveGameCard key={g.id} game={g} />
            ))}
          </div>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm uppercase tracking-wide text-neutral-500">
          recent
        </h2>
        {gamesError ? (
          <p className="text-red-400 text-sm">{gamesError}</p>
        ) : games === null ? (
          <p className="text-neutral-500 text-sm italic">loading…</p>
        ) : recent.length === 0 ? (
          <p className="text-neutral-500 text-sm italic">no games yet</p>
        ) : (
          <div className="flex flex-col gap-2">
            {recent.map((g) => (
              <RecentGameRow key={g.id} game={g} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
