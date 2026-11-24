import asyncio
import io
import os
import re
import tempfile
from dataclasses import dataclass
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

# "[Engine] [14:58:25.264] <tid>  White <--- go wtime 3100 …"; "<---" is what
# fastchess sent the engine, "--->" the engine's output.
_LINE_RE = re.compile(r"\s(White|Black)\s(<---|--->)\s(.*?)\s*$")
_GO_RE = re.compile(r"^go\b.*\bwtime\s+(\d+).*\bbtime\s+(\d+)")
_BESTMOVE_RE = re.compile(r"^bestmove\s+([a-h][1-8][a-h][1-8][qrbn]?|0000)\b")

# `info` fields worth keeping, mapped to their SearchInfo attribute.
_INFO_INTS = {
    "depth": "depth",
    "seldepth": "seldepth",
    "nodes": "nodes",
    "nps": "nps",
    "time": "time_ms",
}
# Caps on what one search may push to the browser.
PV_LIMIT = 16
LOG_LINES_PER_SEARCH = 150
# A search reports on each new depth or every INFO_INTERVAL, but never faster
# than INFO_MIN_GAP: each event re-renders every watching browser.
INFO_INTERVAL = 0.3
INFO_MIN_GAP = 0.15
# An engine that sees mate re-reports it at every depth within milliseconds.
LOG_INFO_INTERVAL = 0.05
# UCI lines are flushed at this many, or after this long.
LOG_BATCH = 24
LOG_INTERVAL = 0.3


def parse_info(payload: str) -> schemas.SearchInfo | None:
    """None for lines that say nothing about the search ("info string …",
    extra `multipv` lines)."""
    tokens = payload.split()
    if len(tokens) < 2 or tokens[0] != "info":
        return None
    info = schemas.SearchInfo()
    useful = False
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "pv":
            info.pv = tokens[i + 1 : i + 1 + PV_LIMIT]
            useful = useful or bool(info.pv)
            break
        if token == "multipv" and i + 1 < len(tokens) and tokens[i + 1] != "1":
            return None  # only the principal variation is shown
        if token == "score" and i + 2 < len(tokens):
            kind, raw = tokens[i + 1], tokens[i + 2]
            try:
                value = int(raw)
            except ValueError:
                i += 1
                continue
            if kind == "cp":
                info.score_cp = value
                useful = True
            elif kind == "mate":
                info.score_mate = value
                useful = True
            i += 3
            continue
        if (field := _INFO_INTS.get(token)) and i + 1 < len(tokens):
            try:
                setattr(info, field, int(tokens[i + 1]))
            except ValueError:
                pass
            else:
                useful = True
            i += 2
            continue
        i += 1
    return info if useful else None


def merge_info(
    old: schemas.SearchInfo | None, new: schemas.SearchInfo
) -> schemas.SearchInfo:
    """Fold one `info` line into the search so far. Lines are partial, so
    fields carry over until the engine says otherwise, as in a GUI."""
    if old is None:
        return new
    merged = old.model_copy()
    fields = new.model_dump(exclude_none=True)
    if not new.pv:
        fields.pop("pv", None)
    if new.score_cp is not None:
        merged.score_mate = None
    if new.score_mate is not None:
        merged.score_cp = None
    for name, value in fields.items():
        setattr(merged, name, value)
    return merged


@dataclass
class _SideState:
    """One engine's per-search state, so a chatty engine is thinned rather than
    relayed. Replaced on each `go`."""

    info: schemas.SearchInfo | None = None
    sent_at: float = 0.0
    sent_depth: int | None = None
    logged_at: float = 0.0
    logged_depth: int | None = None
    logged_lines: int = 0


def parse_tc(spec: str) -> tuple[float, float]:
    base, _, inc = spec.partition("+")
    return float(base), float(inc) if inc else 0.0


def docker_run_args(ref: str, name: str, cpu: int) -> str:
    """fastchess `args=` running the engine as a sandboxed container.

    Named so an aborted game can force-remove it. Both engines share one core:
    with Ponder off only the side to move computes.
    """
    return (
        f"run --rm -i --network none --name {name} "
        f"--cpuset-cpus {cpu} --memory {ENGINE_MEMORY} --cpus {ENGINE_CPUS} "
        f"--cap-drop ALL --security-opt no-new-privileges {ref}"
    )


def _tail(output: bytes | str, lines: int = 10) -> str:
    text = output.decode(errors="replace") if isinstance(output, bytes) else output
    return "\n".join(text.strip().splitlines()[-lines:])


# An allowlist: every run also prints harmless warnings.
_FAULT_RE = re.compile(
    r"^(?:Warning[;:]\s*)?(.*(?:is not responsive|stalled / disconnected"
    r"|disconnects|failed to start|illegal move).*)$",
    re.IGNORECASE,
)


# The UCI transcript and flagged lines; a plain tail would be all bookkeeping.
_TRACE_RE = re.compile(r"--->|<---|\[WARN|\[ERROR")


