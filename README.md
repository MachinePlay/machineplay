# machineplay

Upload a UCI chess engine as a Docker image and watch it play, live, on
[machineplay.org](https://machineplay.org).

This package is the client side of that, in two roles:

- **the CLI** (`machineplay login` / `upload` / `whoami` / `logout`) — builds
  the Dockerfile in the current directory, smoke-tests that it speaks UCI,
  pushes it to the machineplay registry and registers the new version.
- **the runner** (`machineplay runner`) — a daemon that connects to the backend
  over a WebSocket, pulls engine images and plays games with
  [fastchess](https://github.com/Disservin/fastchess), streaming every move
  back as it happens. Each game runs its two engines in sandboxed containers
  (no network, dropped capabilities, capped memory) pinned to one core.

Every external command either role runs is echoed first, so you can see exactly
what touched your machine:

```
> docker build --platform linux/amd64 -t machineplay-local:latest .
> docker push registry.machineplay.org/alice/myengine:2026-08-08-12-30
```

## Install

```sh
uv tool install machineplay     # or: pipx install machineplay
```

Needs Python 3.12+ and Docker. The runner additionally needs `fastchess` on
`PATH` (or `FASTCHESS_PATH` pointing at it).

## Upload an engine

Your engine is a Docker image whose entrypoint speaks UCI on stdin/stdout —
start from [python-chess-starter](https://github.com/MachinePlay/python-chess-starter)
if you want a working example.

```sh
machineplay login          # opens machineplay.org/cli, paste the token
cd myengine                # the directory with your Dockerfile
machineplay upload
```

`upload` asks for an engine name and a version. The **name** groups versions —
uploading under the same name again adds a version to the same engine, so keep
it stable and don't bake a release number into it. Names are lowercase URL
slugs: your engine ends up at `machineplay.org/<you>/<engine>`.

Runners are `linux/amd64`, so that is what `upload` builds and what it checks
the finished image against — you don't have to do anything special on an Apple
Silicon Mac beyond letting docker emulate, which makes the build and the UCI
check slower. A `FROM --platform=…` line in your Dockerfile overrides this and
is rejected: the image would upload fine and then fail to start on a runner.

## Run a runner

A runner offers your machine's cores to play games on. It needs an API token,
either from `machineplay login` or in `MP_TOKEN`:

```sh
machineplay runner
```

It reports its hardware on connect, plays up to `MAX_GAMES` games concurrently
(default: one per core), and reconnects on its own if the backend restarts. Its
identity persists in `~/.config/machineplay/runner.json`, so restarts show up as
the same runner rather than a new one.

## Files this writes

| Path | What |
| --- | --- |
| `~/.config/machineplay/credentials.json` | API token from `login` (mode 0600) |
| `~/.config/machineplay/runner.json` | this runner's stable id |
| `~/.docker/config.json` | registry credentials, via `docker login` |

`machineplay logout` removes all three (the last one via `docker logout`).

## Configuration

Everything is overridable from the environment; the defaults point at
production.

| Variable | Default | What |
| --- | --- | --- |
| `MP_TOKEN` | — | API token for the runner (overrides saved credentials) |
| `RUNNER_ID` | persisted | pin the runner's id explicitly |
| `BACKEND_URL` | `wss://api.machineplay.org/ws` | runner WebSocket endpoint |
| `MAX_GAMES` | CPU count | concurrent games (each pinned to its own core) |
| `MACHINEPLAY_API_URL` | `https://api.machineplay.org` | REST API for the CLI |
| `MACHINEPLAY_WEB_URL` | `https://machineplay.org` | website `login` opens |
| `MACHINEPLAY_REGISTRY` | `registry.machineplay.org` | image registry |
| `MACHINEPLAY_PLATFORM` | `linux/amd64` | platform `upload` builds engines for |
| `MACHINEPLAY_UCI_TIMEOUT` | 30s, 120s emulated | seconds `upload` waits for `uci` |
| `FASTCHESS_PATH` | `fastchess` | fastchess binary |
| `ENGINE_MEMORY` / `ENGINE_CPUS` | `512m` / `1` | per-engine container limits |
| `PULL_TIMEOUT` | `600` | seconds before a stuck `docker pull` gives up |
| `NO_COLOR` | — | set to disable coloured output |

## License

This project is licensed under the GNU Affero General Public License, version 3
or (at your option) any later version (`AGPL-3.0-or-later`). See [LICENSE](LICENSE)
for the full text.
