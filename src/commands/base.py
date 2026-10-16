"""Command framework: context, result, registry and dispatch.

Commands are plain callables ``(ctx, args) -> CommandResult`` registered with
metadata (usage, description, examples) so /Help can document them and tests
can enumerate them. Dispatch is case-insensitive (``/Info`` == ``/info``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from src.lib.formatting import chunk_message
from src.lib.validation import CommandInputError, parse_command

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """What a command wants to send back.

    ``text`` is plain Instagram DM text (chunked automatically). ``image`` is
    raw image bytes sent as a DM photo before the text.
    """

    text: str = ""
    image: Optional[bytes] = None
    image_extension: str = ".jpg"

    def message_chunks(self) -> List[str]:
        return chunk_message(self.text)


@dataclass
class CommandContext:
    """Everything a handler needs (services + config + sender identity)."""

    services: "Services"
    user_id: int = 0
    username: str = ""
    config: object = None

    def reply(self, text: str) -> "CommandResult":
        return CommandResult(text=text)


@dataclass
class Services:
    """Wired service container (built once in app.build_application)."""

    openrouter: object = None
    ai_queue: object = None
    conversations: object = None
    demographics: object = None
    weather: object = None
    anime: object = None
    images: object = None
    wikipedia: object = None
    instagram: object = None
    sherlock: object = None
    store: object = None


@dataclass
class Command:
    name: str  # canonical name, e.g. "Info"
    handler: Callable[[CommandContext, str], CommandResult]
    usage: str
    description: str
    examples: List[str] = field(default_factory=list)
    category: str = "General"
    troubleshooting: str = ""

    @property
    def key(self) -> str:
        return self.name.lower()

    def help_text(self) -> str:
        lines = [f"/{self.name} — {self.description}", f"Usage: {self.usage}"]
        if self.examples:
            lines.append("Examples: " + " | ".join(self.examples))
        return "\n".join(lines)


# The active command registry (paused commands live in commands.paused).
COMMANDS: Dict[str, Command] = {}


def register(command: Command) -> Command:
    if command.key in COMMANDS:
        raise ValueError(f"Duplicate command registration: /{command.name}")
    COMMANDS[command.key] = command
    return command


def get_command(name: str) -> Optional[Command]:
    return COMMANDS.get((name or "").lower())


def active_names() -> List[str]:
    return sorted((c.name for c in COMMANDS.values()), key=str.lower)


def dispatch(ctx: CommandContext, text: str) -> Optional[CommandResult]:
    """Route one message to its handler.

    Returns ``None`` when the message is not a command. Never raises for
    user errors — those become helpful :class:`CommandResult` messages.
    """
    from src.commands import load_all
    from src.commands.paused import paused_response  # local import avoids cycle

    load_all()  # ensure every handler module is registered (idempotent)
    name, args = parse_command(text)
    if not name:
        return None

    command = get_command(name)
    if command is None:
        paused = paused_response(name)
        if paused is not None:
            return CommandResult(text=paused)
        return CommandResult(
            text=(
                f"Unknown command '/{name}'. Send /Help for the list of commands."
            )
        )

    try:
        return command.handler(ctx, args)
    except CommandInputError as exc:
        return CommandResult(text=exc.message)
    except Exception:
        logger.exception("Command /%s crashed", command.name)
        return CommandResult(
            text=f"Something went wrong while running /{command.name}. "
            "Please try again later."
        )
