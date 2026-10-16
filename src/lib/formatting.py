"""Response formatting helpers.

Instagram direct messages are plain text. Formatting stays conservative:
short lines, bullets, hard length caps, and chunking so long answers split
into several readable messages instead of being rejected by Instagram.
"""

from __future__ import annotations

from typing import List

from src.lib.utils import ellipsize

# Instagram DM text limit is 1000 characters; stay safely below it.
MESSAGE_LIMIT = 900


def chunk_message(text: str, limit: int = MESSAGE_LIMIT) -> List[str]:
    """Split *text* into message-sized chunks at line boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current = ""
    for line in text.splitlines():
        # Hard-split single oversized lines.
        while len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}".lstrip("\n") if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current.rstrip())
            current = line
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def bullet(lines: List[str]) -> str:
    return "\n".join(f"• {line}" for line in lines if line)


def paragraph_block(*paragraphs: str) -> str:
    return "\n\n".join(p.strip() for p in paragraphs if p and p.strip())


def format_synopsis(text: str, limit: int = 420) -> str:
    """Clean wiki/AniList markup artifacts and cap the length."""
    cleaned = (text or "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    for tag in ("<i>", "</i>", "<em>", "</em>", "<b>", "</b>", "<strong>", "</strong>", "<p>", "</p>"):
        cleaned = cleaned.replace(tag, "")
    cleaned = cleaned.replace("&amp;", "&").replace("&quot;", '"').replace("&#039;", "'")
    return ellipsize(cleaned, limit)


def format_kv(pairs) -> str:
    """Render (label, value) pairs as aligned-ish bullets."""
    return "\n".join(f"• {label}: {value}" for label, value in pairs if value not in (None, ""))
