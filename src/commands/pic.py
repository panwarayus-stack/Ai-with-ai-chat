"""``/Pic [male|female]`` — random SFW anime artwork.

Content policy (enforced in :mod:`src.services.images`):

* explicit/adult content is excluded at the API level (``IsNsfw=False``);
* mildly suggestive, non-explicit art is mixed in occasionally;
* male artwork comes from nekos.best (waifu.im has no male category);
* the actual image is sent, with its source URL and — only when the provider
  verifies them — character / anime / artist names.
"""

from __future__ import annotations

import logging

from src.commands.base import Command, CommandContext, CommandResult, register
from src.lib.validation import clean_choice
from src.services.images import ImageError

logger = logging.getLogger(__name__)

_GENDERS = {
    "male": "male",
    "m": "male",
    "boy": "male",
    "female": "female",
    "f": "female",
    "girl": "female",
    "random": "random",
    "": "random",
}


def handle_pic(ctx: CommandContext, args: str) -> CommandResult:
    gender = clean_choice(args, _GENDERS, "/Pic [male|female|random]", default="random")
    try:
        artwork = ctx.services.images.random_artwork(gender)
    except ImageError as exc:
        return CommandResult(text=exc.message)

    try:
        image = ctx.services.images.download(artwork.image_url)
    except ImageError as exc:
        return CommandResult(text=exc.message)

    caption = "\n".join(artwork.caption_lines())
    if artwork.note:
        caption += ("\n" if caption else "") + artwork.note
    return CommandResult(text=caption, image=image)


register(
    Command(
        name="Pic",
        handler=handle_pic,
        usage="/Pic [male|female|random]",
        description="Random SFW anime artwork (normal + mildly suggestive, never explicit).",
        examples=["/Pic", "/Pic female", "/Pic male"],
        category="Images",
        troubleshooting="Images come from waifu.im (female) and nekos.best (male). See /Help pic.",
    )
)
