import asyncio
import io
import os
import random
import re
import socket
import ssl
import tempfile
from uuid import UUID, uuid4

import certifi
import chess
import chess.pgn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake
from machineplay import schemas

RUNNER_ID = uuid4()
BACKEND_URL = os.environ.get("BACKEND_URL", "wss://api.machineplay.org/ws")
MAX_GAMES = int(os.environ.get("MAX_GAMES") or (os.cpu_count() or 1))
FASTCHESS_PATH = os.environ.get("FASTCHESS_PATH", "fastchess")

# Reconnect backoff (seconds). Full jitter: sleep ~ U(0, delay), delay doubles
# up to RECONNECT_MAX. A session that stayed up at least RECONNECT_RESET_AFTER
# is considered healthy, so its drop (e.g. a backend hot-reload) resets backoff.
RECONNECT_BASE = float(os.environ.get("RECONNECT_BASE", "0.5"))
RECONNECT_MAX = float(os.environ.get("RECONNECT_MAX", "30.0"))
RECONNECT_RESET_AFTER = float(os.environ.get("RECONNECT_RESET_AFTER", "5.0"))

# Connection-level errors that warrant a reconnect attempt (vs. crashing).
_RETRYABLE = (OSError, ConnectionClosed, InvalidHandshake, TimeoutError)


class _Terminated(Exception):
    """Raised when the server sends an explicit 'exit' command (stop for good)."""


# fastchess engine-log format: "<--- go ... wtime X ... btime Y" (sent to engine)
_GO_RE = re.compile(r"<---\s+go\b.*\bwtime\s+(\d+).*\bbtime\s+(\d+)")
# fastchess engine-log format: "---> bestmove MOVE" (reply from engine)
_BESTMOVE_RE = re.compile(r"--->\s+bestmove\s+([a-h][1-8][a-h][1-8][qrbn]?|0000)\b")


def parse_tc(spec: str) -> tuple[float, float]:
    base, _, inc = spec.partition("+")
    return float(base), float(inc) if inc else 0.0


