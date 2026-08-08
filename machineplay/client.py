import asyncio
import os
import random
import socket
import ssl
from collections.abc import Callable
from functools import partial
from uuid import UUID

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from machineplay import credentials, hardware, log, proc, schemas
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


def _abort_event(game_id: UUID, reason: str) -> schemas.GameEvent:
    return schemas.GameEvent(
        game_id=game_id,
        event=schemas.GameEndEvent(
            result="*",
            pgn=None,
            status=schemas.GameStatus.ABORTED,
            reason=reason,
        ),
    )


def _game_done(
    task: asyncio.Task[None],
    game_id: UUID,
    games: dict[UUID, "Game"],
    free_slots: set[int],
    scheduled_commands: asyncio.Queue[schemas.ClientCommand],
) -> None:
    """Drop a finished game; if its task was cancelled or crashed, still report
    a game end.

    Without this, an unexpected exception in `Game.play_game` (e.g. docker
    missing from PATH) is swallowed by asyncio and the server's game stays
    'playing' forever. (On connection teardown the events land on the dying
    session's queue and go nowhere, which is fine — the backend aborts that
    session's games itself.)
    """
    if (game := games.pop(game_id, None)) is not None:
        free_slots.add(game.slot)
    if task.cancelled():
        scheduled_commands.put_nowait(_abort_event(game_id, "stopped on runner"))
        return
    if (exc := task.exception()) is None:
        return
    log.error(f"game {log.short(game_id)} crashed: {exc!r}")
    # A missing dependency already carries a precise message ("`fastchess` is
    # not installed …"); anything else is a bug, so name its type.
    reason = (
        str(exc)
        if isinstance(exc, proc.CommandNotFound)
        else f"runner error: {type(exc).__name__}"
    )
    scheduled_commands.put_nowait(_abort_event(game_id, reason))


def _load_token() -> str | None:
    """The API token the runner authenticates with. Prefer ``MP_TOKEN`` (set on
    the production runner's systemd unit) over the CLI's saved credentials."""
    env = os.environ.get("MP_TOKEN")
    if env:
        return env
    creds = credentials.load()
    return creds.token if creds else None


async def connect_backend_ws(
    ssl_ctx: ssl.SSLContext | None,
    token: str,
    on_connected: Callable[[], None],
) -> None:
    log.info(f"connecting to {BACKEND_URL}")
    headers = {"Authorization": f"Bearer {token}"}
    async with connect(BACKEND_URL, ssl=ssl_ctx, additional_headers=headers) as ws:
        intro = schemas.Introduction(
            runner_id=RUNNER_ID,
            name=socket.gethostname(),
            max_games=MAX_GAMES,
            hardware=hardware.read_hardware(),
        )
        await ws.send(intro.model_dump_json())
        log.info(f"connected as runner '{intro.name}' ({RUNNER_ID})")
        on_connected()

        scheduled_commands: asyncio.Queue[schemas.ClientCommand] = asyncio.Queue()
        games: dict[UUID, Game] = {}
        # Game slots double as core assignments: slot i pins its game's
        # containers to AVAILABLE_CPUS[i mod n] (see game.docker_run_args).
        free_slots: set[int] = set(range(MAX_GAMES))

        async def receiver() -> None:
            while True:
                text = await ws.recv()
                cmd: schemas.ServerCommandType = schemas.server_adapter.validate_json(
                    text
                )

                match cmd:
                    case schemas.StartGame(
                        game_id=game_id, white=white, black=black, tc=tc
                    ):
                        if not free_slots:
                            log.warn(
                                f"refusing game {log.short(game_id)}: at capacity "
                                f"({len(games)}/{MAX_GAMES} games)"
                            )
                            await scheduled_commands.put(
                                _abort_event(game_id, "runner at capacity")
                            )
                            continue
                        slot = min(free_slots)
                        free_slots.remove(slot)
                        game = Game(game_id, white, black, tc, scheduled_commands, slot)
                        games[game_id] = game
                        game.task.add_done_callback(
                            partial(
                                _game_done,
                                game_id=game_id,
                                games=games,
                                free_slots=free_slots,
                                scheduled_commands=scheduled_commands,
                            )
                        )
                    case schemas.StopGame(game_id=game_id):
                        running = games.get(game_id)
                        if running is None:
                            log.warn(
                                f"stop game {log.short(game_id)}: not running here"
                            )
                        else:
                            running.log.info("stopping (asked by the backend)")
                            # Cancellation kills fastchess + containers; the
                            # done-callback reports the aborted game end.
                            running.task.cancel()
                    case schemas.Terminate():
                        log.info("backend asked this runner to exit")
                        raise _Terminated

        async def sender() -> None:
            while True:
                cmd = await scheduled_commands.get()
                await ws.send(cmd.model_dump_json())

        async def telemetry() -> None:
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
            # outlive the connection; the backend marks them aborted when it
            # notices the disconnect.
            if games:
                log.warn(f"dropping {len(games)} in-flight game(s) with the connection")
            for game in games.values():
                game.task.cancel()


async def run_forever() -> None:
    token = _load_token()
    if not token:
        log.die(
            "no API token found.",
            "run `machineplay login` on this machine",
            "or set MP_TOKEN in the environment",
        )

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
            log.info("terminated by the backend")
            return
        except InvalidStatus as exc:
            # A rejected token is not going to start working on retry, so stop
            # instead of hammering the backend with a bad credential forever.
            if exc.response.status_code in (401, 403):
                log.die(
                    f"the backend rejected this runner's API token "
                    f"(HTTP {exc.response.status_code}).",
                    "run `machineplay login` again, or fix MP_TOKEN",
                )
            log.error(f"handshake failed: HTTP {exc.response.status_code}")
        except _RETRYABLE as exc:
            log.error(f"connection lost: {type(exc).__name__}: {exc}")

        # A session that stayed up a while (healthy) resets backoff so a backend
        # hot-reload reconnects fast; a backend that's truly down keeps backing off.
        if (
            connected_at is not None
            and loop.time() - connected_at >= RECONNECT_RESET_AFTER
        ):
            delay = RECONNECT_BASE

        wait = random.uniform(0, delay)
        log.info(f"reconnecting in {wait:.1f}s")
        await asyncio.sleep(wait)
        delay = min(delay * 2, RECONNECT_MAX)
