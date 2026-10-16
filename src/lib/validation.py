"""Command input parsing and validation.

Every command handler funnels user text through these helpers so that bad
input produces a helpful message instead of an exception or a raw API call.
"""

from __future__ import annotations

import re
from typing import Tuple

MAX_TEXT_LENGTH = 600

# Instagram usernames: letters, digits, periods, underscores, 1-30 chars.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
# Names of people ("John", "Mary Jane", "O'Connor-Smith").
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{0,58}[A-Za-z.]$|^[A-Za-z]$")
# City / place names allow a slightly wider charset.
_PLACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'\-()]{0,78}$")
# Sherlock's own username rules (broader than Instagram's).
_SHERLOCK_RE = re.compile(r"^[A-Za-z0-9._\-]{1,40}$")
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-()]{5,24}$")


class CommandInputError(Exception):
    """User-facing validation failure with a ready-to-send message."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def parse_command(text: str) -> Tuple[str, str]:
    """Split ``/Command rest of line`` into (canonical_name, args).

    The command name is case-insensitive: ``/Info``, ``/info`` and ``/INFO``
    all resolve to ``info``. Returns ("", "") when the text is not a command.
    """
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return "", ""
    head, _, rest = raw.partition(" ")
    name = head[1:].strip().lower()
    # Allow accidental trailing punctuation ("/Help?" -> "help").
    name = name.rstrip("?!.,:;")
    if not name or not name.replace("_", "").isalnum():
        return "", ""
    return name, rest.strip()


def require_args(args: str, usage: str, minimum: int = 1) -> str:
    """Return stripped args or raise with a usage hint."""
    cleaned = (args or "").strip()
    provided = len(cleaned.split()) if cleaned else 0
    if provided < minimum or not cleaned:
        raise CommandInputError(f"Missing arguments. Usage: {usage}")
    return cleaned


def clean_query(args: str, usage: str, max_length: int = 120) -> str:
    """Validate a free-text query (search terms, anime titles, questions)."""
    cleaned = require_args(args, usage)
    if len(cleaned) > max_length:
        raise CommandInputError(
            f"That input is too long ({len(cleaned)} characters). "
            f"Keep it under {max_length} characters. Usage: {usage}"
        )
    if not any(ch.isalnum() for ch in cleaned):
        raise CommandInputError(f"Please include some letters or numbers. Usage: {usage}")
    return cleaned


def clean_name(args: str, usage: str) -> str:
    """Validate a person's name."""
    cleaned = require_args(args, usage)
    if len(cleaned) > 60:
        raise CommandInputError("That name is too long. Keep it under 60 characters.")
    if not _NAME_RE.match(cleaned):
        raise CommandInputError(
            "Please use a plain name (letters, spaces, hyphens or apostrophes). "
            f"Usage: {usage}"
        )
    return cleaned


def clean_city(args: str, usage: str) -> str:
    """Validate a city / place name."""
    cleaned = require_args(args, usage)
    if len(cleaned) > 80:
        raise CommandInputError("That city name is too long. Usage: {usage}")
    if not _PLACE_RE.match(cleaned):
        raise CommandInputError(
            "Please use a plain city name (letters, numbers, spaces, '-'). "
            f"Usage: {usage}"
        )
    return cleaned


def clean_username(args: str, usage: str, sherlock: bool = False) -> str:
    """Validate a username (Instagram or cross-site)."""
    cleaned = require_args(args, usage)
    cleaned = cleaned.lstrip("@")
    pattern = _SHERLOCK_RE if sherlock else _USERNAME_RE
    if not pattern.match(cleaned):
        hint = (
            "Usernames may contain letters, numbers, '.', '_' or '-' (max 40)."
            if sherlock
            else "Instagram usernames may contain letters, numbers, '.' and '_' (max 30)."
        )
        raise CommandInputError(f"{hint} Usage: {usage}")
    return cleaned


def clean_two_names(args: str, usage: str) -> Tuple[str, str]:
    """Validate ``/Ship <name1> <name2>`` input."""
    cleaned = require_args(args, usage, minimum=1)
    parts = cleaned.split()
    if len(parts) < 2:
        raise CommandInputError(
            f"I need two names to ship. Usage: {usage}"
        )
    # Everything up to the last word is name 1 (allow multi-word names),
    # the final word is name 2 — keeps parsing unambiguous.
    name1 = " ".join(parts[:-1])
    name2 = parts[-1]
    for label, name in (("First name", name1), ("Second name", name2)):
        if len(name) > 30 or not re.match(r"^[A-Za-z][A-Za-z .'\-]{0,28}[A-Za-z.]$|^[A-Za-z]$", name):
            raise CommandInputError(
                f"{label} must be a plain name (max 30 characters). Usage: {usage}"
            )
    return name1, name2


def clean_phone(args: str, usage: str) -> str:
    """Validate a phone number for the paused /Num command."""
    cleaned = require_args(args, usage)
    if not _PHONE_RE.match(cleaned):
        raise CommandInputError(
            "Please provide a phone number in international format, e.g. +14155552671. "
            f"Usage: {usage}"
        )
    return cleaned


def clean_choice(args: str, allowed: dict, usage: str, default: str) -> str:
    """Pick a keyword from *allowed* (alias -> canonical) or use *default*."""
    token = (args or "").strip().split(" ")[0].lower() if (args or "").strip() else ""
    if not token:
        return default
    if token in allowed:
        return allowed[token]
    raise CommandInputError(
        f"Unknown option {token!r}. Allowed: {', '.join(sorted(set(allowed.values())))}. "
        f"Usage: {usage}"
    )
