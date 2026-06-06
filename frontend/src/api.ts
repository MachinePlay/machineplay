import type {
  EngineDetailOut,
  EngineOut,
  EngineVersionOut,
  GameOut,
  RunnerOut,
  SseStreamResponse,
  TokenOut,
  UserOut,
} from './api/generated'

export const API_URL = import.meta.env.VITE_API_URL as string
export const gameStreamUrl = (gameId: string): string =>
  `${API_URL}/stream/game/${gameId}`
export const liveStreamUrl = (): string => `${API_URL}/stream/live`

// Kicks off the GitHub OAuth flow; the backend redirects back here when done.
export const githubLoginUrl = (): string => `${API_URL}/auth/github/login`

// Fetch the logged-in user, or null when the session cookie is absent/expired.
export async function fetchMe(): Promise<User | null> {
  const r = await fetch(`${API_URL}/me`, { credentials: 'include' })
  if (r.status === 401) return null
  if (!r.ok) throw new Error(`GET /me failed: ${r.status}`)
  return (await r.json()) as User
}

export async function logout(): Promise<void> {
  await fetch(`${API_URL}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  })
}

export async function fetchEngines(): Promise<Engine[]> {
  const r = await fetch(`${API_URL}/engine`)
  if (!r.ok) throw new Error(`GET /engine failed: ${r.status}`)
  return (await r.json()) as Engine[]
}

export async function fetchEngine(id: string): Promise<EngineDetail> {
  const r = await fetch(`${API_URL}/engine/${id}`)
  if (!r.ok) throw new Error(`GET /engine/${id} failed: ${r.status}`)
  return (await r.json()) as EngineDetail
}

// Mint a CLI API token for the logged-in user (shown once). Session-authed.
export async function createCliToken(): Promise<string> {
  const r = await fetch(`${API_URL}/me/tokens`, {
    method: 'POST',
    credentials: 'include',
  })
  if (!r.ok) throw new Error(`POST /me/tokens failed: ${r.status}`)
  return ((await r.json()) as TokenOut).token
}

export const START_FEN =
  'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

export type Engine = EngineOut
export type EngineDetail = EngineDetailOut
export type EngineVersion = EngineVersionOut
export type Runner = RunnerOut
export type Game = GameOut
export type User = UserOut
export type StreamEvent = SseStreamResponse

export type { GameStatus, LiveStreamEvent } from './api/generated'
