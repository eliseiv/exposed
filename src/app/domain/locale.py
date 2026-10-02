"""Content language: explicit value → ``Accept-Language`` → ``DEFAULT_LOCALE``."""

from __future__ import annotations

from collections.abc import Sequence


def _primary(tag: str) -> str:
    return tag.strip().split(";", 1)[0].strip().split("-", 1)[0].split("_", 1)[0].lower()


def parse_accept_language(header: str | None) -> list[str]:
    """Primary language subtags in preference order (``q`` weights respected, ``*`` dropped)."""
    if not header:
        return []
    weighted: list[tuple[float, int, str]] = []
    for i, part in enumerate(header.split(",")):
        tag = _primary(part)
        if not tag or tag == "*":
            continue
        q = 1.0
        for param in part.split(";")[1:]:
            key, _, value = param.strip().partition("=")
            if key.strip() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        if q > 0:
            weighted.append((-q, i, tag))
    return [tag for _, _, tag in sorted(weighted)]


def resolve_locale(
    explicit: str | None,
    accept_language: str | None,
    supported: Sequence[str],
    default: str,
) -> str:
    """The first supported language among the explicit choice and the client's preferences."""
    candidates = [_primary(explicit)] if explicit else []
    candidates += parse_accept_language(accept_language)
    for tag in candidates:
        if tag in supported:
            return tag
    return default