class Game:
    def __init__(
        self,
        game_id: UUID,
        white: schemas.EngineConfig,
        black: schemas.EngineConfig,
        tc: str,
        queue: asyncio.Queue,
    ):
        self.game_id = game_id
        self.white = white
        self.black = black
        self.tc = tc
        self.queue: asyncio.Queue[schemas.ClientCommand] = queue
        self.san_moves: list[str] = []
        self.clocks: dict[chess.Color, float] = {chess.WHITE: 0.0, chess.BLACK: 0.0}
        self.result: str | None = None
        self.status: schemas.GameStatus = schemas.GameStatus.PLAYING
        self.board = chess.Board()
        self.task = asyncio.create_task(self.play_game())

    def snapshot(self) -> schemas.FenEvent:
        return schemas.FenEvent(
            fen=self.board.fen(),
            ply=self.board.ply(),
            white_name=self.white.name,
            black_name=self.black.name,
            moves=list(self.san_moves),
            white_clock=self.clocks[chess.WHITE],
            black_clock=self.clocks[chess.BLACK],
            result=self.result,
            status=self.status,
            game_id=self.game_id,
        )

    async def send_server(self, event: schemas.GameStreamEvent):
        await self.queue.put(schemas.GameEvent(game_id=self.game_id, event=event))

    async def _stream_log(
        self, log_path: str, proc: asyncio.subprocess.Process, inc: float
    ):
        while not os.path.exists(log_path):
            if proc.returncode is not None:
                return
            await asyncio.sleep(0.05)

        go_loop_time: float | None = None
        go_wtime: float | None = None
        go_btime: float | None = None
        loop = asyncio.get_running_loop()

        async def on_line(line: str) -> None:
            nonlocal go_loop_time, go_wtime, go_btime

            if m := _GO_RE.search(line):
                go_loop_time = loop.time()
                go_wtime = int(m.group(1)) / 1000.0
                go_btime = int(m.group(2)) / 1000.0
            elif m := _BESTMOVE_RE.search(line):
                uci = m.group(1)
                if uci == "0000":
                    return
                try:
                    move = chess.Move.from_uci(uci)
                except ValueError:
                    return
                if move not in self.board.legal_moves:
                    return

                side = self.board.turn
                elapsed = (
                    (loop.time() - go_loop_time) if go_loop_time is not None else 0.0
                )
                if side == chess.WHITE:
                    self.clocks[chess.WHITE] = max(
                        0.0, (go_wtime or 0.0) - elapsed + inc
                    )
                    if go_btime is not None:
                        self.clocks[chess.BLACK] = go_btime
                else:
                    self.clocks[chess.BLACK] = max(
                        0.0, (go_btime or 0.0) - elapsed + inc
                    )
                    if go_wtime is not None:
                        self.clocks[chess.WHITE] = go_wtime

                san = self.board.san(move)
                self.board.push(move)
                self.san_moves.append(san)
                go_loop_time = None

                await self.send_server(
                    schemas.MoveEvent(
                        uci=uci,
                        san=san,
                        from_square=uci[:2],
                        to_square=uci[2:4],
                        fen=self.board.fen(),
                        ply=self.board.ply(),
                        white_clock=self.clocks[chess.WHITE],
                        black_clock=self.clocks[chess.BLACK],
                    )
                )

        with open(log_path) as f:
            while True:
                line = f.readline()
                if not line:
                    if proc.returncode is not None:
                        for line in f:
                            await on_line(line)
                        break
                    await asyncio.sleep(0.005)
                    continue
                await on_line(line)

    async def play_game(self):
        base, inc = parse_tc(self.tc)
        # fastchess pre-adds the first increment to each engine's starting clock
        self.clocks = {chess.WHITE: base + inc, chess.BLACK: base + inc}

        await self.send_server(
            schemas.GameStartEvent(
                white_name=self.white.name,
                black_name=self.black.name,
                game_id=self.game_id,
            )
        )
        await self.send_server(self.snapshot())

        pgn_text = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            pgn_path = os.path.join(tmpdir, "game.pgn")
            log_path = os.path.join(tmpdir, "engine.log")

            cmd = [
                FASTCHESS_PATH,
                "-engine",
                f"cmd={self.white.command}",
                "name=White",
                "-engine",
                f"cmd={self.black.command}",
                "name=Black",
                "-each",
                f"tc={self.tc}",
                "option.Ponder=false",
                "-rounds",
                "1",
                "-games",
                "1",
                "-noswap",
                "-pgnout",
                f"file={pgn_path}",
                "notation=san",
                "timeleft=true",
                "append=false",
                "-log",
                f"file={log_path}",
                "level=trace",
                "engine=true",
                "realtime=true",
                "append=false",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            log_task = asyncio.create_task(self._stream_log(log_path, proc, inc))
            try:
                await proc.communicate()
                await log_task
            except asyncio.CancelledError:
                log_task.cancel()
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                raise

            if os.path.exists(pgn_path):
                with open(pgn_path) as f:
                    pgn_text = f.read()
                game_obj = chess.pgn.read_game(io.StringIO(pgn_text))
                self.result = game_obj.headers.get("Result", "*") if game_obj else "*"
            else:
                self.result = "*"

        self.status = schemas.GameStatus.ENDED
        print(f"game ended result={self.result} plies={self.board.ply()}")
        await self.send_server(
            schemas.GameEndEvent(result=self.result, pgn=pgn_text or None)
        )


async def connect_backend_ws(ssl_ctx, on_connected):
    print(f"connecting to {BACKEND_URL}")
    async with connect(BACKEND_URL, ssl=ssl_ctx) as ws:
        intro = schemas.Introduction(
            runner_id=RUNNER_ID, name=socket.gethostname(), max_games=MAX_GAMES
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
                            lambda _t, gid=game_id: games.pop(gid, None)
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

        recv_task = asyncio.create_task(receiver())
        send_task = asyncio.create_task(sender())

        try:
            done, _ = await asyncio.wait(
                {recv_task, send_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                task.result()  # re-raise exceptions
        finally:
            recv_task.cancel()
            send_task.cancel()
            # Abandon in-flight games so their fastchess subprocesses don't
            # outlive the connection; the server reschedules on reconnect.
            for game in games.values():
                game.task.cancel()


async def run_forever():
    ssl_ctx = (
        ssl.create_default_context(cafile=certifi.where())
        if BACKEND_URL.startswith("wss://")
        else None
    )
    loop = asyncio.get_running_loop()
    delay = RECONNECT_BASE

    while True:
        connected_at: float | None = None

        def on_connected() -> None:
            nonlocal connected_at
            connected_at = loop.time()

        try:
            await connect_backend_ws(ssl_ctx, on_connected)
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


def main():
    print("Welcome")
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        print("shutting down")


if __name__ == "__main__":
    main()
