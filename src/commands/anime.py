"""``/Anime <anime name>`` — anime details (AniList primary, Jikan fallback).

The reply includes title(s), synopsis, genres, release info, status, episode
count, main characters, and relations split into real seasons (prequels /
sequels) vs. related works (spin-offs, side stories, movies, ...). The poster
is sent as an actual image. Episode lists and watch orders are never invented.
"""

from __future__ import annotations

import logging

from src.commands.base import Command, CommandContext, CommandResult, register
from src.lib.validation import clean_query
from src.services.anime import AnimeError, render_result

logger = logging.getLogger(__name__)


def handle_anime(ctx: CommandContext, args: str) -> CommandResult:
    query = clean_query(args, "/Anime <anime name>", max_length=100)
    try:
        outcome = ctx.services.anime.search(query)
    except AnimeError as exc:
        return CommandResult(text=exc.message)

    if outcome.result is None:
        text = outcome.provider_note or f"No anime found for {query!r}."
        if outcome.candidates:
            text += "\nDid you mean: " + ", ".join(outcome.candidates) + "?"
        return CommandResult(text=text)

    text = render_result(outcome.result)
    if outcome.candidates:
        text += "\n\nOther matches: " + ", ".join(outcome.candidates)
    if outcome.provider_note:
        text += "\n" + outcome.provider_note

    image = None
    if outcome.result.poster_url:
        try:
            image = ctx.services.images.download(outcome.result.poster_url)
        except Exception as exc:
            logger.info("Poster download failed (%s); sending text only", exc)

    return CommandResult(text=text, image=image)


register(
    Command(
        name="Anime",
        handler=handle_anime,
        usage="/Anime <anime name>",
        description="Look up an anime: synopsis, genres, episodes, characters, seasons and related works.",
        examples=["/Anime Frieren", "/Anime Attack on Titan", "/Anime Your Name"],
        category="Lookups",
        troubleshooting="Include the full title if several shows match — the bot lists alternatives.",
    )
)
