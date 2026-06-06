from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from beanie import Document, Indexed
from pydantic import Field

from machineplay.schemas import GameStatus


class UUIDDocument(Document):
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]


class Engine(UUIDDocument):
    name: str
    command: str
    description: str = ""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(UUIDDocument):
    github_id: Annotated[int, Indexed(unique=True)]
    login: str
    name: str | None = None
    avatar_url: str = ""
    is_admin: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Game(UUIDDocument):
    white_id: UUID
    black_id: UUID
    white_name: str
    black_name: str
    status: GameStatus = GameStatus.PLAYING
    result: str | None = None
    moves: list[str] = Field(default_factory=list)
    fen: str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    pgn: str | None = None
    white_clock: float = 0.0
    black_clock: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None

    class Settings:
        indexes = ["created_at"]
