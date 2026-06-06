from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter


class EngineConfig(BaseModel):
    name: str
    command: str


class StartGame(BaseModel):
    cmd: Literal["start_game"] = "start_game"
    game_id: UUID
    white: EngineConfig
    black: EngineConfig
    tc: str = "30+0.3"


class StopGame(BaseModel):
    cmd: Literal["stop_game"] = "stop_game"


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


GameStreamEvent = Annotated[
    FenEvent | GameStartEvent | MoveEvent | GameEndEvent,
    Field(discriminator="type"),
]


class Introduction(BaseModel):
    cmd: Literal["intro"] = "intro"
    runner_id: UUID
    name: str
    max_games: int


class GameEvent(BaseModel):
    cmd: Literal["game_event"] = "game_event"
    game_id: UUID
    event: GameStreamEvent


type ClientCommandType = Introduction | GameEvent
ClientCommand = Annotated[ClientCommandType, Field(discriminator="cmd")]
client_adapter = TypeAdapter(ClientCommand)
