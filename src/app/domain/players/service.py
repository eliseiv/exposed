"""Player profile: nickname + preset avatar."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import InvalidAvatarError
from app.domain.models import Avatar


@dataclass(frozen=True)
class Profile:
    user_id: uuid.UUID
    nickname: str | None
    avatar_id: int | None
    avatar_key: str | None


class PlayerService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: uuid.UUID) -> Profile:
        row = (
            await self._s.execute(
                text(
                    "SELECT p.nickname, p.avatar_id, a.key FROM player_profiles p "
                    "LEFT JOIN avatars a ON a.id = p.avatar_id WHERE p.user_id = :uid"
                ),
                {"uid": str(user_id)},
            )
        ).first()
        if row is None:
            return Profile(user_id=user_id, nickname=None, avatar_id=None, avatar_key=None)
        return Profile(user_id=user_id, nickname=row[0], avatar_id=row[1], avatar_key=row[2])

    async def upsert(self, user_id: uuid.UUID, nickname: str, avatar_id: int | None) -> Profile:
        if avatar_id is not None:
            avatar = await self._s.get(Avatar, avatar_id)
            if avatar is None or not avatar.is_active:
                raise InvalidAvatarError("unknown avatarId")
        await self._s.execute(
            text(
                "INSERT INTO player_profiles (user_id, nickname, avatar_id, updated_at) "
                "VALUES (:uid, :nick, :avatar, now()) "
                "ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname, "
                "avatar_id = EXCLUDED.avatar_id, updated_at = now()"
            ),
            {"uid": str(user_id), "nick": nickname, "avatar": avatar_id},
        )
        return await self.get(user_id)

    async def avatars(self) -> list[Avatar]:
        return list(
            (
                await self._s.scalars(
                    select(Avatar)
                    .where(Avatar.is_active.is_(True))
                    .order_by(Avatar.sort_order, Avatar.id)
                )
            ).all()
        )
