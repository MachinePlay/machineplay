from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter


class EngineConfig(BaseModel):
    name: str
    # Docker image coordinates the runner plays from. `repository` is the
    # registry-relative path (e.g. "alice/myengine"), `digest` pins the exact
    # image ("sha256:…"); the runner forms `<registry>/<repository>@<digest>`.
    repository: str
    digest: str


class StartGame(BaseModel):
    cmd: Literal["start_game"] = "start_game"
    game_id: UUID
    white: EngineConfig
    black: EngineConfig
    # Time control "base+inc" in seconds. Required on the wire: the backend
    # always sets it, so the default lives in one place (backend settings.tc)
    # rather than being duplicated here.
    tc: str


class StopGame(BaseModel):
    cmd: Literal["stop_game"] = "stop_game"
    game_id: UUID


class Terminate(BaseModel):
    cmd: Literal["exit"] = "exit"


type ServerCommandType = StartGame | StopGame | Terminate
ServerCommand = Annotated[ServerCommandType, Field(discriminator="cmd")]
server_adapter: TypeAdapter[ServerCommandType] = TypeAdapter(ServerCommand)


class GameStatus(StrEnum):
    # Created and pinned (engines/versions/tc) but not yet dispatched to a
    # runner: the pre-dispatch state for tournament pairings waiting on a free
    # slot. The runner never emits this — the backend sets it on create and
    # flips it to PLAYING when the game is scheduled onto a runner.
    PENDING = "pending"
    PLAYING = "playing"
    ENDED = "ended"
    ABORTED = "aborted"


class SearchInfo(BaseModel):
    """What one engine reported about its search, distilled from `info` lines.

    Scores are UCI's own: from the searching side's point of view, in
    centipawns (`score_cp`) or moves-to-mate (`score_mate`), never both. `pv`
    is the principal variation in UCI move notation, capped so a chatty engine
    can't push arbitrarily large events.
    """

    depth: int | None = None
    seldepth: int | None = None
    score_cp: int | None = None
    score_mate: int | None = None
    nodes: int | None = None
    nps: int | None = None
    time_ms: int | None = None
    pv: list[str] = Field(default_factory=list)


class UciLine(BaseModel):
    """One line of the UCI conversation, for the game page's debug view.

    `sent` is True for what the GUI sent the engine (fastchess's `<---`) and
    False for the engine's own output (`--->`). `ply` is the game's ply when
    the line was logged, so the view can be read alongside the moves.
    """

    side: Literal["white", "black"]
    sent: bool
    text: str
    ply: int


class FenEvent(BaseModel):
    type: Literal["fen"] = "fen"
    fen: str
    ply: int
    white_name: str | None
    black_name: str | None
    moves: list[str]
    # Per-move search summaries, parallel to `moves` (None where the engine
    # said nothing scoreable).
    evals: list[SearchInfo | None] = Field(default_factory=list)
    white_clock: float
    black_clock: float
    result: str | None
    status: GameStatus
    game_id: UUID | None


class GameStartEvent(BaseModel):
    type: Literal["game_start"] = "game_start"
    white_name: str | None
    black_name: str | None
    game_id: UUID | None


class MoveEvent(BaseModel):
    type: Literal["move"] = "move"
    uci: str
    san: str
    from_square: str
    to_square: str
    fen: str
    ply: int
    white_clock: float
    black_clock: float
    # The mover's last search summary, from the `info` lines that preceded its
    # `bestmove`. None when the engine reported nothing usable.
    analysis: SearchInfo | None = None


class EngineInfoEvent(BaseModel):
    """An engine's search-in-progress, sent while it is still thinking.

    Throttled by the runner (a new depth, or every few hundred ms) and never
    persisted: it is superseded by the MoveEvent's `analysis` as soon as the
    engine moves.
    """

    type: Literal["engine_info"] = "engine_info"
    side: Literal["white", "black"]
    ply: int
    info: SearchInfo


class UciLogEvent(BaseModel):
    """A batch of UCI transcript lines. Live-only; the backend keeps a bounded
    tail so a page opened mid-game (or after it ends) still has context."""

    type: Literal["uci_log"] = "uci_log"
    lines: list[UciLine]


class GameEndEvent(BaseModel):
    type: Literal["game_end"] = "game_end"
    result: str | None
    pgn: str | None = None
    # Terminal status: ENDED for games that ran to a result, ABORTED for games
    # cut short (crash, cancel, disconnect, wallclock kill). Standings should
    # only count ENDED games.
    status: GameStatus = GameStatus.ENDED
    # Human-readable termination detail ("time forfeit", "cancelled", …).
    reason: str | None = None


GameStreamEvent = Annotated[
    FenEvent
    | GameStartEvent
    | MoveEvent
    | EngineInfoEvent
    | UciLogEvent
    | GameEndEvent,
    Field(discriminator="type"),
]


class HardwareInfo(BaseModel):
    """Static hardware description a runner reports once, in its Introduction.

    Persisted on the backend's Runner doc so it shows even while offline. GPU
    fields can be added here later as a purely additive change.
    """

    cpu_model: str
    cpu_physical_cores: int
    cpu_logical_cores: int
    ram_total_bytes: int


class Introduction(BaseModel):
    cmd: Literal["intro"] = "intro"
    runner_id: UUID
    name: str
    max_games: int
    hardware: HardwareInfo


class GameEvent(BaseModel):
    cmd: Literal["game_event"] = "game_event"
    game_id: UUID
    event: GameStreamEvent


class Telemetry(BaseModel):
    """Live resource utilization a runner reports periodically while connected.

    Kept in memory on the backend (meaningful only while online) and fanned out
    over the runner SSE stream; not persisted.
    """

    cmd: Literal["telemetry"] = "telemetry"
    cpu_percent: float
    ram_used_bytes: int
    ram_percent: float


type ClientCommandType = Introduction | GameEvent | Telemetry
ClientCommand = Annotated[ClientCommandType, Field(discriminator="cmd")]
client_adapter: TypeAdapter[ClientCommandType] = TypeAdapter(ClientCommand)
