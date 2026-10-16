"""``/Info`` — information about the authenticated bot account only."""

from __future__ import annotations

from src.commands.base import Command, CommandContext, CommandResult, register
from src.lib.utils import format_count


def handle_info(ctx: CommandContext, args: str) -> CommandResult:
    # Deliberately ignores args: only the *authenticated* account is shown.
    try:
        snap = ctx.services.instagram.account_snapshot()
    except Exception as exc:  # never crash the bot on an API hiccup
        return CommandResult(
            text=f"Could not read the bot account info right now ({exc}). "
            "Try again shortly."
        )

    lines = [
        f"Bot account: @{snap.get('username', '?')}",
    ]
    if snap.get("full_name"):
        lines.append(f"Name: {snap['full_name']}")
    lines.append(
        "Followers: {} • Following: {} • Posts: {}".format(
            format_count(snap.get("follower_count")),
            format_count(snap.get("following_count")),
            format_count(snap.get("media_count")),
        )
    )
    lines.append(
        f"Private: {'yes' if snap.get('is_private') else 'no'} • "
        f"Verified: {'yes' if snap.get('is_verified') else 'no'}"
    )
    if snap.get("biography"):
        lines.append(f"Bio: {snap['biography'][:200]}")
    lines.append("")
    lines.append("This is the account the bot is logged in as. No other profile data is shown.")
    return CommandResult(text="\n".join(lines))


register(
    Command(
        name="Info",
        handler=handle_info,
        usage="/Info",
        description="Show info about the account this bot is logged in as (and nothing else).",
        examples=["/Info"],
        category="Bot",
    )
)