def _log_excerpt(path: str, lines: int = 20) -> str:
    with open(path) as f:
        kept = [line for line in f if _TRACE_RE.search(line)]
    return "".join(kept[-lines:]).strip()


def _failure_reason(stdout: bytes) -> str | None:
    """The first engine fault fastchess printed on stdout; later ones are fallout."""
    for line in stdout.decode(errors="replace").splitlines():
        if m := _FAULT_RE.match(line.strip()):
            return m.group(1).strip()
    return None


def termination(game_obj: chess.pgn.Game) -> str | None:
    """How the game ended. The `Termination` header says "normal" for every
    finish; the real reason is the final move comment's last field:

        Ke6 {0.00/245 0.003s, tl=1.954s, Draw by 3-fold repetition} 1/2-1/2
    """
    field = game_obj.end().comment.rsplit(",", 1)[-1].strip()
    if field[:1].isalpha() and not field.startswith("tl="):
        return field[0].lower() + field[1:]
    header = game_obj.headers.get("Termination")
    return None if header == "normal" else header


def opening_pgn(moves: list[str]) -> str:
    """A book line as a one-game PGN for `-openings`. PGN, not EPD, so fastchess
    sends `position startpos moves …`, which early starter engines handle.

    Raises ValueError on an illegal move.
    """
    board = chess.Board()
    for uci in moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"illegal opening move {uci!r} at ply {board.ply()}")
        board.push(move)
    return str(chess.pgn.Game.from_board(board))


