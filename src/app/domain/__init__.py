"""THE extension point: the party-game domain ("Exposed / Who's Most Likely To").

REST: guest login, profile, catalogue, rooms, admin content API.
Realtime: ``/v1/ws/rooms/{code}`` — room state in Redis, fan-out over Redis pub/sub, distributed
timers (see ``realtime/``); the game rules are a pure engine (``game/``).

The template's sample ``POST /v1/generate`` route is kept registered: the core test-suite
(``tests/conftest.py``) patches it by module path, and it is harmless (the core policy blocks it
without a subscription).
"""

from __future__ import annotations

from app.domain.config import DomainSettings
from app.domain.models import DOMAIN_TABLES
from app.domain.realtime.runtime import start_runtime, stop_runtime
from app.domain.realtime.ws import router as ws_router
from app.domain.routers.admin_content import router as admin_content_router
from app.domain.routers.generate import router as generate_router
from app.domain.routers.players import catalog_router
from app.domain.routers.players import router as players_router
from app.domain.routers.rooms import router as rooms_router
from app.extensions.registry import DomainRegistry

_API_DESCRIPTION = """
### Игра
1. `POST /v1/guest` — вход по `deviceId` + никнейм + аватар → токены.
2. `POST /v1/rooms` (хост) или `POST /v1/rooms/{code}/join` (игроки).
3. WebSocket `GET /v1/ws/rooms/{code}` с заголовком `Authorization: Bearer <accessToken>`:
   первым приходит `room.snapshot`, дальше — события комнаты по порядку `seq`.
   Команды: `{"type": "...", "msgId": "...", "data": {...}}` → `ack` / `error`.

Полное описание протокола — `docs/realtime-protocol.md`.
"""

REGISTRY = DomainRegistry(
    routers=(
        players_router,
        catalog_router,
        rooms_router,
        ws_router,
        admin_content_router,
        generate_router,
    ),
    openapi_tags=(
        {"name": "Players", "description": "Гостевой вход, профиль игрока, аватары."},
        {"name": "Catalog", "description": "Каталог игр."},
        {
            "name": "Rooms",
            "description": "Создание комнаты и вход по коду. Игра идёт по WebSocket.",
        },
        {
            "name": "Admin: content",
            "description": "Управление играми, карточками, словами и аватарами (`X-Admin-Token`).",
        },
        {
            "name": "Generation",
            "description": "Пример маршрута шаблона (в игре не используется).",
        },
    ),
    api_description=_API_DESCRIPTION,
    settings_cls=DomainSettings,
    metrics_module="app.domain.metrics",
    truncate_tables=DOMAIN_TABLES,
    on_startup=(start_runtime,),
    on_shutdown=(stop_runtime,),
)
