"""Guest login + player profile + avatars + the game catalogue."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.api_gateway.rate_limit import enforce_auth_limits, enforce_other_limits
from app.auth.service import AuthService
from app.deps import CurrentUser, DbSession, client_ip, get_auth_service
from app.domain.config import get_domain_settings
from app.domain.content.repository import ContentRepository
from app.domain.locale import resolve_locale
from app.domain.players.service import PlayerService, Profile
from app.domain.schemas import (
    AvatarOut,
    GuestLoginRequest,
    GuestLoginResponse,
    Locale,
    ModeOut,
    PlayerProfileOut,
    PlayerProfileUpdate,
)
from app.errors import RateLimitedError
from app.schemas.auth import TokenResponse

router = APIRouter(prefix="/v1", tags=["Players"])
catalog_router = APIRouter(prefix="/v1", tags=["Catalog"])


def get_player_service(session: DbSession) -> PlayerService:
    return PlayerService(session)


def profile_out(p: Profile) -> PlayerProfileOut:
    return PlayerProfileOut(
        userId=p.user_id, nickname=p.nickname, avatarId=p.avatar_id, avatarKey=p.avatar_key
    )


@router.post(
    "/guest",
    response_model=GuestLoginResponse,
    summary="Гостевой вход",
    description=(
        "Мгновенный вход без почты и пароля: `deviceId` (UUID устройства) + никнейм + аватар. "
        "Создаёт или находит пользователя по `deviceId` (повторный вход с того же устройства — "
        "тот же `userId`), сохраняет профиль и выдаёт пару токенов. Обновление токенов — "
        "`POST /v1/auth/refresh`."
    ),
)
async def guest_login(
    request: Request,
    body: GuestLoginRequest,
    auth: Annotated[AuthService, Depends(get_auth_service)],
    players: Annotated[PlayerService, Depends(get_player_service)],
) -> GuestLoginResponse:
    if not await enforce_auth_limits(ip=client_ip(request)):
        raise RateLimitedError("rate limit exceeded")
    tokens = await auth.register_or_token(body.deviceId)
    profile = await players.upsert(tokens.user_id, body.nickname, body.avatarId)
    return GuestLoginResponse(
        tokens=TokenResponse(
            userId=tokens.user_id,
            deviceId=tokens.device_id,
            accessToken=tokens.access_token,
            tokenType="Bearer",
            expiresIn=tokens.expires_in,
            refreshToken=tokens.refresh_token,
            refreshExpiresIn=tokens.refresh_expires_in,
        ),
        profile=profile_out(profile),
    )


@router.get("/players/me", response_model=PlayerProfileOut, summary="Мой профиль")
async def get_me(
    current: CurrentUser,
    players: Annotated[PlayerService, Depends(get_player_service)],
) -> PlayerProfileOut:
    return profile_out(await players.get(current.user_id))


@router.put(
    "/players/me",
    response_model=PlayerProfileOut,
    summary="Изменить никнейм и аватар",
    description="Изменения видны в комнатах, в которые игрок входит после этого.",
)
async def put_me(
    current: CurrentUser,
    body: PlayerProfileUpdate,
    players: Annotated[PlayerService, Depends(get_player_service)],
) -> PlayerProfileOut:
    if not await enforce_other_limits(user_id=current.user_id):
        raise RateLimitedError("rate limit exceeded")
    return profile_out(await players.upsert(current.user_id, body.nickname, body.avatarId))


@router.get(
    "/avatars",
    response_model=list[AvatarOut],
    summary="Заготовленные аватары",
    description="`key` — имя ассета в приложении. Публичный эндпоинт (нужен до входа).",
)
async def list_avatars(
    players: Annotated[PlayerService, Depends(get_player_service)],
) -> list[AvatarOut]:
    return [AvatarOut(id=a.id, key=a.key) for a in await players.avatars()]


def content_locale(explicit: str | None, accept_language: str | None) -> str:
    """Explicit choice → Accept-Language → DEFAULT_LOCALE, limited to SUPPORTED_LOCALES."""
    settings = get_domain_settings()
    return resolve_locale(explicit, accept_language, settings.locales(), settings.default_locale)


@catalog_router.get(
    "/modes",
    response_model=list[ModeOut],
    summary="Каталог игр",
    description=(
        "Активные игры в порядке показа. `kind` определяет логику и экран: `question_list`, "
        "`wheel`, `bomb`, `impostor`, `fill_blank`, `hot_seat`. Новая игра типа «список "
        "вопросов» добавляется через admin API — без изменений кода клиента и сервера.\n\n"
        "Язык: `?locale=en`, иначе по заголовку `Accept-Language`; поддерживаемые — "
        "`SUPPORTED_LOCALES` (`ru`, `en`). Если игр на этом языке нет, отдаётся язык по "
        "умолчанию. Фактический язык — в заголовке ответа `Content-Language`."
    ),
)
async def list_modes(
    session: DbSession,
    response: Response,
    locale: Annotated[
        Locale | None, Query(description="Язык каталога; не указан — по `Accept-Language`.")
    ] = None,
    accept_language: Annotated[str | None, Header()] = None,
) -> list[ModeOut]:
    repo = ContentRepository(session)
    resolved = content_locale(locale, accept_language)
    if not await repo.has_modes(resolved):  # no games in that language yet → default one
        resolved = get_domain_settings().default_locale
    response.headers["Content-Language"] = resolved
    rows = await repo.list_modes(resolved)
    return [
        ModeOut(
            id=m.id,
            slug=m.slug,
            kind=m.kind,
            title=m.title,
            description=m.description,
            icon=m.icon,
            minPlayers=m.min_players,
            maxPlayers=m.max_players,
            defaultSettings=m.default_settings or {},
            locale=m.locale,
            cardCounts=counts,
        )
        for m, counts in rows
    ]
