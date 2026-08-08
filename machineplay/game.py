import asyncio
import io
import os
import re
import tempfile
from uuid import UUID

import chess
import chess.pgn

from machineplay import log, proc, schemas
from machineplay.config import (
    AVAILABLE_CPUS,
    ENGINE_CPUS,
    ENGINE_MEMORY,
    FASTCHESS_PATH,
    PULL_TIMEOUT,
    pull_ref,
)

# fastchess engine-log format: "<--- go ... wtime X ... btime Y" (sent to engine)
_GO_RE = re.compile(r"<---\s+go\b.*\bwtime\s+(\d+).*\bbtime\s+(\d+)")
# fastchess engine-log format: "---> bestmove MOVE" (reply from engine)
_BESTMOVE_RE = re.compile(r"--->\s+bestmove\s+([a-h][1-8][a-h][1-8][qrbn]?|0000)\b")


def parse_tc(spec: str) -> tuple[float, float]:
    base, _, inc = spec.partition("+")
    return float(base), float(inc) if inc else 0.0


def docker_run_args(ref: str, name: str, cpu: int) -> str:
    """fastchess `args=` value that runs the engine image as a sandboxed container.

    The container speaks UCI over stdio (`-i`), has no network, drops all
    capabilities, and is capped on memory/cpu. It's given an explicit `--name`
    so an aborted game can force-remove it (killing fastchess would otherwise
    orphan the container). fastchess launches `docker` with these args (one
    container per engine) and pipes the UCI protocol through.

    Both of a game's containers are pinned to the same dedicated core (`cpu`,
    from the game's slot): with Ponder off only the side to move computes, so
    the thinking engine gets the whole core and concurrent games can't contend
    on wall-clock time controls.
    """
    return (
        f"run --rm -i --network none --name {name} "
        f"--cpuset-cpus {cpu} --memory {ENGINE_MEMORY} --cpus {ENGINE_CPUS} "
        f"--cap-drop ALL --security-opt no-new-privileges {ref}"
    )


def _tail(output: bytes | str, lines: int = 10) -> str:
    """Last few lines of a child's output, for one-line-ish error reporting."""
    text = output.decode(errors="replace") if isinstance(output, bytes) else output
    return "\n".join(text.strip().splitlines()[-lines:])


async def docker_pull(ref: str, logger: log.Log = log.root) -> str | None:
    """Pull an image. Returns None on success, or docker's output on failure.

    Capped at PULL_TIMEOUT so a wedged pull can't hold a game slot forever.
    """
    child = await proc.start(
        ["docker", "pull", ref],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        logger=logger,
    )
    try:
        out, _ = await asyncio.wait_for(child.communicate(), timeout=PULL_TIMEOUT)
    except TimeoutError:
        child.kill()
        await child.wait()
        return f"pull timed out after {PULL_TIMEOUT:.0f}s"
    if child.returncode != 0:
        return _tail(out)
    return None


async def docker_rm(name: str, logger: log.Log = log.root) -> None:
    """Best-effort force-remove of a (possibly already-gone) container."""
    child = await proc.start(
        ["docker", "rm", "-f", name],
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        logger=logger,
    )
    await child.wait()


