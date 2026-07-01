import asyncio
import os
import random
import socket
from uuid import UUID

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from machineplay import credentials, hardware, schemas
from machineplay.config import (
    BACKEND_URL,
    MAX_GAMES,
    RECONNECT_BASE,
    RECONNECT_MAX,
    RECONNECT_RESET_AFTER,
    RUNNER_ID,
    TELEMETRY_INTERVAL,
    make_ssl_context,
)
from machineplay.game import Game

# Connection-level errors that warrant a reconnect attempt (vs. crashing).
_RETRYABLE = (OSError, ConnectionClosed, InvalidHandshake, TimeoutError)


class _Terminated(Exception):
    """Raised when the server sends an explicit 'exit' command (stop for good)."""


def _game_done(
    task: asyncio.Task,
    game_id: UUID,
    games: dict[UUID, "Game"],
    scheduled_commands: asyncio.Queue,
) -> None:
    """Drop a finished game; if its task crashed, still report a game end.

    Without this, an unexpected exception in `Game.play_game` (e.g. docker
    missing from PATH) is swallowed by asyncio and the server's game stays
    'playing' forever.
    """
    games.pop(game_id, None)
    if task.cancelled() or (exc := task.exception()) is None:
        return
    print(f"game {game_id} crashed: {exc!r}")
    scheduled_commands.put_nowait(
        schemas.GameEvent(
            game_id=game_id, event=schemas.GameEndEvent(result="*", pgn=None)
        )
    )


def _load_token() -> str | None:
    """The API token the runner authenticates with. Prefer ``MP_TOKEN`` (set on
    the production runner's systemd unit) over the CLI's saved credentials."""
    env = os.environ.get("MP_TOKEN")
    if env:
        return env
    creds = credentials.load()
    return creds.token if creds else None


async def connect_backend_ws(ssl_ctx, token, on_connected):
    print(f"connecting to {BACKEND_URL}")
    headers = {"Authorization": f"Bearer {token}"}
    async with connect(BACKEND_URL, ssl=ssl_ctx, additional_headers=headers) as ws:
        intro = schemas.Introduction(
            runner_id=RUNNER_ID,
            name=socket.gethostname(),
            max_games=MAX_GAMES,
            hardware=hardware.read_hardware(),
        )
        await ws.send(intro.model_dump_json())
        print("connected")
        on_connected()

        scheduled_commands: asyncio.Queue[schemas.ClientCommand] = asyncio.Queue()
        games: dict[UUID, Game] = {}

        async def receiver():
            while True:
                text = await ws.recv()
                cmd: schemas.ServerCommandType = schemas.server_adapter.validate_json(
                    text
                )

                match cmd:
                    case schemas.StartGame(
                        game_id=game_id, white=white, black=black, tc=tc
                    ):
                        if len(games) >= MAX_GAMES:
                            print(
                                f"refusing start_game {game_id}: at capacity ({len(games)}/{MAX_GAMES})"
                            )
                            await scheduled_commands.put(
                                schemas.GameEvent(
                                    game_id=game_id,
                                    event=schemas.GameEndEvent(result="*", pgn=None),
                                )
                            )
                            continue
                        print(f"start_game {game_id} {white.name} vs {black.name}")
                        game = Game(game_id, white, black, tc, scheduled_commands)
                        games[game_id] = game
                        game.task.add_done_callback(
                            lambda t, gid=game_id: _game_done(
                                t, gid, games, scheduled_commands
                            )
                        )
                    case schemas.StopGame():
                        print("stop_game")
                    case schemas.Terminate():
                        print("exit")
                        raise _Terminated

        async def sender():
            while True:
                cmd = await scheduled_commands.get()
                await ws.send(cmd.model_dump_json())

        async def telemetry():
            # Report CPU/RAM utilization on a timer via the same outgoing queue.
            hardware.prime_cpu_percent()
            while True:
                await asyncio.sleep(TELEMETRY_INTERVAL)
                await scheduled_commands.put(hardware.read_telemetry())

        recv_task = asyncio.create_task(receiver())
        send_task = asyncio.create_task(sender())
        telemetry_task = asyncio.create_task(telemetry())

        try:
            done, _ = await asyncio.wait(
                {recv_task, send_task, telemetry_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                task.result()  # re-raise exceptions
        finally:
            recv_task.cancel()
            send_task.cancel()
            telemetry_task.cancel()
            # Abandon in-flight games so their fastchess subprocesses don't
            # outlive the connection; the server reschedules on reconnect.
            for game in games.values():
                game.task.cancel()


async def run_forever():
    token = _load_token()
    if not token:
        print(
            "no API token found. Run `machineplay login` on this machine, "
            "or set MP_TOKEN in the environment."
        )
        raise SystemExit(1)

    ssl_ctx = make_ssl_context()
    loop = asyncio.get_running_loop()
    delay = RECONNECT_BASE

    while True:
        connected_at: float | None = None

        def on_connected() -> None:
            nonlocal connected_at
            connected_at = loop.time()

        try:
            await connect_backend_ws(ssl_ctx, token, on_connected)
            return  # connect_backend_ws only returns on a clean close
        except _Terminated:
            print("terminated by server")
            return
        except _RETRYABLE as exc:
            print(f"connection lost: {type(exc).__name__}: {exc}")

        # A session that stayed up a while (healthy) resets backoff so a backend
        # hot-reload reconnects fast; a backend that's truly down keeps backing off.
        if (
            connected_at is not None
            and loop.time() - connected_at >= RECONNECT_RESET_AFTER
        ):
            delay = RECONNECT_BASE

        wait = random.uniform(0, delay)
        print(f"reconnecting in {wait:.1f}s")
        await asyncio.sleep(wait)
        delay = min(delay * 2, RECONNECT_MAX)
