"""Game end: leaderboard, archive record, back to the lobby."""

from __future__ import annotations

from typing import Any

from app.domain.game.core import Ctx, brief
from app.domain.game.state import RoomState


def leaderboard(
    room: RoomState, scores: dict[str, int], *, ascending: bool
) -> list[dict[str, Any]]:
    """Ranked players (ties share a rank). ``ascending`` — fewer points is better (penalties)."""
    rows = sorted(scores.items(), key=lambda kv: kv[1] if ascending else -kv[1])
    out: list[dict[str, Any]] = []
    rank = 0
    prev: int | None = None
    for i, (uid, score) in enumerate(rows, start=1):
        if score != prev:
            rank, prev = i, score
        out.append({**(brief(room, uid) or {}), "score": score, "rank": rank})
    return out


def finish_game(room: RoomState, ctx: Ctx, reason: str) -> None:
    """End the running game: emit ``game.finished``, hand the archive to the manager, and return
    the room to the lobby so the same company can play again."""
    from app.domain.game.modes import get_handler  # local: modes import this module

    game = room.game
    if game is None:
        return
    handler = get_handler(game.kind)
    board = (
        leaderboard(room, game.scores, ascending=handler.scores_ascending)
        if handler.has_scores(game)
        else []
    )
    summary = handler.final_summary(room, game)
    ctx.emit(
        "game.finished",
        {"reason": reason, "kind": game.kind, "leaderboard": board, "summary": summary},
    )
    ctx.archive = {
        "room_code": room.code,
        "mode_id": room.mode.id if room.mode else None,
        "mode_kind": game.kind,
        "host_id": room.host_id,
        "finish_reason": reason,
        "settings": game.settings,
        "players": [brief(room, p.user_id) for p in room.players],
        "result": {"leaderboard": board, "summary": summary, "rounds": game.history},
        "started_at": game.started_at,
    }
    room.status = "lobby"
    room.game = None
    # Players who dropped out during the game lose their seat now.
    gone = [p.user_id for p in room.players if not p.active]
    room.players = [p for p in room.players if p.active]
    for uid in gone:
        ctx.emit("player.left", {"userId": uid, "reason": "inactive"})
