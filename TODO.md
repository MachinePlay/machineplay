# Machineplay — Roadmap

**Main goal (MVP):** users log in with GitHub, fork a starter engine template, upload their UCI engine as a Docker image, and watch tournaments play out in realtime.

Subgoals are intentionally tiny — each box should be one short session. Milestones are roughly ordered, but feel free to jump around. No CI for the MVP — pre-commit hooks at most.

---

## M0 — Meta: domain & org migration
- [x] Register `machineplay.org`
- [x] Create GitHub organization (e.g. `machineplay`)
- [x] Transfer this repo from personal account to the org
- [x] Update local git remote (`git remote set-url origin …`)
- [x] Point `machineplay.org` DNS to the VPS (A/AAAA records)
- [x] Point `api.machineplay.org` DNS to the VPS
- [x] Swap nginx vhosts to the new domains
- [x] Issue Let's Encrypt certs for `machineplay.org` + `api.machineplay.org`
- [x] Update frontend `.env.production` `VITE_API_URL` to `https://api.machineplay.org`
- [x] Update backend CORS `allow_origins` to `https://machineplay.org`
- [x] Tear down `*.saegl.me` nginx vhosts and DNS
- [x] Update README, deploy scripts, and any hardcoded URLs in code/docs
- [x] Decide license (MIT/Apache-2.0) and add `LICENSE`

## M1 — Frontend polish (warm-up wins)
- [x] Show engine names above the board during a game (white / black labels)
- [x] Show move count / ply counter as "move 12" instead of "ply 24"
- [x] Render a move list panel next to the board (SAN, scrollable)
- [x] Show captured pieces under each side
- [x] Add a "flip board" button
- [x] Show the SSE connection status as a small dot (green/red), not a word
- [x] Make the board responsive on mobile (board fits viewport, controls below)
- [x] Add a favicon
- [x] Add basic page title + meta description

## M1.5 — Routing & pages
The frontend is a single `App.tsx` today. Wire up routing and stub each page; later milestones fill the content in.
- [x] Add `react-router` to the frontend
- [x] Shared layout: navbar with logo, links, login/avatar slot
- [x] `/` — home: live games + recent games + recent tournaments (filled by M4/M8)
- [x] `/engine` — list of all engines (M3)
- [x] `/engine/{id}` — engine detail page (M3)
- [x] `/engine/upload` — upload form (M6)
- [x] `/game/{id}` — single-game viewer with board + clocks + move list (M2/M4)
- [x] `/tournament` — list of all tournaments (M8)
- [x] `/tournament/new` — create-tournament form (M8)
- [x] `/tournament/{id}` — pairings + standings + live game link (M8)
- [x] `/u/{login}` — user profile: their engines and recent games (M5)
- [x] `/about` — short pitch + link to starter template (M7)
- [x] 404 page

## M2 — Clocks & game state
- [x] Send clocks in the `move` event from backend (already tracked in `game.py`, just include them)
- [x] Render two clocks on the frontend, ticking down for the side to move
- [x] Show `game_end` as a banner overlay (1-0 / 0-1 / ½-½) with reason
- [ ] Surface engine resign / time-loss reasons (currently only `is_game_over`)
- [ ] If an engine process crashes mid-game, end with forfeit by that side

## M3 — Engine CRUD UI
- [ ] `GET /engine/{id}` endpoint
- [ ] `DELETE /engine/{id}` endpoint
- [ ] `/engine` page listing all engines with name + description + owner
- [ ] Engine detail page with description and games played count

## M4 — Multi-game (break the singleton)
- [x] `Game` Beanie document: id, white_id, black_id, status, result, created_at, pgn
- [ ] One `GameStream` per game id (registry/dict keyed by game id)
- [x] `POST /game` returns a game id; current behaviour becomes "start and return id"
- [x] `GET /game/{id}` returns metadata + current FEN
- [ ] `GET /sse/stream/{game_id}` instead of the global stream
- [x] `GET /game` lists recent games
- [x] Persist moves to the Game doc as they happen (append to `moves: list[str]`)
- [x] Persist final PGN on `game_end`
- [x] `/game/{id}` page on the frontend to watch any past or live game
- [x] Home page lists "live now" + "recent" games
- [ ] Only one live game at a time; further `POST /game` calls queue with `status=pending`
- [ ] Worker promotes the next `pending` game to `running` when the current one finishes
- [ ] `POST /game/{id}/cancel` to stop a running or pending game

