"""THE extension point. The template ships the MINIMAL domain: one generate route.

That minimum is deliberate. It makes ``POST /v1/generate`` work the moment ``docker compose up``
finishes (on ``EchoProvider``), and it is the worked example of the whole contract: a service is a
router + a provider + (optionally) settings, a pricing policy, tables and body-limit rules — and
NOT ONE core file is touched (AC-9).

A real service replaces this file with its own:

    from app.extensions.registry import BodyLimitRule, DomainRegistry
    from app.domain.config import DomainSettings
    from app.domain.provider import FluxProvider
    from app.domain.routers.image import router as image_router

    REGISTRY = DomainRegistry(
        routers=(image_router,),
        openapi_tags=({"name": "Image", "description": "Генерация изображений"},),
        generation_provider=FluxProvider(),
        settings_cls=DomainSettings,
        truncate_tables=("image_presets",),
        body_limit_rules=(BodyLimitRule(match="/v1/image/upload", limit=12 * 1024 * 1024),),
    )

Dependency direction is strictly one-way: ``app.domain`` → ``app`` (core), never back. The core
never imports this package; it receives the domain only as the data in ``REGISTRY``.
"""

from __future__ import annotations

from app.domain.routers.generate import router as generate_router
from app.extensions.registry import DomainRegistry

REGISTRY = DomainRegistry(
    routers=(generate_router,),
    openapi_tags=(
        {
            "name": "Generation",
            "description": (
                "Запуск генерации и её результаты. Блокировки (нет подписки, кончились кредиты) "
                "приходят с кодом 200 и полем blockReason — это не ошибка."
            ),
        },
    ),
    # generation_provider is left unset on purpose: with GENERATION_PROVIDER=echo the core uses the
    # built-in EchoProvider. A real domain sets `generation_provider=FluxProvider()` here.
)
