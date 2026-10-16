"""Paused commands: /Num and /Id — disabled placeholders, no network calls.

Both commands are **not** registered in the active command registry and are
**not** listed in /Help. When a user sends them anyway, :func:`paused_response`
returns a clear "currently paused" message.

===========================================================================
TODO (future provider integration)
===========================================================================

``/Num <number>`` — phone-number lookup
    TODO: integrate a vetted phone-lookup provider here (for example a
    numbering-plan lookup such as Numverify, Twilio Lookup or AbstractAPI).
    The provider belongs in a new ``src/services/phone_lookup.py`` module
    exposing ``lookup(number) -> PhoneLookupResult``; wire it in
    ``app.build_application`` and register a real handler below.
    Configuration reserved for that provider (already read by config.py):
    ``PHONE_LOOKUP_API_KEY`` (see .env.example).

``/Id <username>`` — Instagram numeric user ID lookup
    TODO: integrate a documented user-ID provider here (or reuse
    ``InstagramService.client.user_id_from_username`` via an admin-only
    command if that fits the bot's policy). A provider client belongs in
    ``src/services/user_id_lookup.py``; wire it in
    ``app.build_application`` and register a real handler below.
    Configuration reserved for that provider (already read by config.py):
    ``INSTAGRAM_ID_API_KEY`` (see .env.example).

Until those integrations exist:

* the handlers make **no network calls** of any kind,
* no API endpoints, keys or results are invented,
* the commands stay out of the active registry and the help list, and
* ``ENABLE_NUM_COMMAND`` / ``ENABLE_ID_COMMAND`` are read by config.py but
  flipping them to ``true`` only surfaces the same placeholder — it does
  not fabricate a provider. Remove this module's guards when real
  integrations land.
"""

from __future__ import annotations

from typing import Dict, Optional

PAUSED_MESSAGES = {
    "num": (
        "The /Num command (phone-number lookup) is currently paused and not "
        "available. It will return once a vetted lookup provider is connected. "
        "Send /Help for the commands that are active today."
    ),
    "id": (
        "The /Id command (Instagram numeric ID lookup) is currently paused and "
        "not available. It will return once a vetted lookup provider is "
        "connected. Send /Help for the commands that are active today."
    ),
}


def paused_response(name: str) -> Optional[str]:
    """Return the paused message for *name*, or None if it is not paused."""
    return PAUSED_MESSAGES.get((name or "").lower())


def paused_names() -> Dict[str, str]:
    """Names and one-line reasons, for documentation/tests."""
    return {
        "Num": "paused — phone-number lookup provider not integrated (TODO)",
        "Id": "paused — Instagram numeric ID lookup provider not integrated (TODO)",
    }
