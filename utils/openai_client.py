"""Lazily constructed OpenAI clients.

The OpenAI SDK validates the API key in its constructor, so building a client at
module import time turns a missing or rotated ``OPENAI_API_KEY`` into a failed
extension load. Cogs that only *use* the client should not fail to register
because of it, so the client is created on first use instead.
"""

from __future__ import annotations

import openai

from config import OPENAI_API_KEY

_async_client: openai.AsyncOpenAI | None = None


class OpenAINotConfiguredError(RuntimeError):
    """Raised when an OpenAI-backed feature runs without an API key."""


def get_async_openai_client() -> openai.AsyncOpenAI:
    """Return the shared async OpenAI client, creating it on first use.

    Raises:
        OpenAINotConfiguredError: if ``OPENAI_API_KEY`` is not configured.
    """

    global _async_client
    if _async_client is None:
        if not OPENAI_API_KEY:
            raise OpenAINotConfiguredError(
                "OPENAI_API_KEY is not set, so this AI feature is unavailable. "
                "Set it in the environment and redeploy."
            )
        _async_client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _async_client
