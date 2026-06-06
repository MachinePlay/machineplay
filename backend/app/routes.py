import asyncio
import logging
from collections.abc import AsyncIterable
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.sse import EventSourceResponse

from machineplay import schemas

from app import streaming
from app.auth import require_user
from app.config import settings
from app.exceptions import NotFoundError, RunnerBusyError
from app.models import Engine, Game, User
from app.schemas import (
    EngineOut,
    GameOut,
    LiveStreamEvent,
    RunnerOut,
    StartGameRequest,
    StartGameResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/engine", response_model=list[EngineOut])
async def list_engines() -> list[Engine]:
    return await Engine.find_all().to_list()


@router.get("/runners", response_model=list[RunnerOut])
async def list_runners() -> list[streaming.Runner]:
    return streaming.runners.list_runners()


@router.post("/game")
async def start_game(
    payload: StartGameRequest, user: User = Depends(require_user)
) -> StartGameResponse:
    logger.info("start_game requested by user=%s", user.login)
    white = await Engine.get(payload.white_engine_id)
    black = await Engine.get(payload.black_engine_id)
    if white is None or black is None:
        raise NotFoundError("engine not found")

    runner = streaming.runners.get_runner(payload.runner_id)

    if runner.is_full():
        raise RunnerBusyError(
            details={
                "runner_id": str(runner.runner_id),
                "active_games": runner.active_games,
                "max_games": runner.max_games,
            }
        )

    doc = Game(
        white_id=white.id,
        black_id=black.id,
        white_name=white.name,
        black_name=black.name,
    )
    await doc.insert()

    streaming.game_registry.register_game(doc.id)
    runner.track_game(doc.id)

    await runner.scheduled_commands.put(
        schemas.StartGame(
            game_id=doc.id,
            white=schemas.EngineConfig(name=white.name, command=white.command),
            black=schemas.EngineConfig(name=black.name, command=black.command),
            tc=settings.tc,
        )
    )
    logger.info("scheduled game=%s on runner=%s", doc.id, runner.runner_id)

    return StartGameResponse(
        id=doc.id,
        status="started",
        white=white.id,
        black=black.id,
    )


@router.get("/game", response_model=list[GameOut])
async def list_games(limit: int = 50) -> list[Game]:
    limit = max(1, min(limit, 200))
    return await Game.find_all().sort("-created_at").limit(limit).to_list()


@router.get("/game/{game_id}", response_model=GameOut)
async def get_game(game_id: UUID) -> Game:
    doc = await Game.get(game_id)
    if doc is None:
        raise NotFoundError("game not found")
    return doc


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()

    intro = schemas.Introduction.model_validate_json(await ws.receive_text())
    runner = streaming.runners.register_runner(
        intro.runner_id, intro.name, max_games=intro.max_games
    )
    logger.info(
        "runner connected id=%s name=%s max_games=%d",
        intro.runner_id,
        intro.name,
        intro.max_games,
    )

    async def receiver() -> None:
        while True:
            data = await ws.receive_text()
            cmd: schemas.ClientCommandType = schemas.client_adapter.validate_json(data)
            match cmd:
                case schemas.GameEvent(game_id=game_id, event=event):
                    try:
                        game = streaming.game_registry.get_game(game_id)
                    except NotFoundError:
                        logger.warning("event for unregistered game_id=%s", game_id)
                        continue
                    if isinstance(event, schemas.GameEndEvent):
                        runner.untrack_game(game_id)
                        streaming.game_registry.registry.pop(game_id, None)
                    await streaming.persist_event(game_id, event)
                    await game.broadcast(event)
                    await streaming.live_stream.broadcast(game_id, event)

    async def sender() -> None:
        while True:
            command = await runner.scheduled_commands.get()
            await ws.send_text(command.model_dump_json())

    recv_task = asyncio.create_task(receiver())
    send_task = asyncio.create_task(sender())

    try:
        done, _ = await asyncio.wait(
            {recv_task, send_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in done:
            task.result()  # re-raise exceptions
    except WebSocketDisconnect:
        logger.info("runner disconnected id=%s", intro.runner_id)
    finally:
        recv_task.cancel()
        send_task.cancel()
        await runner.abort_games()
        streaming.runners.unregister_runner(intro.runner_id)


@router.get(
    "/stream/game/{game_id}",
    response_class=EventSourceResponse,
    # SSE responses are opaque to FastAPI's auto-schema; declaring the
    # per-message payload here makes the event type appear in OpenAPI so
    # the generated TS client can reference it.
    responses={200: {"model": schemas.GameStreamEvent}},
)
async def sse_stream(game_id: UUID) -> AsyncIterable[schemas.GameStreamEvent]:
    try:
        game = streaming.game_registry.get_game(game_id)
    except NotFoundError:
        # Game is no longer live; if it exists in the DB, emit a single
        # terminal event so late subscribers see a clean end rather than 404.
        doc = await Game.get(game_id)
        if doc is None:
            raise
        yield schemas.GameEndEvent(result=doc.result, pgn=doc.pgn)
        return

    q = game.subscribe()
    try:
        # Subscribe-then-snapshot: the WS receiver writes the DB before
        # broadcasting, so anything already in the snapshot is also (or about
        # to be) in our queue. Dedup queued events by ply against the snapshot
        # so the client sees each event exactly once.
        doc = await Game.get(game_id)
        snapshot_ply = -1
        if doc is not None:
            snapshot_ply = len(doc.moves)
            yield schemas.FenEvent(
                fen=doc.fen,
                ply=snapshot_ply,
                white_name=doc.white_name,
                black_name=doc.black_name,
                moves=doc.moves,
                white_clock=doc.white_clock,
                black_clock=doc.black_clock,
                result=doc.result,
                status=doc.status,
                game_id=game_id,
            )

        while True:
            event = await q.get()
            match event:
                case schemas.GameStartEvent() if snapshot_ply >= 0:
                    continue
                case schemas.MoveEvent(ply=ply) if ply <= snapshot_ply:
                    continue
                case schemas.FenEvent(ply=ply) if ply <= snapshot_ply:
                    continue
            yield event
            if isinstance(event, schemas.GameEndEvent):
                return
    except asyncio.CancelledError:
        logger.info("SSE cancelled game=%s", game_id)
        raise
    finally:
        game.unsubscribe(q)


@router.get(
    "/stream/live",
    response_class=EventSourceResponse,
    responses={200: {"model": LiveStreamEvent}},
)
async def sse_live_stream() -> AsyncIterable[LiveStreamEvent]:
    q = streaming.live_stream.subscribe()
    try:
        while True:
            game_id, event = await q.get()
            yield LiveStreamEvent(game_id=game_id, event=event)
    finally:
        streaming.live_stream.unsubscribe(q)
