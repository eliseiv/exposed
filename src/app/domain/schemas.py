"""REST schemas of the party-game domain (camelCase on the wire, like the core)."""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import Field

from app.schemas.auth import DeviceId, TokenResponse
from app.schemas.common import StrictModel

Nickname = Annotated[str, Field(min_length=1, max_length=24, pattern=r"^\S(.*\S)?$")]
Category = Literal["friendly", "cringe", "spicy"]
ModeKind = Literal["question_list", "wheel", "bomb", "impostor", "fill_blank", "hot_seat"]
CardType = Literal["yes_no", "pick_player", "duel", "dare", "question", "blank_prompt"]
Locale = Annotated[
    str, Field(pattern=r"^[a-z]{2}$", description="Язык контента, ISO 639-1: `ru`, `en`.")
]
RoomCode = Annotated[str, Field(min_length=4, max_length=8, pattern=r"^[A-Za-z0-9]+$")]


# ---- players ---------------------------------------------------------------------------------
class AvatarOut(StrictModel):
    id: int
    key: str = Field(description="Имя ассета аватара в приложении.")


class PlayerProfileOut(StrictModel):
    userId: uuid.UUID
    nickname: str | None = Field(description="Никнейм; `null`, если профиль ещё не заполнен.")
    avatarId: int | None
    avatarKey: str | None


class PlayerProfileUpdate(StrictModel):
    nickname: Nickname
    avatarId: int | None = Field(default=None, description="id из `GET /v1/avatars`.")


class GuestLoginRequest(StrictModel):
    deviceId: DeviceId = Field(description="Стабильный идентификатор устройства (UUID).")
    nickname: Nickname
    avatarId: int | None = None


class GuestLoginResponse(StrictModel):
    tokens: TokenResponse
    profile: PlayerProfileOut


# ---- catalogue -------------------------------------------------------------------------------
class ModeOut(StrictModel):
    id: int
    slug: str
    kind: ModeKind = Field(description="Определяет логику сервера и экран клиента.")
    title: str
    description: str
    icon: str | None
    minPlayers: int
    maxPlayers: int
    defaultSettings: dict[str, Any]
    locale: str = Field(description="Язык названия, описания и карточек игры.")
    cardCounts: dict[str, int] = Field(description="Число активных карточек по категориям.")


# ---- rooms -----------------------------------------------------------------------------------
class CreateRoomRequest(StrictModel):
    modeId: int | None = Field(
        default=None, description="Игра из каталога; `null` — случайная игра при старте."
    )
    locale: Locale | None = Field(
        default=None,
        description=(
            "Язык комнаты (случайная игра и колоды выбираются на нём). Не указан — по "
            "`Accept-Language`. Если указан `modeId`, язык берётся из игры."
        ),
    )


class RoomResponse(StrictModel):
    code: str
    wsPath: str = Field(description="Путь WebSocket-подключения к комнате.")
    room: dict[str, Any] = Field(description="Снимок комнаты (как `room.snapshot` по WS).")


# ---- admin -----------------------------------------------------------------------------------
class ModeIn(StrictModel):
    slug: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")]
    kind: ModeKind
    locale: Locale = "ru"
    title: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=1000)] = ""
    icon: str | None = None
    minPlayers: int = Field(default=2, ge=1, le=50)
    maxPlayers: int = Field(default=12, ge=1, le=50)
    defaultSettings: dict[str, Any] = Field(default_factory=dict)
    sortOrder: int = 0
    isActive: bool = True


class ModePatch(StrictModel):
    locale: Locale | None = None
    title: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    description: Annotated[str, Field(max_length=1000)] | None = None
    icon: str | None = None
    minPlayers: int | None = Field(default=None, ge=1, le=50)
    maxPlayers: int | None = Field(default=None, ge=1, le=50)
    defaultSettings: dict[str, Any] | None = None
    sortOrder: int | None = None
    isActive: bool | None = None


class AdminModeOut(StrictModel):
    id: int
    slug: str
    kind: str
    locale: str
    title: str
    description: str
    icon: str | None
    minPlayers: int
    maxPlayers: int
    defaultSettings: dict[str, Any]
    sortOrder: int
    isActive: bool
    createdAt: datetime.datetime


class CardIn(StrictModel):
    modeId: int
    type: CardType
    category: Category
    text: Annotated[str, Field(min_length=1, max_length=500)]
    isAnonymous: bool = False
    optionsCount: int | None = Field(default=None, ge=2, le=50)
    isActive: bool = True


class CardPatch(StrictModel):
    type: CardType | None = None
    category: Category | None = None
    text: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    isAnonymous: bool | None = None
    optionsCount: int | None = Field(default=None, ge=2, le=50)
    isActive: bool | None = None


class CardOut(StrictModel):
    id: int
    modeId: int
    type: str
    category: str
    text: str
    isAnonymous: bool
    optionsCount: int | None
    isActive: bool
    createdAt: datetime.datetime


class CardsImportRequest(StrictModel):
    cards: list[CardIn] = Field(min_length=1, max_length=1000)


class ImportResult(StrictModel):
    created: int


class WordIn(StrictModel):
    word: Annotated[str, Field(min_length=1, max_length=100)]
    hint: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    category: Category = "friendly"
    locale: Locale = "ru"
    isActive: bool = True


class WordPatch(StrictModel):
    word: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    hint: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    category: Category | None = None
    locale: Locale | None = None
    isActive: bool | None = None


class WordOut(StrictModel):
    id: int
    word: str
    hint: str | None
    category: str
    locale: str
    isActive: bool


class AnswerIn(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=200)]
    category: Category = "friendly"
    locale: Locale = "ru"
    isActive: bool = True


class AnswerPatch(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    category: Category | None = None
    locale: Locale | None = None
    isActive: bool | None = None


class AnswerOut(StrictModel):
    id: int
    text: str
    category: str
    locale: str
    isActive: bool


class AvatarIn(StrictModel):
    key: Annotated[str, Field(min_length=1, max_length=64)]
    sortOrder: int = 0
    isActive: bool = True


class AvatarPatch(StrictModel):
    sortOrder: int | None = None
    isActive: bool | None = None


class AdminAvatarOut(StrictModel):
    id: int
    key: str
    sortOrder: int
    isActive: bool


T = TypeVar("T")


class Page(StrictModel, Generic[T]):
    items: list[T]
    total: int
