"""``/Sherlock <username>`` — public username search via the Sherlock project."""

from __future__ import annotations

from src.commands.base import Command, CommandContext, CommandResult, register
from src.lib.validation import clean_username
from src.services.sherlock import SherlockError

DISCLAIMER = (
    "Note: matches mean the public page exists — they do not prove one person "
    "owns every account. Only publicly accessible pages are checked."
)


def handle_sherlock(ctx: CommandContext, args: str) -> CommandResult:
    username = clean_username(args, "/Sherlock <username>", sherlock=True)
    try:
        matches = ctx.services.sherlock.lookup(username)
    except SherlockError as exc:
        return CommandResult(text=exc.message)

    if not matches:
        return CommandResult(
            text=(
                f"No public profiles found for {username!r} on the checked sites.\n\n"
                + DISCLAIMER
            )
        )

    lines = [f"Public pages found for {username!r}:"]
    for match in matches[:25]:
        lines.append(f"• {match.site}: {match.url}")
    if len(matches) > 25:
        lines.append(f"…and {len(matches) - 25} more.")
    lines.append("")
    lines.append(DISCLAIMER)
    return CommandResult(text="\n".join(lines))


register(
    Command(
        name="Sherlock",
        handler=handle_sherlock,
        usage="/Sherlock <username>",
        description="Search public sites for a username (Sherlock project). Returns profile URLs.",
        examples=["/Sherlock torvalds", "/Sherlock drake"],
        category="Lookups",
        troubleshooting="Checks public pages only. Slow by design — each site gets a real timeout.",
    )
)