class Game:
    def __init__(
        self,
        game_id: UUID,
        white: schemas.EngineConfig,
        black: schemas.EngineConfig,
        tc: str,
        queue: asyncio.Queue[schemas.ClientCommand],
        slot: int,
    ):
        self.game_id = game_id
        self.white = white
        self.black = black
        self.tc = tc
        self.slot = slot
        # Games run concurrently and their lines interleave in the runner's
        # log; label every one of them with the game's short id.
        self.log = log.Log(f"game {log.short(game_id)}")
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

    async def send_server(self, event: schemas.GameStreamEvent) -> None:
        await self.queue.put(schemas.GameEvent(game_id=self.game_id, event=event))

    async def _stream_log(
        self, log_path: str, proc: asyncio.subprocess.Process, inc: float
    ) -> None:
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

    async def play_game(self) -> None:
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

        white_ref = pull_ref(self.white.repository, self.white.digest)
        black_ref = pull_ref(self.black.repository, self.black.digest)

        # Pull both images up front so a slow first pull can't trip fastchess's
        # engine-startup timeout, and pull failures surface as a clean game end.
        for ref in dict.fromkeys((white_ref, black_ref)):
            if (err := await docker_pull(ref, self.log)) is not None:
                self.log.error(f"pull failed for {ref}: {err}")
                self.status = schemas.GameStatus.ABORTED
                self.result = "*"
                await self.send_server(
                    schemas.GameEndEvent(
                        result="*",
                        pgn=None,
                        status=schemas.GameStatus.ABORTED,
                        reason="image pull failed",
                    )
                )
                return

        pgn_text = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            pgn_path = os.path.join(tmpdir, "game.pgn")
            log_path = os.path.join(tmpdir, "engine.log")

            white_container = f"mp-{self.game_id}-w"
            black_container = f"mp-{self.game_id}-b"
            cpu = AVAILABLE_CPUS[self.slot % len(AVAILABLE_CPUS)]
            self.log.info(
                f"{self.white.name} (white) vs {self.black.name} (black), "
                f"tc={self.tc}, slot={self.slot} on core {cpu}"
            )

            cmd = [
                FASTCHESS_PATH,
                "-engine",
                "cmd=docker",
                f"args={docker_run_args(white_ref, white_container, cpu)}",
                "name=White",
                "-engine",
                "cmd=docker",
                f"args={docker_run_args(black_ref, black_container, cpu)}",
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
                # fastchess dumps the resolved tournament config next to
                # itself on every run; keep that inside the game's temp dir
                # instead of littering the runner's working directory.
                "-config",
                f"outname={os.path.join(tmpdir, 'fastchess-config.json')}",
                "-log",
                f"file={log_path}",
                "level=trace",
                "engine=true",
                "realtime=true",
                "append=false",
            ]

            fastchess = await proc.start(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                logger=self.log,
            )

            log_task = asyncio.create_task(self._stream_log(log_path, fastchess, inc))

            async def _kill_fastchess() -> None:
                log_task.cancel()
                if fastchess.returncode is None:
                    fastchess.kill()
                    await fastchess.wait()
                # Killing fastchess orphans its `docker run` children, so the
                # containers keep running; force-remove them explicitly.
                await docker_rm(white_container, self.log)
                await docker_rm(black_container, self.log)

            # Safety net: fastchess normally ends the game on its own (time
            # forfeit etc.), but a hung container/pipe would hold this slot
            # forever. Cap the whole game at what the clocks could plausibly
            # use (300 moves/side) plus generous startup slack.
            wallclock = 2 * (base + 300 * inc) + 120
            try:
                _, errors = await asyncio.wait_for(
                    fastchess.communicate(), timeout=wallclock
                )
                await log_task
            except TimeoutError:
                self.log.error(f"exceeded wallclock ({wallclock:.0f}s)")
                await _kill_fastchess()
                self.status = schemas.GameStatus.ABORTED
                self.result = "*"
                await self.send_server(
                    schemas.GameEndEvent(
                        result="*",
                        pgn=None,
                        status=schemas.GameStatus.ABORTED,
                        reason="wallclock timeout",
                    )
                )
                return
            except asyncio.CancelledError:
                await _kill_fastchess()
                raise

            # A non-zero exit means fastchess itself gave up (bad engine args,
            # an engine that never answered `uci`, …). Its stderr is the only
            # place that says why, so don't swallow it.
            if fastchess.returncode != 0:
                self.log.error(
                    f"fastchess exited {fastchess.returncode}: "
                    f"{_tail(errors) or 'no stderr output'}"
                )

            reason: str | None = None
            if os.path.exists(pgn_path):
                with open(pgn_path) as f:
                    pgn_text = f.read()
                game_obj = chess.pgn.read_game(io.StringIO(pgn_text))
                self.result = game_obj.headers.get("Result", "*") if game_obj else "*"
                # fastchess records how the game ended ("time forfeit", …).
                if game_obj is not None:
                    reason = game_obj.headers.get("Termination")
            else:
                self.log.warn("fastchess wrote no PGN — recording an unfinished game")
                self.result = "*"

        self.status = schemas.GameStatus.ENDED
        self.log.info(
            f"ended result={self.result} plies={self.board.ply()}"
            + (f" ({reason})" if reason else "")
        )
        await self.send_server(
            schemas.GameEndEvent(
                result=self.result, pgn=pgn_text or None, reason=reason
            )
        )
