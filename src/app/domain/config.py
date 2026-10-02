"""Domain settings of the party-game service (extends ``CoreSettings``)."""

from __future__ import annotations

from pydantic import Field

from app.config import CoreSettings, get_settings


class DomainSettings(CoreSettings):
    # --- Rooms ---
    room_code_length: int = Field(default=4, alias="ROOM_CODE_LENGTH")
    room_max_players: int = Field(default=12, alias="ROOM_MAX_PLAYERS")
    # A room nobody touched for this long disappears from Redis.
    room_ttl_seconds: int = Field(default=6 * 3600, alias="ROOM_TTL_SECONDS")
    # Time a disconnected player keeps their seat before being removed / marked inactive.
    reconnect_grace_seconds: int = Field(default=30, alias="RECONNECT_GRACE_SECONDS")

    # --- Realtime transport ---
    redis_key_prefix: str = Field(default="exposed", alias="GAME_REDIS_PREFIX")
    timer_poll_interval_ms: int = Field(default=200, alias="TIMER_POLL_INTERVAL_MS")
    room_lock_timeout_ms: int = Field(default=5000, alias="ROOM_LOCK_TIMEOUT_MS")
    ws_max_message_bytes: int = Field(default=8 * 1024, alias="WS_MAX_MESSAGE_BYTES")
    ws_rate_per_second: float = Field(default=10.0, alias="WS_RATE_PER_SECOND")
    ws_rate_burst: int = Field(default=30, alias="WS_RATE_BURST")

    # --- Game content ---
    game_deck_limit: int = Field(default=200, alias="GAME_DECK_LIMIT")
    # Content languages (ISO 639-1, comma-separated). The first match of the client's
    # Accept-Language wins; otherwise DEFAULT_LOCALE. Adding a language = config + content.
    supported_locales: str = Field(default="ru,en", alias="SUPPORTED_LOCALES")
    default_locale: str = Field(default="ru", alias="DEFAULT_LOCALE")

    def locales(self) -> tuple[str, ...]:
        found = tuple(x.strip().lower() for x in self.supported_locales.split(",") if x.strip())
        return found or (self.default_locale,)


def get_domain_settings() -> DomainSettings:
    """``get_settings()`` instantiates ``registry.settings_cls`` — i.e. this class."""
    settings = get_settings()
    if isinstance(settings, DomainSettings):
        return settings
    # Only reachable when a test swapped the registry for one without settings_cls.
    return DomainSettings()