## M5 — GitHub auth
- [x] Use signed-cookie sessions via Starlette `SessionMiddleware`
- [ ] Register OAuth app on GitHub, store client id/secret in `.env` (`.env`/`.env.example` slots ready; needs real credentials)
- [x] `GET /auth/github/login` → redirect to GitHub
- [x] `GET /auth/github/callback` → exchange code, set session cookie
- [x] `User` Beanie document: github_id, login, avatar_url, is_admin, created_at
- [x] `GET /me` returns current user (or 401)
- [x] `POST /auth/logout`
- [x] Login button in the navbar; avatar + logout when signed in
- [x] Decide who can start games (any logged-in user, with concurrency cap from M4)
- [x] Gate `POST /game` behind auth (no `/engine/upload` endpoint yet — gate in M6)
- [ ] Add `owner_id` to `Engine`; only owner (or admin) can delete
- [x] Script `scripts/promote_admin.py <github_login>` to flip `is_admin=True`

## M6 — Docker engine upload
- [ ] Pick image transport (default: multipart `docker save` tarball — registry push is M+1)
- [ ] Decide docker daemon access (rootless docker, `docker` group, or socket-proxy) — write one-line note in deploy README
- [ ] Add `storage/` to `.gitignore`
- [ ] `POST /engine/upload` accepts a `.tar` from `docker save`, saves to `storage/engines/{engine_id}.tar`
- [ ] On upload: `docker load` the tarball, tag as `machineplay-engine:{engine_id}`
- [ ] Replace `command: str` on `Engine` with `image_tag: str` (always docker)
- [ ] Run engine via `docker run --rm -i --network none --memory 512m --cpus 1 <image>` and pipe UCI over stdio
- [ ] Delete `scripts/seed_stockfish.py` — no more seeded binary engines
- [ ] Engine sandboxing: `--read-only`, `--cap-drop ALL`, `--security-opt no-new-privileges`
- [ ] Per-game wallclock timeout: kill container after N minutes regardless of clock
- [ ] On engine delete: remove image (`docker image rm`) and tarball
- [ ] Cap engines per user (e.g. 5) and per-engine tarball size (e.g. 200MB)
- [ ] Surface docker load / run errors clearly to the user
- [ ] Smoke-test endpoint: spin up the image, send `uci`, verify `uciok` reply, return ok/fail

## M7 — Starter template
- [ ] Create `template-engine/` repo (separate) — minimal Python UCI engine that plays random legal moves
- [ ] Include `Dockerfile` and `README` with build/upload instructions
- [ ] "Fork starter template" button → opens GitHub fork URL
- [ ] Docs page on the site explaining the protocol contract + how to upload

## M8 — Tournaments
- [ ] `Tournament` doc: name, participant_ids, status, created_at
- [ ] `POST /tournament` create (round-robin only for MVP)
- [ ] Round-robin pairing generator
- [ ] Run games sequentially within a tournament (reuse M4 concurrency queue)
- [ ] Standings calculation (W/L/D + score)
- [ ] `/tournament/new` page: name + multi-select engines
- [ ] "Start tournament" button → schedules all pairings
- [ ] `/tournament/{id}` page: pairings, standings, live game link
- [ ] List tournaments on home page

## M9 — Quality, ops, polish
- [ ] Add `pytest` and one smoke test for `GET /engine`
- [ ] Capture engine stdout/stderr to `storage/logs/{game_id}.log` per game
- [ ] Rate-limit `POST /game` and `POST /engine/upload` per user
- [ ] Backup script for MongoDB (cron'd `mongodump`)
- [ ] Add `ruff` + frontend `tsc`/`eslint` to `.pre-commit-config.yaml`
- [ ] Document deploy steps in README

## Stretch / nice-to-have
- [ ] Volunteer worker daemon (Rust CLI) that connects to backend over WebSocket and runs games on its own machine, unlocking concurrent games beyond the single-server cap
- [ ] Stream raw engine `info` lines (depth, score, pv) as a new event type
- [ ] Render last engine eval as a small bar next to the board
- [ ] Per-engine resource limit overrides (memory, cpus)
- [ ] Swiss / gauntlet tournament formats
- [ ] Engine ELO based on tournament results
- [ ] Public leaderboard
- [ ] PGN download per game
- [ ] Health check endpoint `GET /health`
- [ ] Test the move loop in `game.py` against a stubbed engine
