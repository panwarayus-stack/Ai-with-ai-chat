"""``/AI <question>`` and ``/Gf <message>`` — OpenRouter chat commands.

Both commands share:

* one :class:`src.services.openrouter.OpenRouterClient`, and
* one :class:`src.lib.ai_queue.AIQueue` (bounded FIFO, exactly one active
  OpenRouter request at a time),

but they keep strictly separate system prompts and per-user conversation
memories (see :class:`src.services.openrouter.ConversationStore`).

Error policy: real errors are reported as-is. The bot never fabricates an
AI answer as a fallback.
"""

from __future__ import annotations

from src.commands.base import Command, CommandContext, CommandResult, register
from src.lib.ai_queue import AIQueueTimeout, QueueFullError
from src.lib.validation import clean_query
from src.services.openrouter import OpenRouterError


def _chat(ctx: CommandContext, args: str, mode: str, usage: str) -> CommandResult:
    prompt = clean_query(args, usage, max_length=ctx.config.ai_max_prompt_chars)

    if not ctx.services.openrouter.configured:
        return CommandResult(
            text=(
                "The AI is not configured on this bot (missing OPENROUTER_API_KEY). "
                "Ask the bot owner to add an OpenRouter key — until then /AI and "
                "/Gf stay disabled rather than faking replies."
            )
        )

    messages = ctx.services.conversations.build_messages(mode, ctx.user_id, prompt)
    try:
        # One shared FIFO queue: /AI and /Gf requests run one at a time.
        answer = ctx.services.ai_queue.submit(
            ctx.services.openrouter.chat,
            messages,
            timeout=ctx.config.ai_request_timeout + 15,
        )
    except QueueFullError as exc:
        return CommandResult(text=str(exc))
    except AIQueueTimeout as exc:
        return CommandResult(
            text=f"{exc} The AI might be busy — please try again shortly."
        )
    except OpenRouterError as exc:
        return CommandResult(text=f"AI error: {exc.message}")

    ctx.services.conversations.record(mode, ctx.user_id, prompt, answer)
    return CommandResult(text=answer)


def handle_ai(ctx: CommandContext, args: str) -> CommandResult:
    return _chat(ctx, args, "ai", "/AI <question>")


def handle_gf(ctx: CommandContext, args: str) -> CommandResult:
    return _chat(ctx, args, "gf", "/Gf <message>")


register(
    Command(
        name="AI",
        handler=handle_ai,
        usage="/AI <question>",
        description="Ask the AI assistant a question (OpenRouter).",
        examples=["/AI explain photosynthesis simply", "/AI what is a REST API?"],
        category="AI",
        troubleshooting="Requires OPENROUTER_API_KEY. See /Help AI.",
    )
)
register(
    Command(
        name="Gf",
        handler=handle_gf,
        usage="/Gf <message>",
        description="Chat with 'Aria', the bot's playful AI companion (separate from /AI).",
        examples=["/Gf hi, how was your day?", "/Gf tell me something cute"],
        category="AI",
        troubleshooting="Requires OPENROUTER_API_KEY. See /Help AI.",
    )
)
