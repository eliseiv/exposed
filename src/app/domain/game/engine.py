"""The room/game state machine: ``apply(room, command, ctx)``.

Room-level commands (join, presence, leave/kick, host, settings, start/end) are handled here;
everything else is routed to the handler of the running game's ``kind``.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.constants import CATEGORIES as CATEGORIES_ALL
from app.domain.game.core import GRACE_TIMER, PHASE_TIMER, Command, Ctx, GameError, player_view
from app.domain.game.lifecycle import finish_game
from app.domain.game.modes import get_handler
from app.domain.game.state import GameContent, ModeInfo, Player, RoomState

_LOCALE_RE = re.compile(r"[a-z]{2}")


def new_room(
    *,
    code: str,
    host: Player,
    now: int,
    max_players: int,
    mode: ModeInfo | None,
    locale: str = "ru",
) -> RoomState:
    return RoomState(
        code=code,
        host_id=host.user_id,
        created_at=now,
        updated_at=now,
        max_players=min(max_players, mode.max_players) if mode else max_players,
        mode=mode,
        locale=mode.locale if mode else locale,  # a chosen game decides the language
        players=[host],
    )


def effective_settings(mode: ModeInfo, overrides: dict[str, Any]) -> dict[str, Any]:
    return {**mode.default_settings, **overrides}


def required_players(room: RoomState) -> int:
    game_mode = room.game.mode if room.game is not None else room.mode
    floor = get_handler(game_mode.kind).min_players if game_mode else 2
    return max(floor, game_mode.min_players if game_mode else 2)


# --------------------------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------------------------
def mode_view(mode: ModeInfo | None) -> dict[str, Any] | None:
    if mode is None:
        return None
    return {
        "id": mode.id,
        "slug": mode.slug,
        "kind": mode.kind,
        "title": mode.title,
        "minPlayers": mode.min_players,
        "maxPlayers": mode.max_players,
        "locale": mode.locale,
    }


def room_view(room: RoomState, viewer: str) -> dict[str, Any]:
    """The personal snapshot of the room for ``viewer`` (hidden info stays hidden)."""
    game_view: dict[str, Any] | None = None
    if room.game is not None:
        handler = get_handler(room.game.kind)
        game_view = {
            "kind": room.game.kind,
            "mode": mode_view(room.game.mode),
            **handler.project(room, room.game, viewer),
        }
    return {
        "code": room.code,
        "hostId": room.host_id,
        "status": room.status,
        "mode": mode_view(room.mode),
        "locale": room.locale,
        "categories": room.categories,
        "settings": room.settings,
        "maxPlayers": room.max_players,
        "players": [player_view(room, p) for p in room.players],
        "seq": room.seq,
        "game": game_view,
    }


# --------------------------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------------------------
def apply(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    room.updated_at = ctx.now
    handler = _ROOM_COMMANDS.get(cmd.type)
    if handler is not None:
        handler(room, cmd, ctx)
        return
    if room.status != "playing" or room.game is None:
        raise GameError("no_game", "no game is running")
    if room.player(cmd.user_id or "") is None:
        raise GameError("not_in_room", "you are not in this room")
    mode = get_handler(room.game.kind)
    mode.handle(room, room.game, cmd, ctx)


def _member(room: RoomState, cmd: Command) -> Player:
    p = room.player(cmd.user_id or "")
    if p is None:
        raise GameError("not_in_room", "you are not in this room")
    return p


def _require_host(room: RoomState, cmd: Command) -> None:
    if cmd.user_id != room.host_id:
        raise GameError("not_host", "only the host can do this")


def _require_lobby(room: RoomState) -> None:
    if room.status != "lobby":
        raise GameError("game_in_progress", "not allowed while a game is running")


# ---- membership ----------------------------------------------------------------------------
def _join(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    uid = cmd.user_id or ""
    nickname = str(cmd.data.get("nickname") or "Player")
    avatar_id = cmd.data.get("avatarId")
    avatar_key = cmd.data.get("avatarKey")
    if uid in room.banned:
        raise GameError("kicked", "you were removed from this room")
    existing = room.player(uid)
    if existing is not None:
        if not existing.active:
            raise GameError("game_in_progress", "wait until the current game ends")
        existing.nickname, existing.avatar_id, existing.avatar_key = nickname, avatar_id, avatar_key
        ctx.result["rejoined"] = True
        return
    if room.status != "lobby":
        raise GameError("game_in_progress", "the game has already started")
    if len(room.players) >= room.max_players:
        raise GameError("room_full", "the room is full")
    p = Player(
        user_id=uid,
        nickname=nickname,
        avatar_id=avatar_id,
        avatar_key=avatar_key,
        joined_at=ctx.now,
    )
    room.players.append(p)
    ctx.emit("player.joined", {"player": player_view(room, p)})


def _connect(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    p = _member(room, cmd)
    if not p.active:
        raise GameError("game_in_progress", "wait until the current game ends")
    was_connected = p.connected
    p.connected = True
    p.conn_epoch += 1
    ctx.result["conn_epoch"] = p.conn_epoch
    if not was_connected:
        ctx.emit("player.connected", {"userId": p.user_id})


def _disconnect(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    p = room.player(cmd.user_id or "")
    # A replaced / older connection closing must not knock out the current one.
    if p is None or not p.connected or cmd.data.get("conn_epoch") != p.conn_epoch:
        return
    p.connected = False
    grace_until = ctx.now + ctx.grace_ms
    ctx.schedule(GRACE_TIMER, grace_until, f"{p.user_id}:{p.conn_epoch}")
    ctx.emit("player.disconnected", {"userId": p.user_id, "graceUntil": grace_until})
    if room.game is not None:
        get_handler(room.game.kind).check_progress(room, room.game, ctx)


def _leave(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    _member(room, cmd)
    remove_player(room, cmd.user_id or "", "left", ctx)


def _kick(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    _require_host(room, cmd)
    target = str(cmd.data.get("userId") or "")
    if target == room.host_id:
        raise GameError("invalid_data", "the host cannot kick themselves")
    if room.player(target) is None:
        raise GameError("invalid_data", "no such player")
    room.banned.append(target)
    remove_player(room, target, "kicked", ctx)


def _transfer_host(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    _require_host(room, cmd)
    target = room.player(str(cmd.data.get("userId") or ""))
    if target is None or not target.active:
        raise GameError("invalid_data", "no such player")
    room.host_id = target.user_id
    ctx.emit("host.changed", {"hostId": room.host_id, "reason": "transferred"})


def remove_player(room: RoomState, user_id: str, reason: str, ctx: Ctx) -> None:
    """Take a player out: dropped from the lobby, or marked inactive inside a running game."""
    p = room.player(user_id)
    if p is None:
        return
    if room.status == "lobby" or room.game is None:
        room.players = [x for x in room.players if x.user_id != user_id]
    else:
        if not p.active:
            return
        p.active = False
        p.connected = False
    ctx.emit("player.left", {"userId": user_id, "reason": reason})

    if room.host_id == user_id:
        candidates = sorted(room.active_players(), key=lambda x: (not x.connected, x.joined_at))
        if candidates:
            room.host_id = candidates[0].user_id
            ctx.emit("host.changed", {"hostId": room.host_id, "reason": "host_left"})

    if not room.active_players():
        room.closed = True
        return

    if room.game is not None:
        game = room.game
        get_handler(game.kind).on_player_inactive(room, game, user_id, ctx)
        if room.game is not None and len(room.active_ids()) < required_players(room):
            finish_game(room, ctx, "not_enough_players")


# ---- settings ------------------------------------------------------------------------------
def _update_settings(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    _require_host(room, cmd)
    _require_lobby(room)
    data = cmd.data
    if "mode" in data:
        room.mode = ModeInfo.model_validate(data["mode"]) if data["mode"] else None
        room.settings = {}
        if room.mode is not None:
            room.locale = room.mode.locale  # the chosen game decides the language
    if "locale" in data and not data.get("mode"):  # a concrete game chosen together wins
        locale = data["locale"]
        if not isinstance(locale, str) or not _LOCALE_RE.fullmatch(locale):
            raise GameError("invalid_data", "locale must be a two-letter language code")
        room.locale = locale
        if room.mode is not None and room.mode.locale != locale:
            room.mode, room.settings = None, {}  # that game is in another language
    if "categories" in data:
        cats = data["categories"] or []
        if not isinstance(cats, list) or any(c not in CATEGORIES_ALL for c in cats):
            raise GameError("invalid_data", f"categories must be a subset of {CATEGORIES_ALL}")
        room.categories = sorted(set(cats))
    if "settings" in data:
        patch = data["settings"] or {}
        if not isinstance(patch, dict):
            raise GameError("invalid_data", "settings must be an object")
        merged = {**room.settings, **patch}
        room.settings = {k: v for k, v in merged.items() if v is not None}
    if room.mode is not None:
        handler = get_handler(room.mode.kind)
        handler.parse_settings(effective_settings(room.mode, room.settings))
    ctx.emit(
        "room.settings",
        {
            "mode": mode_view(room.mode),
            "locale": room.locale,
            "categories": room.categories,
            "settings": room.settings,
            "maxPlayers": room.max_players,
        },
    )


# ---- game start / end ----------------------------------------------------------------------
def _start(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    _require_host(room, cmd)
    _require_lobby(room)
    content = GameContent.model_validate(cmd.data["content"])
    mode = content.mode
    handler = get_handler(mode.kind)
    online = room.online_ids()
    need = max(handler.min_players, mode.min_players)
    if len(online) < need:
        raise GameError("not_enough_players", f"at least {need} connected players are needed")
    if len(room.active_ids()) > mode.max_players:
        raise GameError("too_many_players", f"at most {mode.max_players} players")
    settings = handler.parse_settings(effective_settings(mode, room.settings))
    handler.validate_start(room, settings, content)

    room.status = "playing"
    ctx.emit(
        "game.started",
        {
            "kind": mode.kind,
            "mode": mode_view(mode),
            "settings": settings.model_dump(),
            "categories": room.categories,
            "players": [player_view(room, p) for p in room.players],
        },
    )
    game = handler.start(room, content, settings, ctx)
    game.mode = mode
    if room.status == "playing":  # start() may already have finished an empty game
        room.game = game


def _end(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    _require_host(room, cmd)
    if room.game is None:
        raise GameError("no_game", "no game is running")
    finish_game(room, ctx, "host_ended")


# ---- timers --------------------------------------------------------------------------------
def _timer(room: RoomState, cmd: Command, ctx: Ctx) -> None:
    kind = cmd.data.get("kind")
    token = str(cmd.data.get("token"))
    if kind == PHASE_TIMER:
        game = room.game
        if game is None or token != str(game.phase_id):
            return  # stale: the phase already moved on
        get_handler(game.kind).on_timer(room, game, ctx)
    elif kind == GRACE_TIMER:
        user_id, _, epoch = token.rpartition(":")
        p = room.player(user_id)
        if p is None or p.connected or str(p.conn_epoch) != epoch:
            return  # reconnected (or reconnected and dropped again — a newer timer owns it)
        remove_player(room, user_id, "timeout", ctx)


_ROOM_COMMANDS = {
    "room.join": _join,
    "presence.connect": _connect,
    "presence.disconnect": _disconnect,
    "room.leave": _leave,
    "room.kick": _kick,
    "room.transfer_host": _transfer_host,
    "room.update_settings": _update_settings,
    "game.start": _start,
    "game.end": _end,
    "timer": _timer,
}
