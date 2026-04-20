"""LLM-based analysis engine.

Uses an LLM (via pluggable backends: OpenAI-compatible API or GitHub Copilot SDK)
to analyze pipeline failures, test case errors, and generate reports.
"""

from __future__ import annotations

import logging
from typing import Optional

from test_analysis_agent.config import Settings
from test_analysis_agent.llm_client import LLMClient, create_llm_client
from test_analysis_agent.models.schemas import (
    AnalyzedIssue,
    FailureCategory,
    PipelineStage,
    PipelineStageResult,
    Severity,
    TestCaseFailure,
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

_STAGE_ANALYSIS_PROMPT = """Analyze the following Jenkins pipeline stage failure.

Stage: {stage}
Status: {status}

Error Summary:
{error_summary}

Log Excerpt (last lines):
{log_excerpt}

{triggered_jobs_section}

Please provide:
1. A concise title for this issue
2. A detailed description of what went wrong
3. The failure category (one of: environment_error, dependency_error, deployment_error,
   configuration_error, network_error, permission_error, timeout_error, resource_error, unknown)
4. Severity (critical, high, medium, low)
5. Root cause analysis
6. Suggested fix
7. Whether a bug should be filed (true/false)
8. If filing a bug, a suggested bug summary

Respond in JSON format with these fields:
{{"title": "...", "description": "...", "category": "...", "severity": "...",
  "root_cause": "...", "suggestion": "...", "should_file_bug": true/false,
  "bug_summary": "..." or null}}
"""

_TEST_FAILURE_ANALYSIS_PROMPT = """Analyze the following test case failure.

Test: {test_name}
Class: {test_class}
Suite: {suite_name}
Status: {status}

Error Message:
{error_message}

Stack Trace:
{stack_trace}

{test_code_section}

Please provide:
1. A concise analysis of why this test failed
2. The failure category (one of: code_bug, test_case_failure, test_infrastructure_error,
   configuration_error, network_error, timeout_error, environment_error, unknown)
3. Root cause analysis
4. Suggested fix

Respond in JSON format:
{{"analysis": "...", "category": "...", "root_cause": "...", "suggestion": "..."}}
"""

_BATCH_ANALYSIS_PROMPT = """Analyze the following {count} test failures and group them by root cause.
Identify common patterns and categorize the issues.

Test Failures:
{failures_text}

For each unique issue group, provide:
1. A title for the issue
2. Description
3. Category
4. Severity
5. Affected test names
6. Root cause
7. Suggestion
8. Whether to file a bug

Respond in JSON as a list:
[{{"title": "...", "description": "...", "category": "...", "severity": "...",
   "affected_tests": ["..."], "root_cause": "...", "suggestion": "...",
   "should_file_bug": true/false, "bug_summary": "..." or null}}]
"""

_SUMMARY_PROMPT = """Based on the following analysis of a CD pipeline failure, write an executive summary.

Job: {job_name} #{build_number}
Failure Stage: {failure_stage}

Issues Found:
{issues_text}

Test Statistics:
- Total: {total_tests}, Passed: {passed_tests}, Failed: {failed_tests}, Broken: {broken_tests}

Please write:
1. A concise executive summary (2-3 paragraphs)
2. A list of bug filing recommendations (if any)

Respond in JSON:
{{"summary": "...", "bug_recommendations": ["..."]}}
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

    def analyze_stage_failure(self, stage_result: PipelineStageResult) -> Optional[AnalyzedIssue]:
        """Analyze a pipeline stage failure using LLM."""
        if stage_result.status != "fail":
            return None

        triggered_section = ""
        if stage_result.triggered_jobs:
            jobs_text = "\n".join(
                f"- {j.job_name} #{j.build_number}: {j.status}" for j in stage_result.triggered_jobs
            )
            triggered_section = f"Triggered Jobs:\n{jobs_text}"

        prompt = _STAGE_ANALYSIS_PROMPT.format(
            stage=stage_result.stage.value,
            status=stage_result.status,
            error_summary=_truncate(stage_result.error_summary or "N/A", 3000),
            log_excerpt=_truncate(stage_result.log_excerpt or "N/A", 3000),
            triggered_jobs_section=triggered_section,
        )

        result = self._call_llm(prompt)
        if not result:
            return None

        try:
            parsed = _parse_json_response(result)
            return AnalyzedIssue(
                title=parsed.get("title", "Unknown stage failure"),
                description=parsed.get("description", ""),
                category=_safe_category(parsed.get("category", "unknown")),
                severity=_safe_severity(parsed.get("severity", "medium")),
                stage=stage_result.stage,
                root_cause=parsed.get("root_cause"),
                suggestion=parsed.get("suggestion"),
                should_file_bug=parsed.get("should_file_bug", False),
                bug_summary=parsed.get("bug_summary"),
                log_evidence=_truncate(stage_result.error_summary, 500),
            )
        except Exception as exc:
            logger.warning("Failed to parse LLM response for stage analysis: %s", exc)
            return None

    def analyze_test_failure(
        self,
        failure: TestCaseFailure,
        test_code: Optional[str] = None,
    ) -> TestCaseFailure:
        """Analyze a single test case failure using LLM."""
        test_code_section = f"Related Test Code:\n```\n{_truncate(test_code, 2000)}\n```" if test_code else ""

        prompt = _TEST_FAILURE_ANALYSIS_PROMPT.format(
            test_name=failure.test_name,
            test_class=failure.test_class or "N/A",
            suite_name=failure.suite_name or "N/A",
            status=failure.status,
            error_message=_truncate(failure.error_message or "N/A", 2000),
            stack_trace=_truncate(failure.stack_trace or "N/A", 2000),
            test_code_section=test_code_section,
        )

        result = self._call_llm(prompt)
        if result:
            try:
                parsed = _parse_json_response(result)
                failure.ai_analysis = parsed.get("analysis", "")
                failure.failure_category = _safe_category(parsed.get("category", "unknown"))
                failure.related_code = test_code
            except Exception as exc:
                logger.warning("Failed to parse LLM response for test analysis: %s", exc)

        return failure

    def analyze_test_failures_batch(
        self,
        failures: list[TestCaseFailure],
    ) -> list[AnalyzedIssue]:
        """Analyze multiple test failures in batch, grouping by root cause."""
        if not failures:
            return []

        failures_text = ""
        for i, f in enumerate(failures, 1):
            failures_text += (
                f"\n--- Test {i} ---\n"
                f"Name: {f.test_name}\n"
                f"Status: {f.status}\n"
                f"Error: {_truncate(f.error_message or 'N/A', 500)}\n"
                f"Trace: {_truncate(f.stack_trace or 'N/A', 500)}\n"
            )

        prompt = _BATCH_ANALYSIS_PROMPT.format(
            count=len(failures),
            failures_text=_truncate(failures_text, 8000),
        )

        result = self._call_llm(prompt)
        if not result:
            return []

        try:
            parsed = _parse_json_response(result)
            if not isinstance(parsed, list):
                parsed = [parsed]

            issues: list[AnalyzedIssue] = []
            for item in parsed:
                issues.append(
                    AnalyzedIssue(
                        title=item.get("title", "Test failure group"),
                        description=item.get("description", ""),
                        category=_safe_category(item.get("category", "test_case_failure")),
                        severity=_safe_severity(item.get("severity", "medium")),
                        stage=PipelineStage.TESTING,
                        affected_tests=item.get("affected_tests", []),
                        root_cause=item.get("root_cause"),
                        suggestion=item.get("suggestion"),
                        should_file_bug=item.get("should_file_bug", False),
                        bug_summary=item.get("bug_summary"),
                    )
                )
            return issues
        except Exception as exc:
            logger.warning("Failed to parse LLM batch analysis: %s", exc)
            return []

    def generate_summary(
        self,
        job_name: str,
        build_number: int,
        failure_stage: PipelineStage,
        issues: list[AnalyzedIssue],
        total_tests: int = 0,
        passed_tests: int = 0,
        failed_tests: int = 0,
        broken_tests: int = 0,
    ) -> tuple[str, list[str]]:
        """Generate an executive summary and bug recommendations."""
        issues_text = "\n".join(
            f"- [{i.severity.value.upper()}] {i.title}: {i.description[:200]}" for i in issues
        )

        prompt = _SUMMARY_PROMPT.format(
            job_name=job_name,
            build_number=build_number,
            failure_stage=failure_stage.value,
            issues_text=issues_text or "No specific issues identified",
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            broken_tests=broken_tests,
        )

        result = self._call_llm(prompt)
        if result:
            try:
                parsed = _parse_json_response(result)
                return parsed.get("summary", ""), parsed.get("bug_recommendations", [])
            except Exception as exc:
                logger.warning("Failed to parse summary response: %s", exc)

        return "Analysis completed but summary generation failed.", []

    def _call_llm(self, user_prompt: str) -> Optional[str]:
        """Call the LLM API with error handling."""
        return self._client.chat_completion(
            system_prompt=_SYSTEM_PROMPT.format(language=self._language),
            user_prompt=user_prompt,
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )


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
