"""Admin API for game content: modes, cards, impostor words, answer options, avatars.

Behind the core ``X-Admin-Token`` (``require_admin``) and the per-IP admin rate limit. A new
"question list" game is created here — a ``kind=question_list`` mode plus its cards — without
touching server or client code. Every change is logged as a structured ``admin_content`` event
(``audit_logs`` needs a user id, and the admin is not a user).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_gateway.auth import require_admin
from app.api_gateway.rate_limit import enforce_admin_limits
from app.deps import DbSession, client_ip
from app.domain.errors import ContentNotFoundError, ModeNotFoundError, SlugTakenError
from app.domain.models import Avatar, BlankAnswer, Card, GameMode, ImpostorWord
from app.domain.schemas import (
    AdminAvatarOut,
    AdminModeOut,
    AnswerIn,
    AnswerOut,
    AnswerPatch,
    AvatarIn,
    AvatarPatch,
    CardIn,
    CardOut,
    CardPatch,
    CardsImportRequest,
    Category,
    ImportResult,
    ModeIn,
    ModePatch,
    Page,
    WordIn,
    WordOut,
    WordPatch,
)
from app.errors import RateLimitedError, ValidationFailedError
from app.observability.logging import log_event

logger = logging.getLogger("app.domain.admin")


async def _limit(request: Request) -> None:
    if not await enforce_admin_limits(ip=client_ip(request)):
        raise RateLimitedError("rate limit exceeded")


router = APIRouter(
    prefix="/v1/admin/content",
    tags=["Admin: content"],
    dependencies=[Depends(require_admin), Depends(_limit)],
)

M = TypeVar("M")

# camelCase API field -> ORM attribute
_FIELDS = {
    "minPlayers": "min_players",
    "maxPlayers": "max_players",
    "defaultSettings": "default_settings",
    "sortOrder": "sort_order",
    "isActive": "is_active",
    "modeId": "mode_id",
    "isAnonymous": "is_anonymous",
    "optionsCount": "options_count",
}


def _orm_fields(values: dict[str, Any]) -> dict[str, Any]:
    return {_FIELDS.get(k, k): v for k, v in values.items()}


async def _get(session: AsyncSession, model: type[M], obj_id: int) -> M:
    obj = await session.get(model, obj_id)
    if obj is None:
        raise ContentNotFoundError(f"{model.__name__} {obj_id} not found")
    return obj


def _audit(action: str, entity: str, obj_id: Any) -> None:
    log_event(logger, logging.INFO, "admin_content", action=action, entity=entity, id=obj_id)


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ValidationFailedError("constraint violated") from exc


def _page(items: list[Any], total: int, conv: Callable[[Any], Any]) -> dict[str, Any]:
    return {"items": [conv(i) for i in items], "total": total}


async def _list(session: AsyncSession, stmt: Any, limit: int, offset: int) -> tuple[list[Any], int]:
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    items = (await session.scalars(stmt.limit(limit).offset(offset))).all()
    return list(items), int(total or 0)


Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


# ---- modes -----------------------------------------------------------------------------------
def mode_out(m: GameMode) -> AdminModeOut:
    return AdminModeOut(
        id=m.id,
        slug=m.slug,
        kind=m.kind,
        title=m.title,
        description=m.description,
        icon=m.icon,
        minPlayers=m.min_players,
        maxPlayers=m.max_players,
        defaultSettings=m.default_settings or {},
        sortOrder=m.sort_order,
        isActive=m.is_active,
        createdAt=m.created_at,
    )


def _check_mode(kind: str, min_p: int, max_p: int, settings: dict[str, Any]) -> None:
    from app.domain.game.core import GameError
    from app.domain.game.modes import get_handler

    if max_p < min_p:
        raise ValidationFailedError("maxPlayers must be >= minPlayers")
    handler = get_handler(kind)
    if min_p < handler.min_players:
        raise ValidationFailedError(f"'{kind}' needs at least {handler.min_players} players")
    try:
        handler.parse_settings(settings)
    except GameError as exc:
        raise ValidationFailedError(f"defaultSettings: {exc.message}") from exc


@router.get("/modes", response_model=list[AdminModeOut], summary="Все игры (вкл. неактивные)")
async def admin_list_modes(session: DbSession) -> list[AdminModeOut]:
    rows = await session.scalars(select(GameMode).order_by(GameMode.sort_order, GameMode.id))
    return [mode_out(m) for m in rows]


@router.post(
    "/modes",
    response_model=AdminModeOut,
    status_code=201,
    summary="Создать игру",
    description=(
        "`kind` выбирает серверную логику. Для новой игры-«списка вопросов» — "
        "`kind=question_list`, затем добавьте карточки. `defaultSettings` проверяются схемой "
        "настроек этого `kind`."
    ),
)
async def admin_create_mode(body: ModeIn, session: DbSession) -> AdminModeOut:
    _check_mode(body.kind, body.minPlayers, body.maxPlayers, body.defaultSettings)
    if await session.scalar(select(GameMode.id).where(GameMode.slug == body.slug)):
        raise SlugTakenError("slug already exists")
    mode = GameMode(**_orm_fields(body.model_dump()))
    session.add(mode)
    await _flush(session)
    await session.refresh(mode)
    _audit("create", "mode", mode.id)
    return mode_out(mode)


@router.patch("/modes/{mode_id}", response_model=AdminModeOut, summary="Изменить игру")
async def admin_update_mode(mode_id: int, body: ModePatch, session: DbSession) -> AdminModeOut:
    mode = await session.get(GameMode, mode_id)
    if mode is None:
        raise ModeNotFoundError("no such mode")
    for key, value in _orm_fields(body.model_dump(exclude_unset=True)).items():
        setattr(mode, key, value)
    _check_mode(mode.kind, mode.min_players, mode.max_players, mode.default_settings or {})
    await _flush(session)
    _audit("update", "mode", mode_id)
    return mode_out(mode)


@router.delete(
    "/modes/{mode_id}",
    status_code=204,
    response_model=None,
    summary="Удалить игру вместе с карточками",
    description="Чтобы только скрыть игру из каталога, используйте `isActive=false`.",
)
async def admin_delete_mode(mode_id: int, session: DbSession) -> None:
    mode = await session.get(GameMode, mode_id)
    if mode is None:
        raise ModeNotFoundError("no such mode")
    await session.delete(mode)
    _audit("delete", "mode", mode_id)


# ---- cards -----------------------------------------------------------------------------------
def card_out(c: Card) -> CardOut:
    return CardOut(
        id=c.id,
        modeId=c.mode_id,
        type=c.type,
        category=c.category,
        text=c.text,
        isAnonymous=c.is_anonymous,
        optionsCount=c.options_count,
        isActive=c.is_active,
        createdAt=c.created_at,
    )


async def _require_mode(session: AsyncSession, mode_id: int) -> None:
    if await session.get(GameMode, mode_id) is None:
        raise ModeNotFoundError(f"mode {mode_id} not found")


@router.get("/cards", response_model=Page[CardOut], summary="Карточки")
async def admin_list_cards(
    session: DbSession,
    modeId: int | None = None,
    category: Category | None = None,
    isActive: bool | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Поиск по тексту")] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> dict[str, Any]:
    stmt = select(Card).order_by(Card.id)
    if modeId is not None:
        stmt = stmt.where(Card.mode_id == modeId)
    if category is not None:
        stmt = stmt.where(Card.category == category)
    if isActive is not None:
        stmt = stmt.where(Card.is_active.is_(isActive))
    if q:
        stmt = stmt.where(Card.text.ilike(f"%{q}%"))
    items, total = await _list(session, stmt, limit, offset)
    return _page(items, total, card_out)


@router.post("/cards", response_model=CardOut, status_code=201, summary="Создать карточку")
async def admin_create_card(body: CardIn, session: DbSession) -> CardOut:
    await _require_mode(session, body.modeId)
    card = Card(**_orm_fields(body.model_dump()))
    session.add(card)
    await _flush(session)
    await session.refresh(card)
    _audit("create", "card", card.id)
    return card_out(card)


@router.post(
    "/cards/import",
    response_model=ImportResult,
    status_code=201,
    summary="Массовый импорт карточек (до 1000)",
)
async def admin_import_cards(body: CardsImportRequest, session: DbSession) -> ImportResult:
    for mode_id in {c.modeId for c in body.cards}:
        await _require_mode(session, mode_id)
    session.add_all(Card(**_orm_fields(c.model_dump())) for c in body.cards)
    await _flush(session)
    _audit("import", "card", len(body.cards))
    return ImportResult(created=len(body.cards))


@router.patch("/cards/{card_id}", response_model=CardOut, summary="Изменить карточку")
async def admin_update_card(card_id: int, body: CardPatch, session: DbSession) -> CardOut:
    card = await _get(session, Card, card_id)
    for key, value in _orm_fields(body.model_dump(exclude_unset=True)).items():
        setattr(card, key, value)
    await _flush(session)
    _audit("update", "card", card_id)
    return card_out(card)


@router.delete("/cards/{card_id}", status_code=204, response_model=None, summary="Удалить карточку")
async def admin_delete_card(card_id: int, session: DbSession) -> None:
    await session.delete(await _get(session, Card, card_id))
    _audit("delete", "card", card_id)


# ---- impostor words --------------------------------------------------------------------------
def word_out(w: ImpostorWord) -> WordOut:
    return WordOut(id=w.id, word=w.word, hint=w.hint, category=w.category, isActive=w.is_active)


@router.get("/impostor-words", response_model=Page[WordOut], summary="Слова для «Импостера»")
async def admin_list_words(
    session: DbSession,
    category: Category | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> dict[str, Any]:
    stmt = select(ImpostorWord).order_by(ImpostorWord.id)
    if category is not None:
        stmt = stmt.where(ImpostorWord.category == category)
    items, total = await _list(session, stmt, limit, offset)
    return _page(items, total, word_out)


@router.post("/impostor-words", response_model=WordOut, status_code=201, summary="Добавить слово")
async def admin_create_word(body: WordIn, session: DbSession) -> WordOut:
    word = ImpostorWord(**_orm_fields(body.model_dump()))
    session.add(word)
    await _flush(session)
    _audit("create", "impostor_word", word.id)
    return word_out(word)


@router.patch("/impostor-words/{word_id}", response_model=WordOut, summary="Изменить слово")
async def admin_update_word(word_id: int, body: WordPatch, session: DbSession) -> WordOut:
    word = await _get(session, ImpostorWord, word_id)
    for key, value in _orm_fields(body.model_dump(exclude_unset=True)).items():
        setattr(word, key, value)
    await _flush(session)
    _audit("update", "impostor_word", word_id)
    return word_out(word)


@router.delete(
    "/impostor-words/{word_id}", status_code=204, response_model=None, summary="Удалить слово"
)
async def admin_delete_word(word_id: int, session: DbSession) -> None:
    await session.delete(await _get(session, ImpostorWord, word_id))
    _audit("delete", "impostor_word", word_id)


# ---- blank answers ---------------------------------------------------------------------------
def answer_out(a: BlankAnswer) -> AnswerOut:
    return AnswerOut(id=a.id, text=a.text, category=a.category, isActive=a.is_active)


@router.get("/blank-answers", response_model=Page[AnswerOut], summary="Варианты для «Допиши фразу»")
async def admin_list_answers(
    session: DbSession,
    category: Category | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> dict[str, Any]:
    stmt = select(BlankAnswer).order_by(BlankAnswer.id)
    if category is not None:
        stmt = stmt.where(BlankAnswer.category == category)
    items, total = await _list(session, stmt, limit, offset)
    return _page(items, total, answer_out)


@router.post(
    "/blank-answers", response_model=AnswerOut, status_code=201, summary="Добавить вариант"
)
async def admin_create_answer(body: AnswerIn, session: DbSession) -> AnswerOut:
    answer = BlankAnswer(**_orm_fields(body.model_dump()))
    session.add(answer)
    await _flush(session)
    _audit("create", "blank_answer", answer.id)
    return answer_out(answer)


@router.patch("/blank-answers/{answer_id}", response_model=AnswerOut, summary="Изменить вариант")
async def admin_update_answer(answer_id: int, body: AnswerPatch, session: DbSession) -> AnswerOut:
    answer = await _get(session, BlankAnswer, answer_id)
    for key, value in _orm_fields(body.model_dump(exclude_unset=True)).items():
        setattr(answer, key, value)
    await _flush(session)
    _audit("update", "blank_answer", answer_id)
    return answer_out(answer)


@router.delete(
    "/blank-answers/{answer_id}", status_code=204, response_model=None, summary="Удалить вариант"
)
async def admin_delete_answer(answer_id: int, session: DbSession) -> None:
    await session.delete(await _get(session, BlankAnswer, answer_id))
    _audit("delete", "blank_answer", answer_id)


# ---- avatars ---------------------------------------------------------------------------------
def avatar_out(a: Avatar) -> AdminAvatarOut:
    return AdminAvatarOut(id=a.id, key=a.key, sortOrder=a.sort_order, isActive=a.is_active)


@router.get("/avatars", response_model=list[AdminAvatarOut], summary="Все аватары")
async def admin_list_avatars(session: DbSession) -> list[AdminAvatarOut]:
    rows = await session.scalars(select(Avatar).order_by(Avatar.sort_order, Avatar.id))
    return [avatar_out(a) for a in rows]


@router.post("/avatars", response_model=AdminAvatarOut, status_code=201, summary="Добавить аватар")
async def admin_create_avatar(body: AvatarIn, session: DbSession) -> AdminAvatarOut:
    avatar = Avatar(**_orm_fields(body.model_dump()))
    session.add(avatar)
    await _flush(session)
    _audit("create", "avatar", avatar.id)
    return avatar_out(avatar)


@router.patch("/avatars/{avatar_id}", response_model=AdminAvatarOut, summary="Изменить аватар")
async def admin_update_avatar(
    avatar_id: int, body: AvatarPatch, session: DbSession
) -> AdminAvatarOut:
    avatar = await _get(session, Avatar, avatar_id)
    for key, value in _orm_fields(body.model_dump(exclude_unset=True)).items():
        setattr(avatar, key, value)
    await _flush(session)
    _audit("update", "avatar", avatar_id)
    return avatar_out(avatar)
