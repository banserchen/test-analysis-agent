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
    # Permission issues — exclude pip/package-manager WARNING lines which report
    # cache/ownership issues that are informational, not actual failures.
    (
        re.compile(r"(?:Permission denied|Access denied|403 Forbidden)", re.IGNORECASE),
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
    # Timeout issues. We deliberately require an explicit timeout qualifier
    # to avoid matching benign git arguments like ``# timeout=10``.
    (
        re.compile(r"(?:timed\s*out|\bdeadline\s+exceeded\b|\btimeout\s+(?:of|after|expired|exceeded|reached)\b)", re.IGNORECASE),
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

# Lines matching these patterns are noise/informational and are excluded from analysis.
_NOISE_LINE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*WARNING:", re.IGNORECASE),           # pip/tool warnings
    re.compile(r"Requirement already satisfied", re.IGNORECASE),  # pip install output
    re.compile(r"^\s*#\s*timeout=\d+", re.IGNORECASE),    # git/jenkins timeout params
    re.compile(r"\[notice\]", re.IGNORECASE),              # pip notices
]


def _is_noise_line(line: str) -> bool:
    return any(p.search(line) for p in _NOISE_LINE_PATTERNS)


class LogPatternAnalyzerSkill(BaseSkill):
    """Built-in skill that matches known error patterns in logs."""

    name = "log_pattern_analyzer"
    description = "Analyzes logs for known error patterns and provides quick categorization"
    version = "1.0.0"

    def can_handle(self, context: dict[str, Any]) -> bool:
        """Handles any context that includes log text."""
        return bool(context.get("log_text"))

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Scan logs for known patterns, filtering out noise lines."""
        log_text = context.get("log_text", "")
        lines = log_text.splitlines()

        # Exclude noise lines before matching
        meaningful_lines = [ln for ln in lines if not _is_noise_line(ln)]

        findings: list[dict[str, str]] = []

        for pattern, category, description in _KNOWN_PATTERNS:
            matching_lines = [ln.strip() for ln in meaningful_lines if pattern.search(ln)]
            if not matching_lines:
                continue

            # Show up to 3 matching lines as log evidence
            evidence_lines = matching_lines[:3]
            if len(matching_lines) > 3:
                evidence_lines.append(f"... and {len(matching_lines) - 3} more")
            evidence = "\n".join(evidence_lines)

            findings.append(
                {
                    "category": category.value,
                    "description": description,
                    "match_count": str(len(matching_lines)),
                    "sample_match": matching_lines[0],
                    "log_evidence": evidence,
                }
            )

        return {
            "pattern_findings": findings,
            "has_known_patterns": len(findings) > 0,
        }

    def post_process(self, report: AnalysisReport) -> AnalysisReport:
        """No post-processing needed for this skill."""
        return report