async def docker_pull(ref: str, logger: log.Log = log.root) -> str | None:
    """None on success, or docker's output on failure."""
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
        opening: list[str] | None = None,
    ):
        self.game_id = game_id
        self.white = white
        self.black = black
        self.tc = tc
        self.slot = slot
        self.opening = opening or []
        self.log = log.Log(f"game {log.short(game_id)}")
        self.queue: asyncio.Queue[schemas.ClientCommand] = queue
        self.san_moves: list[str] = []
        # Parallel to san_moves: what the mover's search reported, or None.
        self.evals: list[schemas.SearchInfo | None] = []
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
            evals=list(self.evals),
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
        sides = {chess.WHITE: _SideState(), chess.BLACK: _SideState()}
        pending: list[schemas.UciLine] = []
        flushed_at = loop.time()

        async def flush_log(force: bool = False) -> None:
            nonlocal flushed_at
            now = loop.time()
            if not pending:
                flushed_at = now
                return
            if (
                not force
                and len(pending) < LOG_BATCH
                and now - flushed_at < LOG_INTERVAL
            ):
                return
            await self.send_server(schemas.UciLogEvent(lines=list(pending)))
            pending.clear()
            flushed_at = now

        def record(side: chess.Color, sent: bool, text: str) -> None:
            """Queue a transcript line, capped per search."""
            state = sides[side]
            if state.logged_lines > LOG_LINES_PER_SEARCH:
                return
            state.logged_lines += 1
            if state.logged_lines > LOG_LINES_PER_SEARCH:
                text = "… further output dropped"
            pending.append(
                schemas.UciLine(
                    side="white" if side == chess.WHITE else "black",
                    sent=sent,
                    text=text,
                    ply=self.board.ply(),
                )
            )

        async def on_line(line: str) -> None:
            nonlocal go_loop_time, go_wtime, go_btime

            parsed = _LINE_RE.search(line)
            if parsed is None:
                return
            name, arrow, payload = parsed.groups()
            side = chess.WHITE if name == "White" else chess.BLACK
            state = sides[side]

            if arrow == "<---":
                if m := _GO_RE.match(payload):
                    go_loop_time = loop.time()
                    go_wtime = int(m.group(1)) / 1000.0
                    go_btime = int(m.group(2)) / 1000.0
                    sides[side] = _SideState()
                record(side, True, payload)
                return

            if payload.startswith("info"):
                info = parse_info(payload)
                if info is None:
                    record(side, False, payload)
                    return
                info = merge_info(state.info, info)
                state.info = info
                now = loop.time()
                # One transcript line per depth, rate-limited.
                if (
                    info.depth != state.logged_depth
                    and now - state.logged_at >= LOG_INFO_INTERVAL
                ):
                    state.logged_depth = info.depth
                    state.logged_at = now
                    record(side, False, payload)
                due = (
                    info.depth != state.sent_depth
                    or now - state.sent_at >= INFO_INTERVAL
                )
                if due and now - state.sent_at >= INFO_MIN_GAP:
                    state.sent_depth = info.depth
                    state.sent_at = now
                    await self.send_server(
                        schemas.EngineInfoEvent(
                            side="white" if side == chess.WHITE else "black",
                            ply=self.board.ply(),
                            info=info,
                        )
                    )
                return

            if not (m := _BESTMOVE_RE.match(payload)):
                record(side, False, payload)
                return

            record(side, False, payload)
            uci = m.group(1)
            if uci == "0000":
                return
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                return
            if move not in self.board.legal_moves:
                return

            mover = self.board.turn
            elapsed = (loop.time() - go_loop_time) if go_loop_time is not None else 0.0
            if mover == chess.WHITE:
                self.clocks[chess.WHITE] = max(0.0, (go_wtime or 0.0) - elapsed + inc)
                if go_btime is not None:
                    self.clocks[chess.BLACK] = go_btime
            else:
                self.clocks[chess.BLACK] = max(0.0, (go_btime or 0.0) - elapsed + inc)
                if go_wtime is not None:
                    self.clocks[chess.WHITE] = go_wtime

            san = self.board.san(move)
            self.board.push(move)
            self.san_moves.append(san)
            self.evals.append(state.info)
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
                    analysis=state.info,
                )
            )
            await flush_log(force=True)

        with open(log_path) as f:
            while True:
                line = f.readline()
                if not line:
                    if proc.returncode is not None:
                        for line in f:
                            await on_line(line)
                        await flush_log(force=True)
                        break
                    await flush_log()
                    await asyncio.sleep(0.005)
                    continue
                await on_line(line)
                await flush_log()

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

        # fastchess plays the opening without reporting it, so replay it here.
        try:
            book_pgn = opening_pgn(self.opening) if self.opening else None
        except ValueError as exc:
            self.log.error(f"bad opening line: {exc}")
            self.status = schemas.GameStatus.ABORTED
            self.result = "*"
            await self.send_server(
                schemas.GameEndEvent(
                    result="*",
                    pgn=None,
                    status=schemas.GameStatus.ABORTED,
                    reason="invalid opening line",
                )
            )
            return
        for uci in self.opening:
            move = chess.Move.from_uci(uci)
            self.san_moves.append(self.board.san(move))
            self.board.push(move)
            self.evals.append(None)

        await self.send_server(self.snapshot())

        white_ref = pull_ref(self.white.repository, self.white.digest)
        black_ref = pull_ref(self.black.repository, self.black.digest)

        # Up front, so a slow pull can't trip fastchess's startup timeout.
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
            # A one-line book: `-rounds 1 -games 1` plays exactly that line.
            opening_args: list[str] = []
            if book_pgn is not None:
                book_path = os.path.join(tmpdir, "opening.pgn")
                with open(book_path, "w") as f:
                    f.write(book_pgn)
                opening_args = ["-openings", f"file={book_path}", "format=pgn"]
            cpu = AVAILABLE_CPUS[self.slot % len(AVAILABLE_CPUS)]
            self.log.info(
                f"{self.white.name} (white) vs {self.black.name} (black), "
                f"tc={self.tc}, slot={self.slot} on core {cpu}"
                + (f", opening {' '.join(self.san_moves)}" if self.opening else "")
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
                *opening_args,
                "-pgnout",
                f"file={pgn_path}",
                "notation=san",
                "timeleft=true",
                "append=false",
                # fastchess writes its config on every run; keep it in tmpdir.
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
                # Killing fastchess orphans its `docker run` containers.
                await docker_rm(white_container, self.log)
                await docker_rm(black_container, self.log)

            # Against hung containers: 300 moves a side plus startup slack.
            wallclock = 2 * (base + 300 * inc) + 120
            try:
                output, errors = await asyncio.wait_for(
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

            fault: str | None = None
            if fastchess.returncode != 0:
                fault = _failure_reason(output) or _tail(errors) or "no output"
                self.log.error(f"fastchess exited {fastchess.returncode}: {fault}")
                # Only the trace log says what the engine got wrong, and tmpdir
                # is about to take it away.
                if os.path.exists(log_path):
                    self.log.error("engine log tail:\n" + _log_excerpt(log_path))

            reason: str | None = None
            if os.path.exists(pgn_path):
                with open(pgn_path) as f:
                    pgn_text = f.read()
            # fastchess writes an empty PGN when it aborts before the first move.
            game_obj = (
                chess.pgn.read_game(io.StringIO(pgn_text)) if pgn_text.strip() else None
            )
            if game_obj is not None:
                self.result = game_obj.headers.get("Result", "*")
                reason = termination(game_obj)
                self.status = schemas.GameStatus.ENDED
            else:
                # An engine fault, not a result: abort, with fastchess's diagnosis.
                self.log.warn("fastchess produced no usable PGN")
                self.result = "*"
                pgn_text = ""
                reason = fault or "engine did not finish the game"
                self.status = schemas.GameStatus.ABORTED

        self.log.info(
            f"{self.status} result={self.result} plies={self.board.ply()}"
            + (f" ({reason})" if reason else "")
        )
        await self.send_server(
            schemas.GameEndEvent(
                result=self.result,
                pgn=pgn_text or None,
                status=self.status,
                reason=reason,
            )
        )
