"""Command handlers and the active command registry.

Importing :func:`load_all` registers every active command exactly once.
Paused commands (:mod:`src.commands.paused`) are intentionally excluded from
the registry.
"""

from __future__ import annotations

_LOADED = False


def load_all() -> None:
    """Import every command module so its ``register(...)`` call runs."""
    global _LOADED
    if _LOADED:
        return
    from src.commands import (  # noqa: F401 - imports register the commands
        ai,
        anime,
        fun,
        gender,
        help_cmd,
        info,
        pic,
        sherlock_cmd,
        weather,
        wiki,
    )

    _LOADED = True
