"""Tests for the LLM client abstraction layer."""

import asyncio
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

from test_analysis_agent.config import Settings
from test_analysis_agent.llm_client import CopilotClient, LLMClient, OpenAIClient, create_llm_client


class TestOpenAIClient:
    """Tests for the OpenAI LLM client."""

    def test_implements_interface(self):
        client = OpenAIClient(api_key="test-key", base_url="https://api.openai.com/v1")
        assert isinstance(client, LLMClient)

    @patch("openai.OpenAI")
    def test_chat_completion_success(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "test response"
        mock_client.chat.completions.create.return_value = mock_response

        client = OpenAIClient(api_key="test-key", base_url="https://api.openai.com/v1")
        result = client.chat_completion(
            system_prompt="You are helpful.",
            user_prompt="Hello",
            model="gpt-4o",
        )

        assert result == "test response"
        mock_client.chat.completions.create.assert_called_once_with(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hello"},
            ],
            max_tokens=4096,
            temperature=0.2,
        )

    @patch("openai.OpenAI")
    def test_chat_completion_with_custom_params(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "response"
        mock_client.chat.completions.create.return_value = mock_response

        client = OpenAIClient(api_key="key", base_url="http://localhost:8000")
        result = client.chat_completion(
            system_prompt="sys",
            user_prompt="user",
            model="custom-model",
            max_tokens=1024,
            temperature=0.8,
        )

        assert result == "response"
        mock_client.chat.completions.create.assert_called_once_with(
            model="custom-model",
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "user"},
            ],
            max_tokens=1024,
            temperature=0.8,
        )

    @patch("openai.OpenAI")
    def test_chat_completion_returns_none_on_error(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = Exception("API error")

        client = OpenAIClient(api_key="key", base_url="https://api.openai.com/v1")
        result = client.chat_completion(
            system_prompt="sys",
            user_prompt="user",
            model="gpt-4o",
        )

        assert result is None

    @patch("openai.OpenAI")
    def test_empty_api_key_defaults_to_not_set(self, mock_openai_cls):
        OpenAIClient(api_key="", base_url="https://api.openai.com/v1")
        mock_openai_cls.assert_called_once_with(api_key="not-set", base_url="https://api.openai.com/v1")


class TestCopilotClient:
    """Tests for the Copilot LLM client."""

    def test_implements_interface(self):
        client = CopilotClient(github_token="test-token")
        assert isinstance(client, LLMClient)

    def test_stores_github_token(self):
        client = CopilotClient(github_token="ghp_test123")
        assert client._github_token == "ghp_test123"

    def test_none_token(self):
        client = CopilotClient(github_token=None)
        assert client._github_token is None

    @pytest.mark.integration
    def test_chat_completion_with_configured_copilot(self):
        settings = Settings()

        if settings.llm_provider != "copilot":
            pytest.skip("Copilot provider is not configured")

        if importlib.util.find_spec("copilot") is None:
            pytest.skip("github-copilot-sdk is not installed")

        from copilot import CopilotClient as SDKCopilotClient
        from copilot import SubprocessConfig

        async def list_model_ids() -> list[str]:
            config = SubprocessConfig(github_token=settings.llm_api_key or None)
            async with SDKCopilotClient(config=config) as sdk_client:
                return [model.id for model in await sdk_client.list_models()]

        available_models = asyncio.run(list_model_ids())

        assert available_models, "Copilot authentication succeeded but no models were returned"
        assert settings.llm_model in available_models, (
            f"Configured model '{settings.llm_model}' is not available. "
            f"Available models: {', '.join(available_models)}"
        )

        client = CopilotClient(github_token=settings.llm_api_key or None)

        response = client.chat_completion(
            system_prompt="Reply with exactly: COPILOT_OK",
            user_prompt="Return the health check token only.",
            model=settings.llm_model,
            max_tokens=32,
            temperature=0,
        )

        assert response is not None
        assert "COPILOT_OK" in response


class TestCreateLLMClient:
    """Tests for the factory function."""

    def test_create_openai_client(self):
        settings = Settings(
            llm_provider="openai",
            llm_api_key="test-key",
            llm_base_url="https://api.openai.com/v1",
        )
        client = create_llm_client(settings)
        assert isinstance(client, OpenAIClient)

    def test_create_copilot_client(self):
        settings = Settings(
            llm_provider="copilot",
            llm_api_key="ghp_test",
        )
        client = create_llm_client(settings)
        assert isinstance(client, CopilotClient)

    def test_create_copilot_client_empty_key(self):
        settings = Settings(
            llm_provider="copilot",
            llm_api_key="",
        )
        client = create_llm_client(settings)
        assert isinstance(client, CopilotClient)
        assert client._github_token is None

    def test_unsupported_provider_raises(self):
        settings = Settings(llm_provider="unsupported")
        with pytest.raises(ValueError, match="Unsupported LLM provider: 'unsupported'"):
            create_llm_client(settings)

    def test_default_provider_is_openai(self):
        settings = Settings()
        assert settings.llm_provider == "openai"
        client = create_llm_client(settings)
        assert isinstance(client, OpenAIClient)
