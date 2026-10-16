"""``/Wiki <search term>`` and ``/Celeb <full name>`` — Wikipedia lookups.

Both commands share one :class:`src.services.wikipedia.WikipediaService`
(search client + Commons image resolver). Replies always include the article
URL and image attribution when an image is shown. When nothing reliable is
found, the bot says so instead of inventing facts.
"""

from __future__ import annotations

import logging

from src.commands.base import Command, CommandContext, CommandResult, register
from src.lib.formatting import format_synopsis
from src.lib.validation import clean_query
from src.services.wikipedia import WikipediaError

logger = logging.getLogger(__name__)


def _image_block(article) -> str:
    if not article.image.url:
        return ""
    credit = "Image: Wikimedia Commons"
    bits = []
    if article.image.artist:
        bits.append(f"artist {article.image.artist}")
    if article.image.license:
        bits.append(article.image.license)
    if bits:
        credit += " (" + ", ".join(bits) + ")"
    if article.image.description_url:
        credit += f" — {article.image.description_url}"
    return credit


def handle_wiki(ctx: CommandContext, args: str) -> CommandResult:
    query = clean_query(args, "/Wiki <search term>", max_length=120)
    try:
        article = ctx.services.wikipedia.resolve(query)
    except WikipediaError as exc:
        return CommandResult(text=exc.message)

    if article is None:
        return CommandResult(
            text=f"No Wikipedia article found for {query!r}. Try different keywords."
        )

    if article.is_disambiguation:
        return CommandResult(
            text=(
                f"'{query}' matches several Wikipedia topics. "
                f"Open the disambiguation page to choose: {article.url}"
            )
        )

    lines = [article.display_title]
    if article.description:
        lines.append(f"({article.description})")
    lines.append("")
    lines.append(format_synopsis(article.extract, limit=700) or "No summary available.")
    lines.append("")
    lines.append(f"Read more: {article.url}")
    credit = _image_block(article)
    if credit:
        lines.append(credit)

    image = None
    if article.image.url:
        try:
            image = ctx.services.images.download(article.image.url)
        except Exception as exc:
            logger.info("Lead image download failed (%s)", exc)

    return CommandResult(text="\n".join(lines), image=image)


def handle_celeb(ctx: CommandContext, args: str) -> CommandResult:
    name = clean_query(args, "/Celeb <full name>", max_length=80)
    try:
        lookup = ctx.services.wikipedia.find_person(name)
    except WikipediaError as exc:
        return CommandResult(text=exc.message)

    if lookup.article is None:
        text = lookup.note or f"No reliable Wikipedia result for {name!r}."
        if lookup.candidates:
            text += "\n" + "\n".join(f"• {title}" for title in lookup.candidates)
        return CommandResult(text=text)

    article = lookup.article
    lines = []
    if lookup.note:
        lines.append(lookup.note)
    lines.append(article.display_title)
    if article.description:
        lines.append(f"({article.description})")
    lines.append("")
    lines.append(format_synopsis(article.extract, limit=700) or "No summary available.")
    lines.append("")
    lines.append(f"Read more: {article.url}")
    credit = _image_block(article)
    if credit:
        lines.append(credit)

    image = None
    if article.image.url:
        try:
            image = ctx.services.images.download(article.image.url)
        except Exception as exc:
            logger.info("Biography image download failed (%s)", exc)

    return CommandResult(text="\n".join(lines), image=image)


register(
    Command(
        name="Wiki",
        handler=handle_wiki,
        usage="/Wiki <search term>",
        description="Wikipedia summary, key content, lead image and source link.",
        examples=["/Wiki Taj Mahal", "/Wiki Python programming"],
        category="Lookups",
    )
)
register(
    Command(
        name="Celeb",
        handler=handle_celeb,
        usage="/Celeb <full name>",
        description="Biography and public photo of a notable person, straight from Wikipedia.",
        examples=["/Celeb A. P. J. Abdul Kalam", "/Celeb Marie Curie"],
        category="Lookups",
        troubleshooting="The bot never guesses identities — if Wikipedia has no reliable entry it says so.",
    )
)
