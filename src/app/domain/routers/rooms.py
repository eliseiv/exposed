"""Rooms over REST: create, join, read, leave. Everything else happens over the WebSocket."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path

from app.api_gateway.rate_limit import enforce_other_limits
from app.deps import CurrentUser
from app.domain.errors import ProfileRequiredError, RoomCommandError, RoomNotFoundError
from app.domain.game.core import Command, GameError
from app.domain.game.engine import room_view
from app.domain.game.state import Player
from app.domain.players.service import PlayerService
from app.domain.realtime.manager import normalize_code
from app.domain.realtime.runtime import get_runtime
from app.domain.routers.players import content_locale, get_player_service
from app.domain.schemas import CreateRoomRequest, RoomCode, RoomResponse
from app.errors import RateLimitedError

router = APIRouter(prefix="/v1/rooms", tags=["Rooms"])

CodePath = Annotated[RoomCode, Path(description="Код комнаты, например `X7B2`.")]


def ws_path(code: str) -> str:
    return f"/v1/ws/rooms/{code}"


async def _player(current: CurrentUser, players: PlayerService) -> Player:
    profile = await players.get(current.user_id)
    if profile.nickname is None:
        raise ProfileRequiredError("set a nickname first (POST /v1/guest or PUT /v1/players/me)")
    return Player(
        user_id=str(current.user_id),
        nickname=profile.nickname,
        avatar_id=profile.avatar_id,
        avatar_key=profile.avatar_key,
        joined_at=0,
    )


async def _limit(current: CurrentUser) -> None:
    if not await enforce_other_limits(user_id=current.user_id):
        raise RateLimitedError("rate limit exceeded")


@router.post(
    "",
    response_model=RoomResponse,
    status_code=201,
    summary="Создать комнату",
    description=(
        "Создатель становится хостом (администратором комнаты). Сервер генерирует короткий "
        "код (например `X7B2`). Дальше хост подключается по WebSocket `wsPath`."
    ),
)
async def create_room(
    current: CurrentUser,
    body: CreateRoomRequest,
    players: Annotated[PlayerService, Depends(get_player_service)],
    accept_language: Annotated[str | None, Header()] = None,
) -> RoomResponse:
    await _limit(current)
    player = await _player(current, players)
    locale = content_locale(body.locale, accept_language)
    try:
        room = await get_runtime().manager.create_room(player, body.modeId, locale)
    except GameError as exc:
        raise RoomCommandError(exc) from exc
    return RoomResponse(
        code=room.code, wsPath=ws_path(room.code), room=room_view(room, player.user_id)
    )


@router.post(
    "/{code}/join",
    response_model=RoomResponse,
    summary="Войти в комнату по коду",
    description=(
        "Добавляет игрока в комнату (остальные получают `player.joined` по WebSocket). "
        "Повторный вход в свою комнату безопасен. Ошибки: `404 room_not_found`, "
        "`409 room_full`, `409 game_in_progress`, `403 kicked`."
    ),
)
async def join_room(
    code: CodePath,
    current: CurrentUser,
    players: Annotated[PlayerService, Depends(get_player_service)],
) -> RoomResponse:
    await _limit(current)
    player = await _player(current, players)
    code = normalize_code(code)
    try:
        room = await get_runtime().manager.join(code, player)
    except GameError as exc:
        raise RoomCommandError(exc) from exc
    return RoomResponse(
        code=room.code, wsPath=ws_path(room.code), room=room_view(room, player.user_id)
    )


@router.get("/{code}", response_model=RoomResponse, summary="Снимок комнаты (только участникам)")
async def get_room(code: CodePath, current: CurrentUser) -> RoomResponse:
    code = normalize_code(code)
    room = await get_runtime().store.load(code)
    uid = str(current.user_id)
    if room is None or room.player(uid) is None:
        raise RoomNotFoundError("no such room")
    return RoomResponse(code=room.code, wsPath=ws_path(room.code), room=room_view(room, uid))


@router.post(
    "/{code}/leave",
    status_code=204,
    response_model=None,
    summary="Выйти из комнаты",
    description="То же, что `room.leave` по WebSocket: место освобождается сразу, без ожидания.",
)
async def leave_room(code: CodePath, current: CurrentUser) -> None:
    try:
        await get_runtime().manager.execute(
            normalize_code(code), Command(type="room.leave", user_id=str(current.user_id))
        )
    except GameError as exc:
        raise RoomCommandError(exc) from exc
