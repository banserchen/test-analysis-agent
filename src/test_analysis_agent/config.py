"""Configuration management for the test analysis agent."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # LLM Configuration
    llm_provider: str = Field(
        default="openai",
        description="LLM provider to use: 'openai' for OpenAI-compatible API, 'copilot' for GitHub Copilot SDK",
    )
    llm_api_key: str = Field(default="", description="API key for the LLM provider (or GitHub token for Copilot)")
    llm_base_url: str = Field(default="https://api.openai.com/v1", description="Base URL for the LLM API")
    llm_model: str = Field(default="gpt-4o", description="Model name to use for analysis")
    llm_max_tokens: int = Field(default=4096, description="Maximum tokens for LLM response")
    llm_temperature: float = Field(default=0.2, description="Temperature for LLM generation")

    # Jenkins Configuration
    jenkins_url: str = Field(default="", description="Jenkins server URL")
    jenkins_username: str = Field(default="", description="Jenkins username")
    jenkins_password: str = Field(default="", description="Jenkins password or API token")

    # Agent Configuration
    skills_dir: str = Field(default="", description="Additional directory to load skill plugins from")
    max_log_lines: int = Field(default=5000, description="Maximum log lines to send to LLM for analysis")
    report_language: str = Field(default="zh-CN", description="Language for generated reports (zh-CN, en)")

    # API Configuration
    api_host: str = Field(default="0.0.0.0", description="API server host")
    api_port: int = Field(default=9090, description="API server port")

    # Infrastructure inspection (optional)
    environments_api_url: str = Field(
        default="",
        description=(
            "Base URL of the test environments API (e.g. http://192.168.103.26). "
            "When set, the infrastructure_inspector skill will SSH into FMS/PP hosts "
            "to investigate container health when connection errors are detected."
        ),
    )

    # Feishu (Lark) integration (optional)
    feishu_app_id: str = Field(default="", description="Feishu app ID for creating cloud documents")
    feishu_app_secret: str = Field(default="", description="Feishu app secret for creating cloud documents")
    feishu_folder_token: str = Field(default="", description="Feishu folder token to store created documents")

    model_config = {"env_prefix": "TAA_", "env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


def get_settings() -> Settings:
    """Create and return application settings."""
    return Settings()
