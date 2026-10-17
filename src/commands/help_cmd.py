"""``/Help <question>`` — local command help and troubleshooting.

This command is fully local: it never calls OpenRouter or any external API.
It answers from the command registry plus a small troubleshooting index.
"""

from __future__ import annotations

from typing import List, Tuple

from src.commands.base import COMMANDS, Command, CommandContext, CommandResult, register

TROUBLESHOOTING: List[Tuple[str, str, str]] = [
    (
        "ai openrouter api key not configured missing",
        "AI commands (/AI, /Gf)",
        "They need OPENROUTER_API_KEY in the bot environment. Without it the "
        "bot explains the problem instead of inventing an answer. Add a key at "
        "openrouter.ai and set OPENROUTER_MODEL to a ':free' model (or 'openrouter/free') to avoid charges.",
    ),
    (
        "rate limit 429 too many requests slow queue busy",
        "Rate limits and the AI queue",
        "OpenRouter's free tier allows roughly 20 requests per minute. The bot "
        "queues /AI and /Gf one at a time; if the queue is full, wait a minute "
        "and retry. Genderize/Nationalize free quotas also return errors when "
        "exhausted — adding their optional keys raises the quota.",
    ),
    (
        "error crash failed broken not working",
        "A command failed",
        "Every command validates its arguments first — check the Usage line it "
        "shows. If a provider is down the bot reports the real error and never "
        "fakes a reply. Try again shortly; if it persists, check the provider "
        "status pages listed in the project README.",
    ),
    (
        "login session instagram challenge checkpoint logout disconnected",
        "Instagram login problems",
        "The bot reuses a saved session when possible. If Instagram raises a "
        "challenge/checkpoint, approve the login in the Instagram app on a "
        "trusted device and restart the bot — it deliberately does not bypass "
        "challenges or CAPTCHAs. For 2FA accounts set IG_VERIFICATION_CODE to "
        "the one-time code.",
    ),
    (
        "weather city not found location",
        "/Weather can't find a city",
        "Try 'City, Country' (e.g. 'Agra, India'). Spelling matters. The bot "
        "uses the Open-Meteo geocoding API and shows the closest match.",
    ),
    (
        "anime ambiguous not found",
        "/Anime found the wrong show (or none)",
        "Include the full title, e.g. 'Fullmetal Alchemist Brotherhood'. When "
        "several shows match, the bot lists the alternatives it found so you "
        "can pick the exact one.",
    ),
    (
        "pic image nsfw safe male female",
        "/Pic content policy",
        "/Pic never returns explicit content (the provider request is limited "
        "to SFW artwork). It mixes normal and mildly suggestive non-explicit "
        "art. Use /Pic male or /Pic female to choose; /Pic is random.",
    ),
    (
        "sherlock username search slow",
        "/Sherlock is slow or finds nothing",
        "The bot checks public pages only, on a curated list of popular sites "
        "by default (set SHERLOCK_SITES to change it). A hit means the public "
        "page exists — not that one person owns every account. Patience helps: "
        "each site gets a real network timeout.",
    ),
    (
        "num id paused disabled",
        "Where are /Num and /Id?",
        "Those two commands are paused: their providers are not integrated "
        "yet, so they return a notice and make no network calls. They will "
        "return when a vetted provider is connected.",
    ),
    (
        "deploy voroa hosting env environment variable setup install",
        "Deploying on Voroa",
        "Create a Background Worker with start command 'python app.py'. Voroa "
        "installs requirements.txt automatically. Set IG_USERNAME, IG_PASSWORD "
        "and the optional keys in the service's Environment tab. See the "
        "README 'Voroa deployment' section for session persistence details.",
    ),
    (
        "ship score deterministic",
        "/Ship score",
        "The compatibility score is deterministic fun only: the same two names "
        "always get the same score (it is a hash, not a real measure of "
        "anything!).",
    ),
]


def _score(query: str, keywords: str) -> int:
    query_words = [w for w in query.lower().split() if len(w) > 2]
    if not query_words:
        return 0
    haystack = keywords.lower()
    return sum(1 for word in query_words if word in haystack)


def handle_help(ctx: CommandContext, args: str) -> CommandResult:
    query = (args or "").strip()
    if not query:
        return CommandResult(text=_full_listing())

    best = max(TROUBLESHOOTING, key=lambda entry: _score(query, entry[0]), default=None)
    if best is None or _score(query, best[0]) == 0:
        # Fall back to command-name matching ("how do I use ship?").
        for command in COMMANDS.values():
            if command.key in query.lower():
                return CommandResult(text=command.help_text())
        return CommandResult(
            text=(
                "I don't have a help topic for that. Try one of these keywords: "
                "AI, rate limits, Instagram login, weather, anime, pic, sherlock, "
                "deploy — or send /Help with no text for the full command list."
            )
        )

    # Also show the matching command's usage when the question names one.
    extra = ""
    for command in COMMANDS.values():
        if command.key in query.lower():
            extra = "\n\n" + command.help_text()
            break
    _, title, body = best
    return CommandResult(text=f"{title}\n\n{body}{extra}")


def _full_listing() -> str:
    lines = ["Instagram Bot — active commands", ""]
    categories: dict = {}
    for command in COMMANDS.values():
        categories.setdefault(command.category, []).append(command)
    for category in sorted(categories):
        lines.append(f"[{category}]")
        for command in sorted(categories[category], key=lambda c: c.name.lower()):
            lines.append(f"• /{command.name} — {command.description}")
        lines.append("")
    lines.append("Send /Help <question> for troubleshooting (e.g. '/Help login').")
    return "\n".join(lines).rstrip()


register(
    Command(
        name="Help",
        handler=handle_help,
        usage="/Help <question>  (or /Help for the full list)",
        description="List every command, or get usage and troubleshooting for a question.",
        examples=["/Help", "/Help how do I fix an AI error?", "/Help weather city not found"],
        category="Bot",
    )
)
