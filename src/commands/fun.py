"""Local entertainment commands: /Roast, /Ship, /Truth, /Dare, /Quote.

All five are fully local — data lives in ``data/`` and nothing here calls
OpenRouter or any external API. Roasts are playful and non-hateful; dares are
safe; quotes carry honest attribution.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import List

from src.commands.base import Command, CommandContext, CommandResult, register
from src.lib.utils import normalize_name
from src.lib.validation import clean_name, clean_two_names

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _load(filename: str) -> List[dict]:
    path = DATA_DIR / filename
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------- #
# /Roast
# ---------------------------------------------------------------------- #
def handle_roast(ctx: CommandContext, args: str) -> CommandResult:
    name = clean_name(args, "/Roast <name>")
    templates = _load("roasts.json")
    template = random.choice(templates)["template"]
    text = template.format(name=name)
    return CommandResult(text=f"{text}\n(Just playful fun — no hard feelings!)")


# ---------------------------------------------------------------------- #
# /Ship
# ---------------------------------------------------------------------- #
def _ship_score(name1: str, name2: str) -> int:
    """Deterministic entertainment-only score in [0, 100]."""
    import hashlib

    pair = "|".join(sorted((normalize_name(name1).casefold(), normalize_name(name2).casefold())))
    digest = hashlib.sha256(pair.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 101


def _ship_verdict(score: int) -> str:
    if score < 20:
        return "Just friends — and that's okay!"
    if score < 40:
        return "There's a spark… somewhere."
    if score < 60:
        return "Honestly? It could go either way."
    if score < 80:
        return "Now we're talking! Strong potential."
    return "Match made in heaven (allegedly)."


def handle_ship(ctx: CommandContext, args: str) -> CommandResult:
    name1, name2 = clean_two_names(args, "/Ship <name1> <name2>")
    score = _ship_score(name1, name2)
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    return CommandResult(
        text=(
            f"Shipping {name1} + {name2}\n"
            f"Compatibility: {score}/100 [{bar}]\n"
            f"{_ship_verdict(score)}\n"
            "(Deterministic entertainment only — the same pair always scores the same!)"
        )
    )


# ---------------------------------------------------------------------- #
# /Truth, /Dare, /Quote
# ---------------------------------------------------------------------- #
def handle_truth(ctx: CommandContext, args: str) -> CommandResult:
    questions = _load("truths.json")
    item = random.choice(questions)
    return CommandResult(text=f"Truth question (#{item['id']}):\n{item['question']}")


def handle_dare(ctx: CommandContext, args: str) -> CommandResult:
    dares = _load("dares.json")
    item = random.choice(dares)
    return CommandResult(
        text=f"Dare ({item.get('difficulty', 'easy')}):\n{item['dare']}\n(Skip anything you're not comfortable with.)"
    )


def handle_quote(ctx: CommandContext, args: str) -> CommandResult:
    quotes = _load("quotes.json")
    item = random.choice(quotes)
    text = f"“{item['text']}”"
    if item.get("author"):
        text += f"\n— {item['author']}"
        if item.get("source"):
            text += f", {item['source']}"
    elif item.get("source"):
        text += f"\n— {item['source']}"
    return CommandResult(text=text)


register(
    Command(
        name="Roast",
        handler=handle_roast,
        usage="/Roast <name>",
        description="A playful, good-natured roast (local templates, no AI).",
        examples=["/Roast Alex", "/Roast my exam"],
        category="Fun",
    )
)
register(
    Command(
        name="Ship",
        handler=handle_ship,
        usage="/Ship <name1> <name2>",
        description="Deterministic, entertainment-only compatibility score for two names.",
        examples=["/Ship Alex Sam", "/Ship pizza pineapple"],
        category="Fun",
    )
)
register(
    Command(
        name="Truth",
        handler=handle_truth,
        usage="/Truth",
        description="Random truth question from the local question bank.",
        examples=["/Truth"],
        category="Fun",
    )
)
register(
    Command(
        name="Dare",
        handler=handle_dare,
        usage="/Dare",
        description="Random safe dare from the local dare bank (always optional).",
        examples=["/Dare"],
        category="Fun",
    )
)
register(
    Command(
        name="Quote",
        handler=handle_quote,
        usage="/Quote",
        description="Motivational, funny and anime quotes with honest attribution.",
        examples=["/Quote"],
        category="Fun",
    )
)
