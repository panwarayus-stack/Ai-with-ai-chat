"""OpenRouter chat-completions client for /AI and /Gf.

Docs: https://openrouter.ai/docs

Both commands share ONE :class:`OpenRouterClient` and ONE
:class:`src.lib.ai_queue.AIQueue` (see the wiring in :mod:`app`), so only a
single OpenRouter request is ever in flight. What differs between /AI and
/Gf is the **system prompt** and the **conversation memory** — those are
kept strictly separate per mode and per user.

Failures are raised as :class:`OpenRouterError` with a human-readable
message. The bot never substitutes a fake answer.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict, deque
from typing import Deque, List, Optional, Tuple

from src.lib.http import HttpClient, HttpError, HttpStatusError

logger = logging.getLogger(__name__)

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPTS = {
    "ai": (
        "You are a helpful, concise assistant inside an Instagram bot. "
        "Answer accurately, keep replies under 200 words, and use plain text "
        "(no markdown tables or code fences). If you are unsure, say so."
    ),
    "gf": (
        "You are 'Aria', a warm, playful and supportive chat companion inside "
        "an Instagram bot. Chat in a friendly, slightly flirty but always "
        "respectful and non-explicit style. Keep replies short and natural, "
        "like a text message. Never claim to be a real person or an official "
        "Instagram service; if asked, remind the user you are an AI companion."
    ),
}

# Free-tier models rotate; when the configured model is gone the API says so.
_MODEL_HINT = (
    "set OPENROUTER_MODEL to a currently listed free model "
    "(see https://openrouter.ai/models — search for ':free')."
)


class OpenRouterError(Exception):
    """User-facing OpenRouter failure (never a fake fallback)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class OpenRouterClient:
    """One shared OpenRouter HTTP client."""

    def __init__(
        self,
        api_key: str,
        model: str,
        site_url: str = "",
        app_name: str = "InstagramBot",
        timeout: float = 60.0,
        http: Optional[HttpClient] = None,
    ):
        self.api_key = api_key
        self.model = model
        self.site_url = site_url
        self.app_name = app_name
        self.timeout = timeout
        self.http = http or HttpClient(timeout=timeout, retries=1)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: List[dict], *, temperature: float = 0.8) -> str:
        """Run one chat completion and return the assistant text.

        Raises :class:`OpenRouterError` for every failure mode, with a
        message that is safe to show to end users.
        """
        if not self.configured:
            raise OpenRouterError(
                "The AI is not configured on this bot (missing OPENROUTER_API_KEY). "
                "Ask the bot owner to add an OpenRouter key."
            )
        if not messages:
            raise OpenRouterError("Nothing to send to the AI.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-OpenRouter-Title"] = self.app_name

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 600,
        }
        try:
            data = self.http.post_json(
                OPENROUTER_ENDPOINT,
                payload,
                headers=headers,
                timeout=self.timeout,
            )
        except HttpStatusError as exc:
            raise self._map_http_error(exc) from exc
        except HttpError as exc:
            raise OpenRouterError(
                f"Could not reach OpenRouter ({exc}). Please try again shortly."
            ) from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("Unexpected OpenRouter payload shape: %s", type(data))
            raise OpenRouterError(
                "OpenRouter returned an unexpected response. Please try again."
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise OpenRouterError("The AI returned an empty answer. Please try again.")
        return content.strip()

    # ------------------------------------------------------------------ #
    def _map_http_error(self, exc: HttpStatusError) -> OpenRouterError:
        if exc.status == 401:
            return OpenRouterError(
                "OpenRouter rejected the API key (401). "
                "Check OPENROUTER_API_KEY in the bot configuration."
            )
        if exc.status == 402:
            return OpenRouterError(
                "This OpenRouter account is out of credits (402). "
                "Top up credits or switch OPENROUTER_MODEL to a ':free' model."
            )
        if exc.status == 429:
            return OpenRouterError(
                "OpenRouter rate limit reached (the free tier allows roughly "
                "20 requests per minute). Please wait a moment and try again."
            )
        if exc.status in (400, 404) and "model" in exc.body.lower():
            return OpenRouterError(
                f"The configured AI model {self.model!r} is unavailable: {_MODEL_HINT}"
            )
        if exc.status >= 500:
            return OpenRouterError(
                "OpenRouter is temporarily unavailable (server error). "
                "Please try again shortly."
            )
        return OpenRouterError(f"OpenRouter request failed (HTTP {exc.status}).")


class ConversationStore:
    """Bounded, thread-safe conversation memory.

    Memory is keyed by ``(mode, user_id)`` so /AI and /Gf never share
    context. Each key keeps at most ``memory_turns`` user/assistant
    exchanges, and at most ``max_users`` keys are kept (oldest evicted).
    """

    def __init__(self, memory_turns: int = 4, max_users: int = 200):
        self.memory_turns = max(0, int(memory_turns))
        self.max_users = max(1, int(max_users))
        self._lock = threading.Lock()
        self._memory: "OrderedDict[Tuple[str, int], Deque[dict]]" = OrderedDict()

    def build_messages(self, mode: str, user_id: int, prompt: str) -> List[dict]:
        """Return the full message list (system + history + new prompt)."""
        if mode not in SYSTEM_PROMPTS:
            raise ValueError(f"Unknown conversation mode: {mode}")
        key = (mode, int(user_id))
        with self._lock:
            history = self._memory.get(key)
            if history is None:
                history = deque(maxlen=max(1, self.memory_turns * 2))
                self._memory[key] = history
                self._evict_locked()
            else:
                self._memory.move_to_end(key)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPTS[mode]},
                *list(history),
                {"role": "user", "content": prompt},
            ]
        return messages

    def record(self, mode: str, user_id: int, prompt: str, answer: str) -> None:
        """Append a completed exchange to the memory."""
        key = (mode, int(user_id))
        with self._lock:
            history = self._memory.get(key)
            if history is None:
                history = deque(maxlen=max(1, self.memory_turns * 2))
                self._memory[key] = history
            history.append({"role": "user", "content": prompt})
            history.append({"role": "assistant", "content": answer})
            self._memory.move_to_end(key)
            self._evict_locked()

    def clear(self, mode: str, user_id: int) -> None:
        with self._lock:
            self._memory.pop((mode, int(user_id)), None)

    def size(self) -> int:
        with self._lock:
            return len(self._memory)

    def _evict_locked(self) -> None:
        while len(self._memory) > self.max_users:
            self._memory.popitem(last=False)
