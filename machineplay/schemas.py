from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter


class EngineConfig(BaseModel):
    name: str
    # The runner pulls `<registry>/<repository>@<digest>`.
    repository: str
    digest: str


class StartGame(BaseModel):
    cmd: Literal["start_game"] = "start_game"
    game_id: UUID
    white: EngineConfig
    black: EngineConfig
    # "base+inc" in seconds; the default lives in the backend's settings.tc.
    tc: str
    # UCI moves from the standard start position, dealt from a backend book.
    opening: list[str] = Field(default_factory=list)


class StopGame(BaseModel):
    cmd: Literal["stop_game"] = "stop_game"
    game_id: UUID


class Terminate(BaseModel):
    cmd: Literal["exit"] = "exit"


type ServerCommandType = StartGame | StopGame | Terminate
ServerCommand = Annotated[ServerCommandType, Field(discriminator="cmd")]
server_adapter: TypeAdapter[ServerCommandType] = TypeAdapter(ServerCommand)


class GameStatus(StrEnum):
    # Not yet dispatched to a runner; backend-only.
    PENDING = "pending"
    PLAYING = "playing"
    ENDED = "ended"
    ABORTED = "aborted"


class SearchInfo(BaseModel):
    """One engine's search, distilled from `info` lines.

    Scores are from the searching side's point of view, `score_cp` or
    `score_mate` but never both. `pv` is capped.
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
    """One line of the UCI conversation. `sent` is True for what fastchess sent
    the engine; `ply` is the game's ply when it was logged."""

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
    # Parallel to `moves`.
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
    # The mover's last search before `bestmove`.
    analysis: SearchInfo | None = None


class EngineInfoEvent(BaseModel):
    """A search in progress. Throttled and never persisted: the MoveEvent's
    `analysis` supersedes it."""

    type: Literal["engine_info"] = "engine_info"
    side: Literal["white", "black"]
    ply: int
    info: SearchInfo


class UciLogEvent(BaseModel):
    """A batch of UCI transcript lines; the backend keeps only a tail."""

    type: Literal["uci_log"] = "uci_log"
    lines: list[UciLine]


class GameEndEvent(BaseModel):
    type: Literal["game_end"] = "game_end"
    result: str | None
    pgn: str | None = None
    # ABORTED for games cut short; standings count only ENDED.
    status: GameStatus = GameStatus.ENDED
    # "time forfeit", "cancelled", …
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
    """Static hardware a runner reports in its Introduction."""

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
    """Live utilization a runner reports periodically; never persisted."""

    cmd: Literal["telemetry"] = "telemetry"
    cpu_percent: float
    ram_used_bytes: int
    ram_percent: float


type ClientCommandType = Introduction | GameEvent | Telemetry
ClientCommand = Annotated[ClientCommandType, Field(discriminator="cmd")]
client_adapter: TypeAdapter[ClientCommandType] = TypeAdapter(ClientCommand)
