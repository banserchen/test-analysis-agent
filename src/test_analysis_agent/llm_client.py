"""LLM client abstraction layer.

Provides a unified interface for different LLM backends:
- OpenAI-compatible API (default)
- GitHub Copilot SDK

This allows the analysis engine to work with either backend
transparently, selected via configuration.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from test_analysis_agent.config import Settings

logger = logging.getLogger(__name__)


def _deny_permission_requests(request: object, invocation: dict[str, str]) -> object:
    """Deny all Copilot permission requests across SDK versions."""

    del request, invocation

    from copilot.session import PermissionRequestResult

    return PermissionRequestResult(kind="denied-by-rules")


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    def chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str | None:
        """Send a chat completion request and return the response text.

        Args:
            system_prompt: The system message.
            user_prompt: The user message.
            model: Model name to use.
            max_tokens: Maximum tokens for the response.
            temperature: Sampling temperature.

        Returns:
            The assistant's response text, or None on failure.
        """


class OpenAIClient(LLMClient):
    """LLM client using the OpenAI-compatible API."""

    def __init__(self, api_key: str, base_url: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key or "not-set",
            base_url=base_url,
        )

    def chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str | None:
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as exc:
            logger.error("OpenAI API call failed: %s", exc)
            return None


class CopilotClient(LLMClient):
    """LLM client using the GitHub Copilot SDK.

    The Copilot SDK communicates with the Copilot CLI via JSON-RPC.
    It requires either a GitHub token or a logged-in Copilot CLI session.

    This client wraps the async Copilot SDK into a synchronous interface
    compatible with the rest of the analysis engine.
    """

    def __init__(self, github_token: str | None = None) -> None:
        self._github_token = github_token

    def chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str | None:
        try:
            return _run_async(
                self._async_chat_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            )
        except Exception as exc:
            logger.error("Copilot SDK call failed: %s", exc)
            return None

    async def _async_chat_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str | None:
        from copilot import CopilotClient as _CopilotClient
        from copilot import SubprocessConfig
        from copilot.session import SessionEventType

        config = SubprocessConfig(github_token=self._github_token)

        async with _CopilotClient(config=config) as client:
            system_message = {"mode": "replace", "content": system_prompt}

            async with await client.create_session(
                on_permission_request=_deny_permission_requests,
                model=model,
                system_message=system_message,
            ) as session:
                event = await session.send_and_wait(user_prompt, timeout=120.0)

                if event is None:
                    logger.warning("Copilot session returned no response event")
                    return None

                if event.type == SessionEventType.ASSISTANT_MESSAGE:
                    return event.data.content

                # If send_and_wait returned a non-message event (e.g. idle),
                # retrieve messages from history.
                messages = await session.get_messages()
                for msg in reversed(messages):
                    role = getattr(msg, "role", None)
                    content = getattr(msg, "content", None)
                    if role == "assistant" and content:
                        return content

                logger.warning("No assistant message found in Copilot session history")
                return None


def create_llm_client(settings: Settings) -> LLMClient:
    """Create an LLM client based on application settings.

    Args:
        settings: Application settings specifying the LLM provider.

    Returns:
        An LLMClient instance for the configured provider.

    Raises:
        ValueError: If the configured provider is not supported.
    """
    provider = settings.llm_provider

    if provider == "openai":
        return OpenAIClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    elif provider == "copilot":
        return CopilotClient(
            github_token=settings.llm_api_key or None,
        )
    else:
        raise ValueError(
            f"Unsupported LLM provider: '{provider}'. "
            f"Supported providers: 'openai', 'copilot'."
        )


def _run_async(coro: Coroutine[None, None, str | None]) -> str | None:
    """Run an async coroutine from synchronous code.

    Handles the case where an event loop may already be running
    (e.g., inside FastAPI or Jupyter).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're inside an already-running event loop (e.g. FastAPI).
        # Create a new thread to run the coroutine.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=180)
    else:
        return asyncio.run(coro)
