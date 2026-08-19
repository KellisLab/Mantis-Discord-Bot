"""API client for the Mantis Oracle chat completions endpoint."""

from __future__ import annotations

import aiohttp

from config import ORACLE_API_KEY, ORACLE_API_URL, ORACLE_REQUEST_TIMEOUT

from .network import retry_with_exponential_backoff


class OracleAPIError(Exception):
    """Raised when the Oracle API cannot produce a response."""


async def ask_oracle(messages: list[dict[str, str]]) -> str:
    """Send a conversation to the Oracle and return its text response.

    Args:
        messages: Chat messages in {"role": ..., "content": ...} form,
            oldest first. The API injects its own system prompt.
    """
    headers = {
        "x-api-key": ORACLE_API_KEY or "",
    }
    payload = {"messages": messages}
    timeout = aiohttp.ClientTimeout(total=ORACLE_REQUEST_TIMEOUT)

    async def call():
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(
                ORACLE_API_URL, json=payload, headers=headers
            ) as response,
        ):
            if response.status != 200:
                body = await response.text()
                if response.status in (429, 500, 502, 503):
                    response.raise_for_status()
                raise OracleAPIError(
                    f"Oracle API returned {response.status}: {body}"
                )
            return await response.json()

    success, result, error = await retry_with_exponential_backoff(
        call, max_retries=3, base_delay=1.0
    )
    if not success:
        raise OracleAPIError(error)

    return _extract_reply(result)


def _extract_reply(result: dict) -> str:
    completion = result.get("completion")
    if completion:
        return completion.strip()
    raise OracleAPIError(f"Oracle API response missing completion: {result!r}")
