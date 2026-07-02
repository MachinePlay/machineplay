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
    tc: str = "30+0.3"


class StopGame(BaseModel):
    cmd: Literal["stop_game"] = "stop_game"
    game_id: UUID


class Terminate(BaseModel):
    cmd: Literal["exit"] = "exit"


type ServerCommandType = StartGame | StopGame | Terminate
ServerCommand = Annotated[ServerCommandType, Field(discriminator="cmd")]
server_adapter = TypeAdapter(ServerCommand)


class GameStatus(StrEnum):
    PLAYING = "playing"
    ENDED = "ended"
    ABORTED = "aborted"


class FenEvent(BaseModel):
    type: Literal["fen"] = "fen"
    fen: str
    ply: int
    white_name: str | None
    black_name: str | None
    moves: list[str]
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
    FenEvent | GameStartEvent | MoveEvent | GameEndEvent,
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
client_adapter = TypeAdapter(ClientCommand)
