"""Vote aggregation shared by the voting games."""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping


def tally(votes: Mapping[str, str], options: Iterable[str]) -> dict[str, int]:
    """Count votes per option. Every option is present (0 votes included); votes for anything
    that is not an option are ignored."""
    counts = dict.fromkeys(options, 0)
    for choice in votes.values():
        if choice in counts:
            counts[choice] += 1
    return counts


def percentages(counts: Mapping[str, int]) -> dict[str, int]:
    """Integer percentages that always sum to 100 (largest remainder), or all 0 with no votes."""
    total = sum(counts.values())
    if total == 0:
        return dict.fromkeys(counts, 0)
    raw = {k: v * 100 / total for k, v in counts.items()}
    floored = {k: int(v) for k, v in raw.items()}
    rest = 100 - sum(floored.values())
    for k in sorted(raw, key=lambda k: raw[k] - floored[k], reverse=True)[:rest]:
        floored[k] += 1
    return floored


def leaders(counts: Mapping[str, int]) -> list[str]:
    """Options with the maximum number of votes (empty when nobody voted)."""
    if not counts:
        return []
    top = max(counts.values())
    if top == 0:
        return []
    return [k for k, v in counts.items() if v == top]


def resolve_tie(tied: list[str], policy: str, rng: random.Random) -> list[str]:
    """``all`` — everybody tied is a target ("both do it"); ``random`` — one of them."""
    if len(tied) <= 1 or policy == "all":
        return list(tied)
    return [rng.choice(tied)]
