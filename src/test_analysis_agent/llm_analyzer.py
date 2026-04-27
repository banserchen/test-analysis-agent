"""LLM-based analysis engine.

Uses an LLM (via pluggable backends: OpenAI-compatible API or GitHub Copilot SDK)
to analyze pipeline failures, test case errors, and generate reports.
"""

from __future__ import annotations

import logging
from typing import Optional

from test_analysis_agent.config import Settings
from test_analysis_agent.llm_client import LLMClient, LLMUsage, create_llm_client
from test_analysis_agent.models.schemas import (
    FailureCategory,
    PipelineStage,
    Severity,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an expert CI/CD pipeline failure analyst. Your job is to analyze
pipeline build logs, test failures, and deployment issues to identify root causes and provide
actionable recommendations.

You must respond in {language}. Be precise and technical. When analyzing failures:
1. Identify the root cause clearly
2. Classify the failure category
3. Suggest specific fixes
4. Indicate whether a bug should be filed

When analyzing test failures, consider:
- Whether the failure is a product bug, test infrastructure issue, or environment problem
- The error message and stack trace
- Any related test code provided
- Patterns across multiple failures that might indicate the same root cause
"""


class LLMAnalyzer:
    """LLM-powered failure analysis engine."""

    def __init__(self, settings: Settings, client: LLMClient | None = None):
        self._client = client or create_llm_client(settings)
        self._model = settings.llm_model
        self._max_tokens = settings.llm_max_tokens
        self._temperature = settings.llm_temperature
        self._max_log_lines = settings.max_log_lines
        self._language = "Chinese" if settings.report_language.startswith("zh") else "English"
        self._usage = LLMUsage()  # cumulative token usage for this analyzer instance
        self._llm_calls = 0  # number of LLM API calls made

    def reset_usage(self) -> None:
        """Reset cumulative token usage counter (call before each analysis run)."""
        self._usage = LLMUsage()
        self._llm_calls = 0

    def get_total_usage(self) -> tuple[LLMUsage, int]:
        """Return the accumulated token usage and call count since last reset."""
        return self._usage, self._llm_calls

    def call_llm(self, prompt: str) -> Optional[str]:
        """Public LLM call interface for skills. Tracks token usage identically to internal calls."""
        return self._call_llm(prompt)

    def _call_llm(self, user_prompt: str) -> Optional[str]:
        """Call the LLM API with error handling, accumulating token usage."""
        response = self._client.chat_completion(
            system_prompt=_SYSTEM_PROMPT.format(language=self._language),
            user_prompt=user_prompt,
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        self._llm_calls += 1
        self._usage = self._usage + response.usage
        return response.text


def _truncate(text: Optional[str], max_chars: int) -> str:
    """Truncate text to max characters."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _parse_json_response(text: str) -> dict | list:
    """Parse JSON from LLM response, handling markdown code blocks."""
    import json

    cleaned = text.strip()
    # Remove markdown code blocks if present
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last lines (``` markers)
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines)

    return json.loads(cleaned)


def _safe_category(value: str) -> FailureCategory:
    """Safely convert a string to FailureCategory."""
    try:
        return FailureCategory(value)
    except ValueError:
        return FailureCategory.UNKNOWN


def _safe_severity(value: str) -> Severity:
    """Safely convert a string to Severity."""
    try:
        return Severity(value)
    except ValueError:
        return Severity.MEDIUM
