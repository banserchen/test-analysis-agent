"""Built-in skill: Log pattern analyzer.

Analyzes Jenkins logs for common error patterns and provides
quick categorization without requiring LLM calls.
"""

from __future__ import annotations

import re
from typing import Any

from test_analysis_agent.models.schemas import AnalysisReport, FailureCategory
from test_analysis_agent.skills.base import BaseSkill

_KNOWN_PATTERNS: list[tuple[re.Pattern[str], FailureCategory, str]] = [
    # Network/connectivity issues
    (
        re.compile(r"(?:Connection refused|ECONNREFUSED|connect ETIMEDOUT)", re.IGNORECASE),
        FailureCategory.NETWORK_ERROR,
        "Network connection failure detected",
    ),
    (
        re.compile(r"(?:DNS resolution failed|Name or service not known|getaddrinfo)", re.IGNORECASE),
        FailureCategory.NETWORK_ERROR,
        "DNS resolution failure detected",
    ),
    # Permission issues
    (
        re.compile(r"(?:Permission denied|Access denied|Forbidden|403 Forbidden)", re.IGNORECASE),
        FailureCategory.PERMISSION_ERROR,
        "Permission or access denial detected",
    ),
    # Resource issues
    (
        re.compile(r"(?:No space left on device|disk full|ENOSPC)", re.IGNORECASE),
        FailureCategory.RESOURCE_ERROR,
        "Disk space exhaustion detected",
    ),
    (
        re.compile(r"(?:Out of memory|OOM|Cannot allocate memory|MemoryError)", re.IGNORECASE),
        FailureCategory.RESOURCE_ERROR,
        "Memory exhaustion detected",
    ),
    # Timeout issues
    (
        re.compile(r"(?:timed? ?out|timeout|deadline exceeded)", re.IGNORECASE),
        FailureCategory.TIMEOUT_ERROR,
        "Timeout detected",
    ),
    # Dependency issues
    (
        re.compile(r"(?:ModuleNotFoundError|ImportError|No module named)", re.IGNORECASE),
        FailureCategory.DEPENDENCY_ERROR,
        "Python module import failure detected",
    ),
    (
        re.compile(r"(?:npm ERR!|Cannot find module|Module not found)", re.IGNORECASE),
        FailureCategory.DEPENDENCY_ERROR,
        "Node.js module resolution failure detected",
    ),
    (
        re.compile(r"(?:Could not resolve dependencies|Dependency .* not found)", re.IGNORECASE),
        FailureCategory.DEPENDENCY_ERROR,
        "Dependency resolution failure detected",
    ),
    # Configuration issues
    (
        re.compile(r"(?:configuration error|config .* not found|missing .* config)", re.IGNORECASE),
        FailureCategory.CONFIGURATION_ERROR,
        "Configuration error detected",
    ),
    # Deployment issues
    (
        re.compile(r"(?:ImagePullBackOff|ErrImagePull|CrashLoopBackOff)", re.IGNORECASE),
        FailureCategory.DEPLOYMENT_ERROR,
        "Kubernetes deployment issue detected",
    ),
    (
        re.compile(r"(?:helm .* failed|chart .* not found|release .* failed)", re.IGNORECASE),
        FailureCategory.DEPLOYMENT_ERROR,
        "Helm deployment failure detected",
    ),
]


class LogPatternAnalyzerSkill(BaseSkill):
    """Built-in skill that matches known error patterns in logs."""

    name = "log_pattern_analyzer"
    description = "Analyzes logs for known error patterns and provides quick categorization"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        """Handles any context that includes log text."""
        return bool(context.get("log_text"))

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Scan logs for known patterns."""
        log_text = context.get("log_text", "")
        findings: list[dict[str, str]] = []

        for pattern, category, description in _KNOWN_PATTERNS:
            matches = pattern.findall(log_text)
            if matches:
                findings.append(
                    {
                        "category": category.value,
                        "description": description,
                        "match_count": str(len(matches)),
                        "sample_match": matches[0] if matches else "",
                    }
                )

        return {
            "pattern_findings": findings,
            "has_known_patterns": len(findings) > 0,
        }

    def post_process(self, report: AnalysisReport) -> AnalysisReport:
        """No post-processing needed for this skill."""
        return report
