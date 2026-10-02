"""Room state — the single JSON document stored in Redis per room.

Everything the game needs is inside it (including the shuffled deck loaded at start), so a command
never touches PostgreSQL while a round is running. Times are epoch milliseconds (``int``).

Mode-specific state lives in ``RoomState.game`` as a discriminated union on ``kind``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

UserId = str


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=False)


class Player(_Model):
    user_id: UserId
    nickname: str
    avatar_id: int | None = None
    avatar_key: str | None = None
    joined_at: int
    connected: bool = False
    # Monotonic per player: a disconnect / grace timer only applies to the connection it saw.
    conn_epoch: int = 0
    # False once the player left or their reconnect grace expired DURING a game (the seat is kept
    # so scores and results stay consistent; the player is dropped when the game ends).
    active: bool = True


class ModeInfo(_Model):
    id: int
    slug: str
    kind: str
    title: str
    min_players: int
    max_players: int
    default_settings: dict[str, Any] = Field(default_factory=dict)
    locale: str = "ru"


class CardData(_Model):
    id: int
    type: str
    category: str
    text: str
    is_anonymous: bool = False
    options_count: int | None = None


class WordData(_Model):
    id: int
    word: str
    hint: str | None = None


class AnswerOption(_Model):
    id: int
    text: str


class GameContent(_Model):
    """Content loaded from PostgreSQL right before ``game.start``."""

    mode: ModeInfo
    cards: list[CardData] = Field(default_factory=list)
    words: list[WordData] = Field(default_factory=list)
    answers: list[AnswerOption] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------
# Per-mode game state
# --------------------------------------------------------------------------------------------
class GameBase(_Model):
    phase: str = "starting"
    # Bumped on every phase change; a timer carries the id it was scheduled for, so a stale
    # timer (the phase already moved on) is ignored.
    phase_id: int = 0
    ends_at: int | None = None
    round: int = 0
    started_at: int = 0
    mode: ModeInfo | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    scores: dict[UserId, int] = Field(default_factory=dict)
    history: list[dict[str, Any]] = Field(default_factory=list)


class QuestionRound(_Model):
    card: CardData
    text: str
    subject_user_id: UserId | None = None
    candidates: list[UserId] = Field(default_factory=list)  # empty for yes_no
    votes: dict[UserId, str] = Field(default_factory=dict)
    result: dict[str, Any] | None = None


class QuestionListGame(GameBase):
    kind: Literal["question_list"] = "question_list"
    deck: list[CardData] = Field(default_factory=list)
    current: QuestionRound | None = None


class WheelItem(_Model):
    category: str  # question | dare | gossip
    text: str
    author_id: UserId


class WheelGame(GameBase):
    kind: Literal["wheel"] = "wheel"
    submissions: dict[UserId, dict[str, str]] = Field(default_factory=dict)
    items: list[WheelItem] = Field(default_factory=list)
    last_spin: dict[str, Any] | None = None
    reveal_at: int | None = None


class BombGame(GameBase):
    kind: Literal["bomb"] = "bomb"
    deck: list[CardData] = Field(default_factory=list)
    deck_pos: int = 0
    holder: UserId | None = None
    prev_holder: UserId | None = None
    question: str | None = None
    return_until: int | None = None
    returned: bool = False
    fuse_ends_at: int | None = None
    last_loser: UserId | None = None


class ImpostorGame(GameBase):
    kind: Literal["impostor"] = "impostor"
    words: list[WordData] = Field(default_factory=list)
    game_no: int = 0
    word: WordData | None = None
    impostors: list[UserId] = Field(default_factory=list)
    alive: list[UserId] = Field(default_factory=list)
    speaking_order: list[UserId] = Field(default_factory=list)
    speaker_idx: int = 0
    votes: dict[UserId, UserId] = Field(default_factory=dict)
    winner: str | None = None


class BlankSubmission(_Model):
    answer_id: str
    author_id: UserId
    text: str


class FillBlankGame(GameBase):
    kind: Literal["fill_blank"] = "fill_blank"
    deck: list[CardData] = Field(default_factory=list)
    answers_pool: list[AnswerOption] = Field(default_factory=list)
    judge: UserId | None = None
    judge_idx: int = -1
    prompt: str | None = None
    dealt: dict[UserId, list[AnswerOption]] = Field(default_factory=dict)
    submissions: dict[UserId, BlankSubmission] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)  # answer ids in the shuffled judging order


class HotSeatGame(GameBase):
    kind: Literal["hot_seat"] = "hot_seat"
    deck: list[CardData] = Field(default_factory=list)
    deck_pos: int = 0
    bag: list[UserId] = Field(default_factory=list)
    player: UserId | None = None
    question: str | None = None


GameState = Annotated[
    QuestionListGame | WheelGame | BombGame | ImpostorGame | FillBlankGame | HotSeatGame,
    Field(discriminator="kind"),
]


class RoomState(_Model):
    code: str
    host_id: UserId
    status: Literal["lobby", "playing"] = "lobby"
    created_at: int
    updated_at: int
    seq: int = 0
    max_players: int = 12
    mode: ModeInfo | None = None  # None = pick a random mode at start
    # Content language of the room: the random mode and the decks are picked in it.
    locale: str = "ru"
    categories: list[str] = Field(default_factory=list)  # empty = all categories
    settings: dict[str, Any] = Field(default_factory=dict)  # host overrides of mode defaults
    players: list[Player] = Field(default_factory=list)
    banned: list[UserId] = Field(default_factory=list)  # kicked players cannot rejoin
    game: GameState | None = None
    # Set by the engine when the last player left; the manager then deletes the room.
    closed: bool = False

    # ---- helpers -----------------------------------------------------------------------------
    def player(self, user_id: UserId) -> Player | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    def active_players(self) -> list[Player]:
        return [p for p in self.players if p.active]

    def active_ids(self) -> list[UserId]:
        return [p.user_id for p in self.players if p.active]

    def online_ids(self) -> list[UserId]:
        """Active AND currently connected — whose input a phase waits for."""
        return [p.user_id for p in self.players if p.active and p.connected]
